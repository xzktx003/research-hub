"""Observability helpers for Research Hub request and job execution paths.

The module is intentionally dependency-free so it can be used by FastAPI
middleware, CLI scripts, cron jobs, and tests without changing deployment
requirements.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import sqlite3
import time
from typing import Any, Protocol, TextIO
from urllib import request as urllib_request
from uuid import uuid4

from .database import dumps, loads
from .models import JobRetryRequest
from .repository import Repository


TRACE_ID_HEADER = "X-Trace-Id"
REQUEST_ID_HEADER = "X-Request-Id"
FAILED_JOB_STATUSES = ("retryable_failed", "terminal_failed")

_trace_id: ContextVar[str | None] = ContextVar("research_hub_trace_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("research_hub_request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("research_hub_job_id", default=None)


def new_trace_id(prefix: str = "trc") -> str:
    """Create a compact trace identifier for request and background job logs."""

    return f"{prefix}_{uuid4().hex}"


def current_trace_context() -> dict[str, str]:
    """Return the active trace fields, excluding unset values."""

    values = {
        "trace_id": _trace_id.get(),
        "request_id": _request_id.get(),
        "job_id": _job_id.get(),
    }
    return {key: value for key, value in values.items() if value}


@contextmanager
def trace_context(
    *,
    trace_id: str | None = None,
    request_id: str | None = None,
    job_id: str | None = None,
):
    """Temporarily bind trace identifiers to the current context."""

    trace_token = _trace_id.set(trace_id or _trace_id.get() or new_trace_id())
    request_token = _request_id.set(request_id or _request_id.get())
    job_token = _job_id.set(job_id or _job_id.get())
    try:
        yield current_trace_context()
    finally:
        _job_id.reset(job_token)
        _request_id.reset(request_token)
        _trace_id.reset(trace_token)


def trace_from_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Normalize incoming HTTP trace headers into internal context field names."""

    lower = {str(key).lower(): str(value) for key, value in headers.items() if value}
    trace = lower.get(TRACE_ID_HEADER.lower()) or lower.get("traceparent")
    request_id = lower.get(REQUEST_ID_HEADER.lower())
    return {
        "trace_id": trace or new_trace_id(),
        "request_id": request_id or new_trace_id("req"),
    }


class JsonLogFormatter(logging.Formatter):
    """Format log records as one JSON object per line with trace fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(current_trace_context())
        for key in ("event", "component", "job_id", "kind", "status", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        extra = getattr(record, "fields", None)
        if isinstance(extra, Mapping):
            payload.update(_json_safe_dict(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_json_logging(
    logger: logging.Logger | None = None,
    *,
    stream: TextIO | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Install a structured JSON stream handler on a logger."""

    target = logger or logging.getLogger()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    target.handlers = [handler]
    target.setLevel(level)
    return target


