"""Operational backup, restore, and audit helpers for Research Hub SQLite data."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


TERMINAL_FAILURE_STATUSES = {"retryable_failed", "terminal_failed", "failed"}
SUCCESS_STATUSES = {"succeeded"}
DEFAULT_EXPECTED_VERSION_JOBS = ("download", "parse", "analyze", "translate")


@dataclass(frozen=True)
class BackupManifest:
    """Evidence emitted after a consistent SQLite backup."""

    source_path: str
    backup_path: str
    checksum_sha256: str
    row_counts: dict[str, int]
    integrity_check: str
    page_count: int
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "backup_path": self.backup_path,
            "checksum_sha256": self.checksum_sha256,
            "row_counts": self.row_counts,
            "integrity_check": self.integrity_check,
            "page_count": self.page_count,
            "created_at": self.created_at,
        }


def backup_sqlite_database(source_path: Path, backup_path: Path) -> BackupManifest:
    """Create a consistent SQLite backup and return verification evidence."""

    source_path = source_path.expanduser().resolve()
    backup_path = backup_path.expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        backup_path.unlink()

    with _connect_readonly(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
        target.commit()

    with sqlite3.connect(backup_path) as check_conn:
        check_conn.row_factory = sqlite3.Row
        row_counts = table_row_counts(check_conn)
        integrity = integrity_check(check_conn)
        page_count = int(check_conn.execute("PRAGMA page_count").fetchone()[0])

    return BackupManifest(
        source_path=str(source_path),
        backup_path=str(backup_path),
        checksum_sha256=file_sha256(backup_path),
        row_counts=row_counts,
        integrity_check=integrity,
        page_count=page_count,
        created_at=utc_now(),
    )


def restore_sqlite_database(
    backup_path: Path,
    target_path: Path,
    *,
    expected_checksum: str | None = None,
) -> dict[str, Any]:
    """Restore a SQLite backup into target_path and verify checksum and row counts."""

    backup_path = backup_path.expanduser().resolve()
    target_path = target_path.expanduser().resolve()
    if not backup_path.exists():
        raise FileNotFoundError(f"SQLite backup not found: {backup_path}")

    actual_checksum = file_sha256(backup_path)
    if expected_checksum and actual_checksum != expected_checksum:
        raise ValueError(
            f"Backup checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
        )

    with _connect_readonly(backup_path) as source:
        source.row_factory = sqlite3.Row
        backup_counts = table_row_counts(source)
        backup_integrity = integrity_check(source)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    with _connect_readonly(backup_path) as source, sqlite3.connect(target_path) as target:
        source.backup(target)
        target.commit()

    with sqlite3.connect(target_path) as restored:
        restored.row_factory = sqlite3.Row
        restored_counts = table_row_counts(restored)
        restored_integrity = integrity_check(restored)

    return {
        "status": "ok" if backup_counts == restored_counts and restored_integrity == "ok" else "failed",
        "backup_path": str(backup_path),
        "target_path": str(target_path),
        "checksum_sha256": actual_checksum,
        "backup_row_counts": backup_counts,
        "restored_row_counts": restored_counts,
        "backup_integrity_check": backup_integrity,
        "restored_integrity_check": restored_integrity,
        "restored_at": utc_now(),
    }


def audit_recent_operations(
    db_path: Path,
    *,
    days: int = 14,
    end_date: date | None = None,
    expected_version_jobs: Iterable[str] = DEFAULT_EXPECTED_VERSION_JOBS,
) -> dict[str, Any]:
    """Audit recent daily runs, duplicate records, missing jobs, and failure isolation."""

    if days < 1:
        raise ValueError("days must be >= 1")
    db_path = db_path.expanduser().resolve()
    end = end_date or datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    expected_jobs = tuple(dict.fromkeys(expected_version_jobs))

    with _connect_readonly(db_path) as conn:
        conn.row_factory = sqlite3.Row
        daily_runs = _daily_run_check(conn, start, end)
        duplicates = _duplicate_check(conn, start, end)
        missing_jobs = _missing_jobs_check(conn, start, end, expected_jobs)
        failure_isolation = _failure_isolation_check(conn, start, end)
        counts = _window_counts(conn, start, end)

    checks = {
        "daily_runs": daily_runs,
        "duplicates": duplicates,
        "missing_jobs": missing_jobs,
        "failure_isolation": failure_isolation,
    }
    return {
        "status": "ok" if all(item["status"] == "ok" for item in checks.values()) else "failed",
        "window": {"start_date": start.isoformat(), "end_date": end.isoformat(), "days": days},
        "expected_version_jobs": list(expected_jobs),
        "counts": counts,
        "checks": checks,
        "generated_at": utc_now(),
    }


def table_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]
    return {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in names}


def integrity_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "missing"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_report(report: dict[str, Any], output_path: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if output_path is None:
        print(payload)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload + "\n", encoding="utf-8")


def _daily_run_check(conn: sqlite3.Connection, start: date, end: date) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, status, COALESCE(window_start, created_at) AS observed_at
        FROM discovery_run
        WHERE date(COALESCE(window_start, created_at)) BETWEEN ? AND ?
        ORDER BY observed_at, id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    by_day: dict[str, list[dict[str, str]]] = {day.isoformat(): [] for day in _date_range(start, end)}
    for row in rows:
        day = _date_part(row["observed_at"])
        if day in by_day:
            by_day[day].append({"id": row["id"], "status": row["status"]})
    missing = [day for day, values in by_day.items() if not values]
    return {
        "status": "ok" if not missing else "failed",
        "missing_dates": missing,
        "runs_by_date": by_day,
    }


def _duplicate_check(conn: sqlite3.Connection, start: date, end: date) -> dict[str, Any]:
    duplicate_titles = _rows_as_dicts(
        conn.execute(
            """
            SELECT lower(trim(canonical_title)) AS duplicate_key, COUNT(*) AS count,
                   json_group_array(id) AS paper_ids
            FROM paper
            WHERE date(created_at) BETWEEN ? AND ?
            GROUP BY lower(trim(canonical_title))
            HAVING COUNT(*) > 1
            ORDER BY count DESC, duplicate_key
            """,
            (start.isoformat(), end.isoformat()),
        )
    )
    duplicate_identifiers = _rows_as_dicts(
        conn.execute(
            """
            SELECT identifier_type, identifier_value, COUNT(*) AS count,
                   json_group_array(paper_id) AS paper_ids
            FROM paper_identifier
            GROUP BY identifier_type, identifier_value
            HAVING COUNT(DISTINCT paper_id) > 1
            ORDER BY count DESC, identifier_type, identifier_value
            """
        )
    )
    duplicate_versions = _rows_as_dicts(
        conn.execute(
            """
            SELECT paper_id, version_label, source, COUNT(*) AS count,
                   json_group_array(id) AS paper_version_ids
            FROM paper_version
            WHERE date(created_at) BETWEEN ? AND ?
            GROUP BY paper_id, version_label, source
            HAVING COUNT(*) > 1
            ORDER BY count DESC, paper_id, version_label, source
            """,
            (start.isoformat(), end.isoformat()),
        )
    )
    duplicate_source_versions = _rows_as_dicts(
        conn.execute(
            """
            SELECT source, source_version_id, COUNT(DISTINCT paper_id) AS count,
                   json_group_array(id) AS paper_version_ids
            FROM paper_version
            WHERE source_version_id IS NOT NULL AND source_version_id != ''
              AND date(created_at) BETWEEN ? AND ?
            GROUP BY source, source_version_id
            HAVING COUNT(DISTINCT paper_id) > 1
            ORDER BY count DESC, source, source_version_id
            """,
            (start.isoformat(), end.isoformat()),
        )
    )
    issues = {
        "paper_title_duplicates": duplicate_titles,
        "identifier_duplicates": duplicate_identifiers,
        "paper_version_duplicates": duplicate_versions,
        "source_version_duplicates": duplicate_source_versions,
    }
    issue_count = sum(len(values) for values in issues.values())
    return {"status": "ok" if issue_count == 0 else "failed", "issue_count": issue_count, **issues}


def _missing_jobs_check(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    expected_jobs: tuple[str, ...],
) -> dict[str, Any]:
    version_rows = conn.execute(
        """
        SELECT pv.id, pv.paper_id, pv.metadata_json, p.metadata_json AS paper_metadata_json
        FROM paper_version pv
        JOIN paper p ON p.id = pv.paper_id
        WHERE date(pv.created_at) BETWEEN ? AND ?
        ORDER BY pv.id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    missing: list[dict[str, Any]] = []
    for version in version_rows:
        existing = {
            row["kind"]
            for row in conn.execute(
                "SELECT DISTINCT kind FROM job WHERE target_type = 'paper_version' AND target_id = ?",
                (version["id"],),
            )
        }
        explanations = _job_skip_explanations(version["metadata_json"], version["paper_metadata_json"])
        for kind in expected_jobs:
            if kind in existing:
                continue
            explanation = explanations.get(kind) or explanations.get("*")
            if explanation:
                continue
            missing.append(
                {
                    "paper_id": version["paper_id"],
                    "paper_version_id": version["id"],
                    "missing_kind": kind,
                }
            )
    return {
        "status": "ok" if not missing else "failed",
        "missing": missing,
        "version_count": len(version_rows),
    }


