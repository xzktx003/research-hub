from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from research_hub.postgres_runtime import (
    PostgresRuntimeDatabase,
    PostgresRuntimeDependencyError,
    create_database_from_env,
    translate_qmark_sql,
)
from research_hub.database import loads
from research_hub.repository import Repository


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.description: tuple[tuple[str], ...] | None = None
        self._rows: list[tuple[object, ...]] = []
        self.rowcount = -1

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.executed.append((sql, params))
        self.description = None
        self._rows = []
        self.rowcount = 1
        if "SELECT * FROM topic" in sql:
            self.description = (
                ("id",),
                ("name_zh",),
                ("name_en",),
                ("parent_id",),
                ("enabled",),
                ("aliases_json",),
                ("rules_json",),
                ("created_at",),
                ("updated_at",),
                ("config_version_id",),
                ("daily_quota",),
            )
            self._rows = [
                (
                    "aif-01",
                    "Efficient models",
                    "Efficient Models and Architectures",
                    None,
                    True,
                    "[]",
                    "{}",
                    "2026-08-02T00:00:00+00:00",
                    "2026-08-02T00:00:00+00:00",
                    None,
                    12,
                )
            ]
        elif "SELECT version FROM schema_migrations" in sql:
            self.description = (("version",),)
            self._rows = []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class FakeDatabase:
    def __init__(self) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True


def test_translate_qmark_sql_preserves_literals_and_rewrites_is_placeholder() -> None:
    sql = "SELECT '?' AS literal, id FROM topic WHERE config_version_id IS ? OR alias = ?"

    translated, params = translate_qmark_sql(sql, (None, "moe"))

    assert translated == "SELECT '?' AS literal, id FROM topic WHERE config_version_id IS NULL OR alias = %s"
    assert params == ("moe",)


def test_translate_qmark_sql_rewrites_non_null_is_placeholder_to_equality() -> None:
    sql = "SELECT * FROM topic WHERE config_version_id IS ? OR config_version_id = ?"

    translated, params = translate_qmark_sql(sql, ("cfg-1", "cfg-1"))

    assert translated == "SELECT * FROM topic WHERE config_version_id = %s OR config_version_id = %s"
    assert params == ("cfg-1", "cfg-1")


def test_create_database_from_env_keeps_sqlite_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("RESEARCH_HUB_POSTGRES_DSN", raising=False)

    database = create_database_from_env(tmp_path / "research_hub.sqlite3")

    assert database.__class__.__name__ == "Database"


def test_json_loader_accepts_postgres_decoded_json_values() -> None:
    payload = {"sources": ["arxiv", "openreview"]}

    assert loads(payload) is payload
    assert loads(["markdown", "json"]) == ["markdown", "json"]


def test_runtime_fails_loudly_when_driver_absent(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_HUB_POSTGRES_DSN", "postgresql://example/db")
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg2", None)

    sqlite_path = Path("/tmp/unused.sqlite3")
    database = create_database_from_env(sqlite_path)

    assert isinstance(database, PostgresRuntimeDatabase)
    with pytest.raises(PostgresRuntimeDependencyError, match="neither psycopg nor psycopg2 is installed"):
        database.initialize()
    assert sqlite_path.name == "unused.sqlite3"


def test_runtime_context_commits_and_exposes_dict_rows(monkeypatch) -> None:
    raw_conn = FakeConnection()
    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: raw_conn)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg2", None)

    database = PostgresRuntimeDatabase("postgresql://example/db")
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM topic WHERE id = ?", ("aif-01",)).fetchone()

    assert row is not None
    assert row["id"] == "aif-01"
    assert row[2] == "Efficient Models and Architectures"
    assert raw_conn.committed is True
    assert raw_conn.rolled_back is False
    assert raw_conn.closed is True
    assert raw_conn.cursor_instance.executed[-1] == (
        "SELECT * FROM topic WHERE id = %s",
        ("aif-01",),
    )


def test_repository_can_read_through_runtime_connection(monkeypatch) -> None:
    raw_conn = FakeConnection()
    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: raw_conn)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg2", None)

    database = PostgresRuntimeDatabase("postgresql://example/db")
    with database.connect() as conn:
        topics = Repository(conn).list_topics()

    assert len(topics) == 1
    assert topics[0].id == "aif-01"
    assert raw_conn.cursor_instance.executed[-1] == (
        "SELECT * FROM topic WHERE deleted_at IS NULL ORDER BY id",
        None,
    )


def test_runtime_context_rolls_back_on_exception(monkeypatch) -> None:
    raw_conn = FakeConnection()
    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: raw_conn)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg2", None)

    database = PostgresRuntimeDatabase("postgresql://example/db")
    with pytest.raises(ValueError, match="boom"):
        with database.connect():
            raise ValueError("boom")

    assert raw_conn.committed is False
    assert raw_conn.rolled_back is True
    assert raw_conn.closed is True


def test_create_app_uses_runtime_database_factory(monkeypatch, tmp_path) -> None:
    from config.settings import Settings
    from research_hub import app as app_module

    database = FakeDatabase()
    monkeypatch.setattr(app_module, "create_database_from_env", lambda sqlite_path: database)

    app = app_module.create_app(
        Settings(
            database_path=tmp_path / "unused.sqlite3",
            api_key=None,
            static_dir=tmp_path / "missing-static",
        )
    )

    assert database.initialized is True
    assert app.state.database is database


def test_deployment_installs_postgres_driver_and_keeps_example_dsn_disabled(project_root) -> None:
    requirements = (project_root / "requirements.txt").read_text(encoding="utf-8")
    example_env = (project_root / ".env.example").read_text(encoding="utf-8")

    assert "psycopg[binary]==" in requirements
    assert "RESEARCH_HUB_POSTGRES_DSN=\n" in example_env
    assert "RESEARCH_HUB_POSTGRES_DSN=<" not in example_env