def log_event(
    logger: logging.Logger,
    event: str,
    message: str = "",
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a structured event with arbitrary JSON-safe fields."""

    logger.log(level, message or event, extra={"event": event, "fields": fields})


class MetricsRegistry:
    """Simple in-process counter/gauge registry with JSON and text renderers."""

    def __init__(self) -> None:
        self._counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def increment(self, name: str, amount: float = 1.0, **labels: str) -> None:
        self._counters[(name, _labels(labels))] += amount

    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._gauges[(name, _labels(labels))] = value

    def observe_duration_ms(self, name: str, started_at: float, **labels: str) -> float:
        duration = (time.monotonic() - started_at) * 1000.0
        self.increment(f"{name}_count", 1, **labels)
        self.increment(f"{name}_ms_total", duration, **labels)
        self.gauge(f"{name}_ms_last", duration, **labels)
        return duration

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "counters": _metric_rows(self._counters),
            "gauges": _metric_rows(self._gauges),
        }

    def render_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)

    def render_text(self) -> str:
        lines: list[str] = []
        for group in ("counters", "gauges"):
            for row in self.snapshot()[group]:
                lines.append(_metric_line(row["name"], float(row["value"]), row["labels"]))
        return "\n".join(lines) + ("\n" if lines else "")


METRICS = MetricsRegistry()


def record_job_result(result: Mapping[str, Any], *, registry: MetricsRegistry = METRICS) -> None:
    """Record a job result payload returned by ResearchJobService."""

    job_id = str(result.get("job_id") or "")
    status = str(result.get("status") or "unknown")
    kind = str(result.get("kind") or "")
    if not kind and job_id:
        kind = str(result.get("result", {}).get("kind") or "unknown") if isinstance(result.get("result"), Mapping) else "unknown"
    registry.increment("research_hub_jobs_total", status=status, kind=kind or "unknown")


def collect_job_metrics(conn: sqlite3.Connection, *, registry: MetricsRegistry = METRICS) -> dict[str, Any]:
    """Render database-backed job gauges into the in-process registry."""

    jobs = conn.execute("SELECT kind, status, result_json FROM job").fetchall()
    status_rows = conn.execute("SELECT status, COUNT(*) AS n FROM job GROUP BY status").fetchall()
    for row in status_rows:
        registry.gauge("research_hub_jobs_by_status", float(row["n"]), status=str(row["status"]))
    kind_rows = conn.execute("SELECT kind, status, COUNT(*) AS n FROM job GROUP BY kind, status").fetchall()
    for row in kind_rows:
        registry.gauge(
            "research_hub_jobs_by_kind_status",
            float(row["n"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
        )
    kind_totals: defaultdict[str, int] = defaultdict(int)
    kind_successes: defaultdict[str, int] = defaultdict(int)
    model_tokens: defaultdict[str, float] = defaultdict(float)
    source_totals: defaultdict[str, int] = defaultdict(int)
    source_successes: defaultdict[str, int] = defaultdict(int)
    for row in jobs:
        kind = str(row["kind"])
        status = str(row["status"])
        kind_totals[kind] += 1
        if status in {"succeeded", "partial_succeeded"}:
            kind_successes[kind] += 1
        result = loads(row["result_json"], {})
        model_tokens[kind] += _model_token_count(result)
        if kind == "discover":
            for outcome in result.get("source_outcomes") or []:
                if not isinstance(outcome, Mapping):
                    continue
                source = str(outcome.get("source") or "unknown")
                source_totals[source] += 1
                if outcome.get("status") == "ok":
                    source_successes[source] += 1
    for kind, total in kind_totals.items():
        ratio = kind_successes[kind] / total
        registry.gauge("research_hub_job_success_ratio", ratio, kind=kind)
        if model_tokens[kind]:
            registry.gauge("research_hub_model_tokens_total", model_tokens[kind], kind=kind)
    if kind_totals.get("download"):
        registry.gauge(
            "research_hub_download_success_ratio",
            kind_successes["download"] / kind_totals["download"],
        )
    if kind_totals.get("analyze"):
        registry.gauge(
            "research_hub_report_success_ratio",
            kind_successes["analyze"] / kind_totals["analyze"],
        )
    for source, total in source_totals.items():
        registry.gauge(
            "research_hub_source_success_ratio",
            source_successes[source] / total,
            source=source,
        )
        registry.gauge("research_hub_source_attempts_total", float(total), source=source)

    attempt_rows = conn.execute(
        """
        SELECT j.kind, a.started_at, a.completed_at
        FROM job_attempt a
        JOIN job j ON j.id = a.job_id
        WHERE a.completed_at IS NOT NULL
        """
    ).fetchall()
    durations: defaultdict[str, list[float]] = defaultdict(list)
    for row in attempt_rows:
        duration_ms = _duration_ms(row["started_at"], row["completed_at"])
        if duration_ms is not None:
            durations[str(row["kind"])].append(duration_ms)
    for kind, values in durations.items():
        registry.gauge(
            "research_hub_job_duration_ms_avg",
            sum(values) / len(values),
            kind=kind,
        )
    if durations.get("parse"):
        registry.gauge(
            "research_hub_parse_duration_ms_avg",
            sum(durations["parse"]) / len(durations["parse"]),
        )

    candidate_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM invention_candidate GROUP BY status"
    ).fetchall()
    candidate_total = sum(int(row["n"]) for row in candidate_rows)
    candidate_passed = sum(
        int(row["n"])
        for row in candidate_rows
        if str(row["status"]) in {"approved", "drafting", "self_checked", "exported"}
    )
    if candidate_total:
        registry.gauge(
            "research_hub_patent_candidate_pass_ratio",
            candidate_passed / candidate_total,
        )
    failed = list_dead_letter_jobs(conn, limit=1000)
    registry.gauge("research_hub_dead_letter_jobs", float(len(failed)))
    return registry.snapshot()


def _model_token_count(result: Mapping[str, Any]) -> float:
    candidates: list[Mapping[str, Any]] = [result]
    response = result.get("response")
    if isinstance(response, Mapping):
        candidates.append(response)
        data = response.get("data")
        if isinstance(data, Mapping):
            candidates.append(data)
    for candidate in candidates:
        total = candidate.get("total_tokens")
        usage = candidate.get("usage")
        if isinstance(usage, Mapping):
            total = usage.get("total_tokens") or total
            if total is None:
                total = float(usage.get("prompt_tokens") or 0) + float(
                    usage.get("completion_tokens") or 0
                )
        elif total is None:
            continue
        try:
            return float(total or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _duration_ms(started_at: Any, completed_at: Any) -> float | None:
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return max(0.0, (end - start).total_seconds() * 1000.0)


@dataclass(frozen=True)
class DeadLetterJob:
    id: str
    kind: str
    status: str
    target_type: str
    target_id: str
    attempt_count: int
    error: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_dead_letter_jobs(
    conn: sqlite3.Connection,
    *,
    statuses: Iterable[str] = FAILED_JOB_STATUSES,
    kind: str | None = None,
    limit: int = 100,
) -> list[DeadLetterJob]:
    """Return failed jobs that require operator attention or manual replay."""

    selected = tuple(dict.fromkeys(statuses))
    if not selected:
        return []
    clauses = [f"j.status IN ({','.join('?' for _ in selected)})"]
    params: list[Any] = list(selected)
    if kind:
        clauses.append("j.kind = ?")
        params.append(kind)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            j.id, j.kind, j.status, j.target_type, j.target_id,
            j.error_json, j.updated_at, COUNT(a.id) AS attempt_count
        FROM job j
        LEFT JOIN job_attempt a ON a.job_id = j.id
        WHERE {' AND '.join(clauses)}
        GROUP BY j.id
        ORDER BY j.updated_at ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        DeadLetterJob(
            id=str(row["id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            target_type=str(row["target_type"]),
            target_id=str(row["target_id"]),
            attempt_count=int(row["attempt_count"]),
            error=loads(row["error_json"], {}),
            updated_at=str(row["updated_at"]),
        )
        for row in rows
    ]


def dead_letter_payload(conn: sqlite3.Connection, *, limit: int = 100) -> dict[str, Any]:
    """Return the JSON payload an admin route or CLI can expose for failed jobs."""

    items = [job.to_dict() for job in list_dead_letter_jobs(conn, limit=limit)]
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
    }


def replay_dead_letter_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    reason: str,
    registry: MetricsRegistry = METRICS,
) -> dict[str, Any]:
    """Move a failed/cancelled job back to queued state for manual replay."""

    job = Repository(conn).retry_job(job_id, JobRetryRequest(reason=reason))
    registry.increment("research_hub_dead_letter_replays_total", kind=job.kind, status=job.status)
    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "next_poll_after": job.next_poll_after,
        "request": job.request,
    }


@dataclass(frozen=True)
class AlertEvent:
    name: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, str] = field(default_factory=current_trace_context)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AlertSink(Protocol):
    def send(self, event: AlertEvent) -> None:
        """Deliver one alert event."""


class InMemoryAlertSink:
    """Test and development sink that keeps delivered alerts in process."""

    def __init__(self) -> None:
        self.events: list[AlertEvent] = []

    def send(self, event: AlertEvent) -> None:
        self.events.append(event)


class LoggingAlertSink:
    """Alert sink that emits alerts through structured logging."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("research_hub.alerts")

    def send(self, event: AlertEvent) -> None:
        log_event(
            self.logger,
            "alert.emitted",
            event.message,
            level=logging.WARNING,
            **event.to_dict(),
        )


class WebhookAlertSink:
    """Minimal JSON webhook alert sink using only the standard library."""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def send(self, event: AlertEvent) -> None:
        payload = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=self.timeout_seconds):
            return