def _failure_isolation_check(conn: sqlite3.Connection, start: date, end: date) -> dict[str, Any]:
    failed = _rows_as_dicts(
        conn.execute(
            """
            SELECT id, kind, status, target_type, target_id, error_json, updated_at
            FROM job
            WHERE date(updated_at) BETWEEN ? AND ?
              AND status IN ('retryable_failed', 'terminal_failed', 'failed')
            ORDER BY updated_at, id
            """,
            (start.isoformat(), end.isoformat()),
        )
    )
    if not failed:
        return {"status": "ok", "failed_jobs": [], "isolated_failures": [], "blocking_failures": []}

    blocking: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []
    for job in failed:
        target_type = job["target_type"]
        target_id = job["target_id"]
        sibling_success = conn.execute(
            """
            SELECT COUNT(*)
            FROM job
            WHERE date(updated_at) BETWEEN ? AND ?
              AND status = 'succeeded'
              AND NOT (target_type = ? AND target_id = ?)
            """,
            (start.isoformat(), end.isoformat(), target_type, target_id),
        ).fetchone()[0]
        if sibling_success:
            isolated.append(job)
        else:
            blocking.append(job)
    return {
        "status": "ok" if not blocking else "failed",
        "failed_jobs": failed,
        "isolated_failures": isolated,
        "blocking_failures": blocking,
    }


