from __future__ import annotations

import json
from datetime import date, timedelta

from research_hub.operations import audit_recent_operations


def test_audit_14_day_accepts_single_isolated_failure_without_duplicate_records(initialized_db) -> None:
    end = date(2026, 8, 2)
    with initialized_db.connect() as conn:
        for offset in range(14):
            current = end - timedelta(days=13 - offset)
            _insert_discovery_run(conn, f"run-{current}", current, "succeeded")

        _insert_paper_version(conn, "paper-a", "pver-a", "Paper A", end - timedelta(days=1))
        _insert_paper_version(conn, "paper-b", "pver-b", "Paper B", end)
        for version_id in ("pver-a", "pver-b"):
            for kind in ("download", "parse", "analyze", "translate"):
                status = "retryable_failed" if version_id == "pver-b" and kind == "translate" else "succeeded"
                _insert_job(conn, f"job-{version_id}-{kind}", kind, version_id, status, end)

    report = audit_recent_operations(initialized_db.path, end_date=end)

    assert report["status"] == "ok"
    assert report["checks"]["daily_runs"]["missing_dates"] == []
    assert report["checks"]["duplicates"]["issue_count"] == 0
    assert report["checks"]["missing_jobs"]["missing"] == []
    assert [job["id"] for job in report["checks"]["failure_isolation"]["isolated_failures"]] == ["job-pver-b-translate"]
    assert report["checks"]["failure_isolation"]["blocking_failures"] == []


def test_audit_14_day_reports_missing_run_duplicate_version_and_unexplained_missing_job(initialized_db) -> None:
    end = date(2026, 8, 2)
    missing_day = date(2026, 7, 25)
    with initialized_db.connect() as conn:
        for offset in range(14):
            current = end - timedelta(days=13 - offset)
            if current != missing_day:
                _insert_discovery_run(conn, f"run-{current}", current, "succeeded")

        _insert_paper_version(conn, "paper-a", "pver-a", "Duplicate Main", end)
        _insert_paper_version(conn, "paper-b", "pver-b", "Duplicate Main", end)
        for kind in ("download", "parse", "analyze"):
            _insert_job(conn, f"job-pver-a-{kind}", kind, "pver-a", "succeeded", end)
        for kind in ("download", "parse", "analyze", "translate"):
            _insert_job(conn, f"job-pver-b-{kind}", kind, "pver-b", "succeeded", end)

    report = audit_recent_operations(initialized_db.path, end_date=end)

    assert report["status"] == "failed"
    assert report["checks"]["daily_runs"]["missing_dates"] == [missing_day.isoformat()]
    assert report["checks"]["duplicates"]["paper_title_duplicates"][0]["duplicate_key"] == "duplicate main"
    assert report["checks"]["missing_jobs"]["missing"] == [
        {"paper_id": "paper-a", "paper_version_id": "pver-a", "missing_kind": "translate"}
    ]


def test_audit_14_day_respects_metadata_skip_explanations(initialized_db) -> None:
    end = date(2026, 8, 2)
    with initialized_db.connect() as conn:
        for offset in range(14):
            current = end - timedelta(days=13 - offset)
            _insert_discovery_run(conn, f"run-{current}", current, "succeeded")
        _insert_paper_version(
            conn,
            "paper-a",
            "pver-a",
            "Paper A",
            end,
            version_metadata={"operations": {"skip_jobs": {"translate": "not needed"}}},
        )
        for kind in ("download", "parse", "analyze"):
            _insert_job(conn, f"job-pver-a-{kind}", kind, "pver-a", "succeeded", end)

    report = audit_recent_operations(initialized_db.path, end_date=end)

    assert report["status"] == "ok"
    assert report["checks"]["missing_jobs"]["missing"] == []


def _insert_discovery_run(conn, run_id: str, run_date: date, status: str) -> None:
    timestamp = f"{run_date.isoformat()}T09:00:00+00:00"
    conn.execute(
        """
        INSERT INTO discovery_run (id, source, status, window_start, window_end, created_at, updated_at)
        VALUES (?, 'arxiv', ?, ?, ?, ?, ?)
        """,
        (run_id, status, timestamp, f"{run_date.isoformat()}T23:59:59+00:00", timestamp, timestamp),
    )


def _insert_paper_version(
    conn,
    paper_id: str,
    version_id: str,
    title: str,
    created_date: date,
    *,
    version_metadata: dict | None = None,
) -> None:
    timestamp = f"{created_date.isoformat()}T10:00:00+00:00"
    conn.execute(
        """
        INSERT INTO paper (id, canonical_title, metadata_json, created_at, updated_at)
        VALUES (?, ?, '{}', ?, ?)
        """,
        (paper_id, title, timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO paper_version
            (id, paper_id, version_label, source, source_version_id, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'v1', 'arxiv', ?, ?, ?, ?)
        """,
        (
            version_id,
            paper_id,
            version_id,
            json.dumps(version_metadata or {}, sort_keys=True),
            timestamp,
            timestamp,
        ),
    )


def _insert_job(conn, job_id: str, kind: str, version_id: str, status: str, updated_date: date) -> None:
    timestamp = f"{updated_date.isoformat()}T11:00:00+00:00"
    error_json = json.dumps({"message": "single-paper failure"}) if status != "succeeded" else "{}"
    conn.execute(
        """
        INSERT INTO job
            (id, kind, status, target_type, target_id, idempotency_key, error_json, created_at, updated_at)
        VALUES (?, ?, ?, 'paper_version', ?, ?, ?, ?, ?)
        """,
        (job_id, kind, status, version_id, f"{kind}:{version_id}", error_json, timestamp, timestamp),
    )