def emit_alert(event: AlertEvent, sinks: Iterable[AlertSink]) -> int:
    """Deliver an alert to all configured sinks and return delivery count."""

    delivered = 0
    for sink in sinks:
        sink.send(event)
        delivered += 1
    METRICS.increment("research_hub_alerts_total", severity=event.severity, alert=event.name)
    return delivered


def alert_on_dead_letters(
    conn: sqlite3.Connection,
    sinks: Iterable[AlertSink],
    *,
    limit: int = 100,
) -> AlertEvent | None:
    """Emit one alert when failed jobs are present."""

    failed = list_dead_letter_jobs(conn, limit=limit)
    if not failed:
        return None
    event = AlertEvent(
        name="research_hub.dead_letter_jobs",
        severity="warning",
        message=f"{len(failed)} failed Research Hub job(s) require attention",
        details={"jobs": [job.to_dict() for job in failed]},
    )
    emit_alert(event, sinks)
    return event


def _labels(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _metric_rows(metrics: Mapping[tuple[str, tuple[tuple[str, str], ...]], float]) -> list[dict[str, Any]]:
    return [
        {"name": name, "labels": dict(labels), "value": value}
        for (name, labels), value in sorted(metrics.items())
    ]


def _metric_line(name: str, value: float, labels: Mapping[str, str]) -> str:
    if labels:
        label_text = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items()))
        return f"{name}{{{label_text}}} {value:g}"
    return f"{name} {value:g}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _json_safe_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(dumps(dict(value)))
