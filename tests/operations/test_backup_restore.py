from __future__ import annotations

import sqlite3
from pathlib import Path

from research_hub.operations import backup_sqlite_database, file_sha256, restore_sqlite_database


def test_backup_restore_preserves_checksum_and_row_counts(initialized_db, tmp_path: Path) -> None:
    with initialized_db.connect() as conn:
        conn.execute(
            "INSERT INTO paper (id, canonical_title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("paper-ops", "Operations Paper", "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO paper_version (id, paper_id, version_label, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("pver-ops", "paper-ops", "v1", "manual", "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
        )

    backup_path = tmp_path / "backup.sqlite3"
    manifest = backup_sqlite_database(initialized_db.path, backup_path)

    assert manifest.integrity_check == "ok"
    assert manifest.checksum_sha256 == file_sha256(backup_path)
    assert manifest.row_counts["paper"] == 1
    assert manifest.row_counts["paper_version"] == 1

    restored_path = tmp_path / "restored.sqlite3"
    report = restore_sqlite_database(
        backup_path,
        restored_path,
        expected_checksum=manifest.checksum_sha256,
    )

    assert report["status"] == "ok"
    assert report["backup_row_counts"] == report["restored_row_counts"]
    with sqlite3.connect(restored_path) as conn:
        assert conn.execute("SELECT canonical_title FROM paper WHERE id = 'paper-ops'").fetchone()[0] == "Operations Paper"
