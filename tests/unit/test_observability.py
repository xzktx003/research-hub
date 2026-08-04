from __future__ import annotations

from io import StringIO
import json
import logging

from research_hub.database import dumps
from research_hub.models import PaperCreate
from research_hub.observability import (
    AlertEvent,
    InMemoryAlertSink,
    MetricsRegistry,
    alert_on_dead_letters,
    collect_job_metrics,
    configure_json_logging,
    current_trace_context,
    dead_letter_payload,
    emit_alert,
    list_dead_letter_jobs,
    log_event,
    replay_dead_letter_job,
    trace_context,
    trace_from_headers,
)
from research_hub.repository import Repository


def test_trace_context_and_json_logging_include_stable_ids() -> None:
    stream = StringIO()
    logger = configure_json_logging(logging.getLogger("test.trace"), stream=stream)
    logger.propagate = False

    with trace_context(trace_id="trc-test", request_id="req-test", job_id="job-test"):
        log_event(logger, "unit.event", "hello", component="unit", count=1)

    payload = json.loads(stream.getvalue())
    assert payload["trace_id"] == "trc-test"
    assert payload["request_id"] == "req-test"
    assert payload["job_id"] == "job-test"
    assert payload["event"] == "unit.event"
    assert payload["component"] == "unit"
    assert payload["count"] == 1
    assert current_trace_context() == {}


def test_trace_from_headers_accepts_request_and_trace_ids() -> None:
    trace = trace_from_headers({"X-Trace-Id": "trc-in", "X-Request-Id": "req-in"})
    assert trace == {"trace_id": "trc-in", "request_id": "req-in"}


def test_metrics_registry_renders_json_and_text() -> None:
    registry = MetricsRegistry()
    registry.increment("research_hub_jobs_total", kind="parse", status="succeeded")
    registry.gauge("research_hub_dead_letter_jobs", 2)

    snapshot = registry.snapshot()
    assert snapshot["counters"][0]["value"] == 1.0
    assert "research_hub_jobs_total" in registry.render_json()
    assert 'research_hub_jobs_total{kind="parse",status="succeeded"} 1' in registry.render_text()
    assert "research_hub_dead_letter_jobs 2" in registry.render_text()


def test_database_metrics_cover_phase_8_business_signals(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = repo.create_paper(PaperCreate(canonical_title="Metrics Paper"))
        jobs = {
            kind: repo.create_job(kind, "paper", paper.id, {"source": "unit"})
            for kind in ("discover", "download", "parse", "analyze")
        }
        conn.execute(
            "UPDATE job SET status = 'succeeded', result_json = ? WHERE id = ?",
            (
                dumps(
                    {
                        "source_outcomes": [
                            {"source": "arxiv", "status": "ok"},
                            {"source": "openreview", "status": "degraded"},
                        ]
                    }
                ),
                jobs["discover"].job_id,
            ),
        )
        conn.execute(
            "UPDATE job SET status = 'succeeded' WHERE id IN (?, ?)",
            (jobs["download"].job_id, jobs["parse"].job_id),
        )
        conn.execute(
            "UPDATE job SET status = 'succeeded', result_json = ? WHERE id = ?",
            (
                dumps({"response": {"data": {"total_tokens": 321}}}),
                jobs["analyze"].job_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO job_attempt (id, job_id, attempt_no, status, started_at, completed_at)
            VALUES ('attempt-metrics-parse', ?, 1, 'succeeded', ?, ?)
            """,
            (
                jobs["parse"].job_id,
                "2026-08-02T10:00:00+00:00",
                "2026-08-02T10:00:02+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO invention_candidate (
                id, title, status, source_refs_json, evidence_json
            ) VALUES ('candidate-metrics', 'Metrics Candidate', 'approved', '[]', '[]')
            """
        )

        registry = MetricsRegistry()
        snapshot = collect_job_metrics(conn, registry=registry)
        gauges = {
            (item["name"], tuple(sorted(item["labels"].items()))): item["value"]
            for item in snapshot["gauges"]
        }

    assert gauges[("research_hub_source_success_ratio", (("source", "arxiv"),))] == 1.0
    assert gauges[("research_hub_source_success_ratio", (("source", "openreview"),))] == 0.0
    assert gauges[("research_hub_download_success_ratio", ())] == 1.0
    assert gauges[("research_hub_parse_duration_ms_avg", ())] == 2000.0
    assert gauges[("research_hub_model_tokens_total", (("kind", "analyze"),))] == 321.0
    assert gauges[("research_hub_report_success_ratio", ())] == 1.0
    assert gauges[("research_hub_patent_candidate_pass_ratio", ())] == 1.0


def test_dead_letter_listing_and_replay_use_existing_job_contract(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = repo.create_paper(
            PaperCreate(canonical_title="Observable Paper"),
        )
        job = repo.create_job(
            "parse",
            "paper_version",
            paper.current_version_id or "",
            {"source": "unit"},
        )
        conn.execute(
            """
            UPDATE job
            SET status = 'retryable_failed', error_json = ?
            WHERE id = ?
            """,
            (dumps({"message": "adapter down"}), job.job_id),
        )

        failed = list_dead_letter_jobs(conn)
        assert [item.id for item in failed] == [job.job_id]
        assert failed[0].error == {"message": "adapter down"}
        assert dead_letter_payload(conn)["count"] == 1

        replayed = replay_dead_letter_job(conn, job.job_id, reason="manual fix")
        saved = repo.get_job(job.job_id)
        assert replayed["status"] == "queued"
        assert saved.status == "queued"
        assert saved.request["_retry"]["retry_reason"] == "manual fix"
        assert list_dead_letter_jobs(conn) == []


def test_alert_hook_abstraction_emits_dead_letter_alert(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = repo.create_paper(
            PaperCreate(canonical_title="Alert Paper"),
        )
        job = repo.create_job("analyze", "paper_version", paper.current_version_id or "", {})
        conn.execute(
            "UPDATE job SET status = 'terminal_failed', error_json = ? WHERE id = ?",
            (dumps({"message": "bad output"}), job.job_id),
        )

        sink = InMemoryAlertSink()
        event = alert_on_dead_letters(conn, [sink])
        assert event is not None
        assert sink.events[0].name == "research_hub.dead_letter_jobs"
        assert sink.events[0].details["jobs"][0]["id"] == job.job_id


def test_emit_alert_returns_delivery_count() -> None:
    sink = InMemoryAlertSink()
    delivered = emit_alert(
        AlertEvent(name="unit", severity="info", message="test"),
        [sink],
    )
    assert delivered == 1
    assert sink.events[0].message == "test"
