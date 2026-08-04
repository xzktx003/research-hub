from __future__ import annotations

import json

from research_hub.database import Database
from research_hub.postgres import (
    EXPECTED_SCHEMA_VERSION,
    PostgresMigrationRunner,
    diff_sqlite_to_postgres,
    extract_postgres_tables,
    load_migrations,
    main,
    split_sql_statements,
)


def test_postgres_migration_files_are_loadable_and_checksummed() -> None:
    migrations = load_migrations()

    assert [migration.version for migration in migrations] == ["001", "002", "003"]
    assert migrations[0].name == "initial_schema"
    assert len(migrations[0].checksum) == 64
    assert "CREATE TABLE IF NOT EXISTS paper" in migrations[0].sql


def test_postgres_ddl_has_core_schema_and_artifact_indexes() -> None:
    tables = extract_postgres_tables()

    assert {
        "schema_migrations",
        "schema_meta",
        "paper",
        "paper_version",
        "artifact",
        "job",
        "invention_candidate",
        "patent_draft",
        "topic_config_version",
        "topic_alias",
        "topic_quota",
        "author",
        "organization",
        "venue",
        "paper_author",
        "author_organization",
        "paper_venue",
        "pipeline_run",
        "candidate_component",
        "integration_mechanism_record",
        "patent_stage_run",
    }.issubset(tables)
    assert {"paper_version_id", "patent_draft_id", "checksum", "metadata_json"}.issubset(
        tables["artifact"]
    )
    assert "translated_abstract" in tables["paper"]
    assert "method_summary" in tables["paper"]
    assert {"config_version_id", "daily_quota"}.issubset(tables["topic"])
    assert {"config_version_id", "alias", "alias_type", "weight"}.issubset(
        tables["topic_alias"]
    )
    assert {"normalized_name", "orcid", "metadata_json"}.issubset(tables["author"])
    assert {"status", "run_type", "config_version_id", "input_counts_json"}.issubset(
        tables["pipeline_run"]
    )
    assert {"source_ref_index", "component_type", "evidence_json"}.issubset(
        tables["candidate_component"]
    )
    assert {
        "coupling_interface",
        "data_or_control_flow",
        "why_not_juxtaposition",
        "expected_joint_effect",
    }.issubset(tables["integration_mechanism_record"])
    assert {"stage", "status", "input_json", "output_json", "artifact_id", "job_id"}.issubset(
        tables["patent_stage_run"]
    )

    migration_sql = load_migrations()[0].sql
    assert "ux_artifact_version_type_uri" in migration_sql
    assert "ux_artifact_draft_type_uri" in migration_sql
    assert "CHECK (num_nonnulls(paper_version_id, patent_draft_id) = 1)" in migration_sql
    assert "idx_pipeline_run_status" in migration_sql
    assert "idx_candidate_component_candidate" in migration_sql
    assert "idx_integration_mechanism_candidate" in migration_sql
    assert "idx_patent_stage_run_candidate" in migration_sql
    assert "idx_patent_stage_run_status" in migration_sql
    assert "VALUES ('schema_version', '5')" in migration_sql
    assert "VALUES ('schema_version', '6')" in load_migrations()[1].sql


def test_split_sql_statements_preserves_dollar_quoted_blocks() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS example (id TEXT PRIMARY KEY);
    DO $$
    BEGIN
        RAISE NOTICE 'semicolon stays inside block; ok';
    END
    $$;
    INSERT INTO example (id) VALUES ('literal;semicolon');
    """

    statements = split_sql_statements(sql)

    assert len(statements) == 3
    assert "RAISE NOTICE" in statements[1]
    assert "literal;semicolon" in statements[2]


def test_postgres_dry_run_reports_pending_checksummed_plan() -> None:
    plan = PostgresMigrationRunner().apply(dry_run=True)

    assert plan.applied_versions == ()
    assert [migration.version for migration in plan.pending] == ["001", "002", "003"]
    assert len(plan.plan_checksum) == 64
    assert plan.to_dict()["migrations"][0]["statement_count"] > 10


def test_sqlite_schema_matches_postgres_contract(tmp_path) -> None:
    sqlite_path = tmp_path / "research_hub.sqlite3"
    Database(sqlite_path).initialize()

    diff = diff_sqlite_to_postgres(sqlite_path)

    assert diff.ok, diff.to_dict()
    assert diff.sqlite_checksum is not None
    assert diff.postgres_plan_checksum == PostgresMigrationRunner().plan().plan_checksum


def test_cli_dry_run_outputs_json_with_sqlite_diff(tmp_path, capsys) -> None:
    sqlite_path = tmp_path / "research_hub.sqlite3"
    Database(sqlite_path).initialize()

    exit_code = main(["--dry-run", "--sqlite", str(sqlite_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["plan"]["pending_count"] == 3
    assert payload["sqlite_diff"]["ok"] is True


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self._last_sql = ""

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self._last_sql = sql
        self.executed.append((sql, params))

    def fetchall(self) -> list[tuple[str]]:
        if "information_schema.tables" in self._last_sql:
            return [
                ("schema_migrations",),
                ("schema_meta",),
                ("paper",),
                ("paper_version",),
                ("artifact",),
                ("job",),
                ("invention_candidate",),
                ("patent_draft",),
                ("topic_config_version",),
                ("topic_alias",),
                ("topic_quota",),
                ("topic_digest_note",),
                ("author",),
                ("organization",),
                ("venue",),
                ("paper_author",),
                ("author_organization",),
                ("paper_venue",),
                ("pipeline_run",),
                ("candidate_component",),
                ("integration_mechanism_record",),
                ("patent_stage_run",),
            ]
        return []

    def fetchone(self) -> tuple[str] | None:
        if "schema_meta" in self._last_sql:
            return (EXPECTED_SCHEMA_VERSION,)
        return None

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        return None


def test_postgres_runner_applies_pending_migration_and_smoke_checks() -> None:
    conn = _FakeConnection()
    runner = PostgresMigrationRunner(conn=conn)

    plan = runner.apply()
    smoke = runner.smoke_check()

    assert [migration.version for migration in plan.pending] == ["001", "002", "003"]
    assert conn.committed is True
    assert conn.rolled_back is False
    assert smoke == {"ok": True, "missing_tables": [], "schema_version": EXPECTED_SCHEMA_VERSION}
    assert any("INSERT INTO schema_migrations" in sql for sql, _ in conn.cursor_instance.executed)