def _window_counts(conn: sqlite3.Connection, start: date, end: date) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ("discovery_run", "paper", "paper_version", "job"):
        counts[table] = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE date(created_at) BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            ).fetchone()[0]
        )
    return counts


def _job_skip_explanations(*json_values: str) -> dict[str, str]:
    explanations: dict[str, str] = {}
    for raw in json_values:
        payload = _loads_json(raw, {})
        operations = payload.get("operations") if isinstance(payload, dict) else None
        if not isinstance(operations, dict):
            continue
        skip_jobs = operations.get("skip_jobs", {})
        if isinstance(skip_jobs, list):
            explanations.update({str(item): "listed in operations.skip_jobs" for item in skip_jobs})
        elif isinstance(skip_jobs, dict):
            explanations.update({str(key): str(value) for key, value in skip_jobs.items() if value})
    return explanations


def _loads_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _rows_as_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if isinstance(value, str) and value.startswith("["):
                item[key] = _loads_json(value, value)
        output.append(item)
    return output


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _date_part(value: str | None) -> str:
    if not value:
        return ""
    return value[:10]


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    if os.name == "nt":
        temp_dir = Path(tempfile.mkdtemp(prefix="research-hub-sqlite-ro-"))
        temp_copy = temp_dir / path.name
        shutil.copy2(path, temp_copy)
        return sqlite3.connect(temp_copy)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
