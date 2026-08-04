"""Importer for Dify paper_digest SQLite state, when present."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DifyPaperRecord:
    natural_key: str
    payload: dict[str, Any]

    @property
    def import_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"dify_sqlite:{self.natural_key}"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "kind": "paper_digest_state",
            "natural_key": self.natural_key,
            "payload": self.payload,
        }


class DifySQLiteImporter:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    def available(self) -> bool:
        return self.database_path.is_file()

    def import_records(self) -> list[DifyPaperRecord]:
        if not self.available():
            return []
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            if not _table_exists(connection, "papers"):
                # The database file exists but the expected table is missing.
                # This is a schema-drift / wrong-file error, not "no data": be
                # loud so the operator doesn't mistake a broken source for an
                # empty one.
                raise sqlite3.DatabaseError(
                    f"Dify SQLite source exists but has no 'papers' table: {self.database_path}"
                )
            expected_columns = {"arxiv_id", "version", "version_id", "run_id", "run_date"}
            present_columns = {column["name"] for column in connection.execute("PRAGMA table_info(papers)").fetchall()}
            missing_columns = expected_columns - present_columns
            if missing_columns:
                raise sqlite3.DatabaseError(
                    f"Dify SQLite 'papers' table is missing required columns: "
                    f"{sorted(missing_columns)} in {self.database_path}"
                )
            rows = connection.execute("SELECT * FROM papers ORDER BY updated_at").fetchall()
        finally:
            connection.close()
        return [self._record_from_row(row) for row in rows]

    def _record_from_row(self, row: sqlite3.Row) -> DifyPaperRecord:
        metadata = _json_or_none(row["metadata_json"]) or {}
        relevance = _json_or_none(_row_get(row, "relevance_json"))
        score = _json_or_none(_row_get(row, "score_json"))
        arxiv_id = row["arxiv_id"]
        natural_key = f"arxiv:{str(arxiv_id).lower()}:v{row['version']}"
        payload = {
            "source": "dify_paper_digest_sqlite",
            "version_id": row["version_id"],
            "arxiv_id": arxiv_id,
            "version": row["version"],
            "run_id": row["run_id"],
            "run_date": row["run_date"],
            "metadata": metadata,
            "area": _row_get(row, "area"),
            "summary": _row_get(row, "summary"),
            "relevance": relevance,
            "status": _row_get(row, "status"),
            "pdf_path": _row_get(row, "pdf_path"),
            "review_path": _row_get(row, "review_path"),
            "score": _row_get(row, "score"),
            "score_detail": score,
            "featured_dir": _row_get(row, "featured_dir"),
            "error": _row_get(row, "error"),
            "updated_at": _row_get(row, "updated_at"),
        }
        return DifyPaperRecord(natural_key=natural_key, payload=payload)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _row_get(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def _json_or_none(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value
