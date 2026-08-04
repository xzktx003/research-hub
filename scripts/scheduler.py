#!/usr/bin/env python3
"""Small scheduler/runner entrypoint for Research Hub executable jobs."""

# Direct execution bootstraps the project root before local-package imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from research_hub.database import Database
from research_hub.models import DiscoveryRunCreate
from research_hub.observability import (
    METRICS,
    collect_job_metrics,
    configure_json_logging,
    current_trace_context,
    log_event,
    record_job_result,
    trace_context,
)
from research_hub.repository import Repository
from research_hub.runtime_config import load_runtime_config
from research_hub.services import ResearchJobService


LOGGER = logging.getLogger("research_hub.scheduler")


def _daily_window(selected: datetime, lookback_days: int) -> tuple[datetime, datetime]:
    day_start = selected.replace(hour=0, minute=0, second=0, microsecond=0)
    normalized_lookback = max(1, lookback_days)
    return day_start - timedelta(days=normalized_lookback - 1), day_start + timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    runtime_schedule = load_runtime_config()["schedule"]
    parser = argparse.ArgumentParser(description="Run Research Hub discovery and workflow jobs")
    parser.add_argument("--json-logs", action="store_true", help="Emit structured JSON logs to stderr")
    sub = parser.add_subparsers(dest="command", required=True)

    run_discovery = sub.add_parser("run-discovery", help="Execute a discovery_run by id")
    run_discovery.add_argument("run_id")

    run_job = sub.add_parser("run-job", help="Execute one queued job by id")
    run_job.add_argument("job_id")

    run_queued = sub.add_parser("run-queued", help="Execute queued jobs once")
    run_queued.add_argument("--limit", type=int, default=10)

    poll_running = sub.add_parser("poll-running", help="Poll running external jobs once")
    poll_running.add_argument("--limit", type=int, default=10)

    worker = sub.add_parser("worker", help="Continuously execute and poll workflow jobs")
    worker.add_argument("--interval", type=int, default=30)
    worker.add_argument("--limit", type=int, default=10)
    worker.add_argument("--daily-hour", type=int)
    worker.add_argument("--daily-max-results", type=int)
    worker.add_argument(
        "--daily-lookback-days",
        type=int,
    )
    worker.add_argument("--timezone")
    worker.add_argument("--disable-daily", action="store_true")

    daily = sub.add_parser("daily", help="Create and execute one daily arXiv discovery run")
    daily.add_argument("--date", help="UTC date (YYYY-MM-DD); defaults to today")
    daily.add_argument("--topic", action="append", default=[], help="Topic id; repeatable")
    daily.add_argument("--max-results", type=int, default=runtime_schedule["max_results"], help="Maximum results per topic")
    daily.add_argument(
        "--lookback-days",
        type=int,
        default=runtime_schedule["lookback_days"],
        help="Inclusive publication lookback window; defaults to 7 days",
    )

    args = parser.parse_args(argv)
    if args.json_logs:
        configure_json_logging(logging.getLogger())
    database = Database(get_settings().database_path)
    database.initialize()
    with database.connect() as conn:
        service = ResearchJobService(conn)
        with trace_context(job_id=getattr(args, "job_id", None)) as trace:
            started_at = time.monotonic()
            log_event(LOGGER, "scheduler.command.started", command=args.command, **trace)
            if args.command == "run-discovery":
                payload = service.run_discovery_run(args.run_id)
                record_job_result(payload)
            elif args.command == "run-job":
                payload = service.run_job(args.job_id)
                record_job_result(payload)
            elif args.command == "run-queued":
                items = service.run_queued_jobs_once(limit=args.limit)
                for item in items:
                    record_job_result(item)
                payload = {"items": items}
            elif args.command == "poll-running":
                items = service.poll_running_jobs_once(limit=args.limit)
                for item in items:
                    record_job_result(item)
                payload = {"items": items}
            elif args.command == "worker":
                while True:
                    with trace_context():
                        active_schedule = load_runtime_config()["schedule"]
                        service = ResearchJobService(conn)
                        scheduler_timezone = ZoneInfo(args.timezone or active_schedule["timezone"])
                        daily_hour = (
                            args.daily_hour
                            if args.daily_hour is not None
                            else active_schedule["daily_hour"]
                        )
                        daily_max_results = (
                            args.daily_max_results
                            if args.daily_max_results is not None
                            else active_schedule["max_results"]
                        )
                        daily_lookback_days = (
                            args.daily_lookback_days
                            if args.daily_lookback_days is not None
                            else active_schedule["lookback_days"]
                        )
                        now = datetime.now(scheduler_timezone)
                        daily_payload = None
                        if active_schedule["enabled"] and not args.disable_daily and now.hour >= daily_hour:
                            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            window_start, window_end = _daily_window(
                                day_start, daily_lookback_days
                            )
                            daily_run = Repository(conn).create_discovery_run(
                                DiscoveryRunCreate(
                                    source="multi",
                                    window_start=window_start,
                                    window_end=window_end,
                                    max_results=daily_max_results,
                                    metadata={
                                        "trigger": "embedded_worker",
                                        "hit_date": day_start.date().isoformat(),
                                        "lookback_days": max(1, daily_lookback_days),
                                        "auto_process": active_schedule["auto_process"],
                                    },
                                ),
                                idempotency_key=(
                                    f"daily:v2:{day_start.date().isoformat()}:all:"
                                    f"{daily_max_results}:lookback:{max(1, daily_lookback_days)}"
                                ),
                            )
                            if daily_run.status in {"queued", "retryable_failed"}:
                                daily_payload = service.run_discovery_run(daily_run.id)
                                record_job_result(daily_payload)
                        queued = service.run_queued_jobs_once(limit=args.limit)
                        running = service.poll_running_jobs_once(limit=args.limit)
                        for item in [*(queued or []), *(running or [])]:
                            record_job_result(item)
                        conn.commit()
                        metrics = collect_job_metrics(conn, registry=METRICS)
                        payload = {
                            "trace": current_trace_context(),
                            "daily": daily_payload,
                            "queued": queued,
                            "running": running,
                            "metrics": metrics,
                        }
                        log_event(
                            LOGGER,
                            "scheduler.worker.iteration",
                            queued_count=len(queued),
                            running_count=len(running),
                        )
                        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
                        time.sleep(max(5, args.interval))
            elif args.command == "daily":
                selected = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc) if args.date else datetime.now(timezone.utc)
                hit_date = selected.date().isoformat()
                window_start, window_end = _daily_window(selected, args.lookback_days)
                run = Repository(conn).create_discovery_run(
                    DiscoveryRunCreate(
                        source="multi",
                        window_start=window_start,
                        window_end=window_end,
                        topics=args.topic,
                        max_results=args.max_results,
                        metadata={
                            "trigger": "daily_scheduler",
                            "hit_date": hit_date,
                            "lookback_days": max(1, args.lookback_days),
                            "auto_process": runtime_schedule["auto_process"],
                        },
                    ),
                    idempotency_key=(
                        f"daily:v2:{hit_date}:{','.join(sorted(args.topic)) or 'all'}:"
                        f"{args.max_results}:lookback:{max(1, args.lookback_days)}"
                    ),
                )
                payload = service.run_discovery_run(run.id)
                record_job_result(payload)
            else:
                raise AssertionError(args.command)
            duration_ms = METRICS.observe_duration_ms("research_hub_scheduler_command", started_at, command=args.command)
            collect_job_metrics(conn, registry=METRICS)
            payload = {
                **payload,
                "trace": current_trace_context(),
                "metrics": METRICS.snapshot(),
            }
            log_event(
                LOGGER,
                "scheduler.command.finished",
                command=args.command,
                duration_ms=round(duration_ms, 3),
            )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
