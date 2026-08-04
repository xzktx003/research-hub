from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_hub.database import Database
from research_hub.models import ArtifactCreate
from research_hub.repository import ConflictError, Repository


def test_initialize_creates_schema_version(initialized_db) -> None:
    with initialized_db.connect() as conn:
        value = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()

    assert value["value"] == "6"


def test_paper_schema_contains_translated_abstract_and_method_summary(initialized_db) -> None:
    with initialized_db.connect() as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(paper)").fetchall()
        }

    assert "translated_abstract" in columns
    assert "method_summary" in columns


def test_initialize_creates_core_tables(initialized_db) -> None:
    expected = {
        "paper",
        "paper_identifier",
        "paper_version",
        "paper_source_hit",
        "topic",
        "job",
        "artifact",
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
    }
    with initialized_db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()

    assert expected.issubset({row["name"] for row in rows})


def test_seeded_topic_catalog_contains_at_least_ten_ai_infra_topics(initialized_db) -> None:
    with initialized_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM topic WHERE enabled = 1").fetchone()["count"]

    assert count >= 10


def test_paper_identifier_uniqueness_prevents_duplicate_papers(initialized_db) -> None:
    with initialized_db.connect() as conn:
        conn.execute(
            "INSERT INTO paper (id, canonical_title) VALUES ('paper-1', 'KV Cache Quantization')"
        )
        conn.execute(
            """
            INSERT INTO paper_identifier (id, paper_id, identifier_type, identifier_value)
            VALUES ('identifier-1', 'paper-1', 'doi', '10.0000/example')
            """
        )
        conn.execute(
            "INSERT INTO paper (id, canonical_title) VALUES ('paper-2', 'Duplicate KV Cache Quantization')"
        )

        try:
            conn.execute(
                """
                INSERT INTO paper_identifier (id, paper_id, identifier_type, identifier_value)
                VALUES ('identifier-2', 'paper-2', 'doi', '10.0000/example')
                """
            )
        except sqlite3.IntegrityError:
            duplicate_rejected = True
        else:
            duplicate_rejected = False

    assert duplicate_rejected is True


def test_merged_paper_preserves_multiple_source_hits(initialized_db) -> None:
    with initialized_db.connect() as conn:
        conn.execute("INSERT INTO paper (id, canonical_title) VALUES ('paper-1', 'PD Disaggregation')")
        conn.execute(
            """
            INSERT INTO paper_source_hit (id, paper_id, source, query, hit_date)
            VALUES ('hit-arxiv', 'paper-1', 'arxiv', 'pd disaggregation', '2026-08-02')
            """
        )
        conn.execute(
            """
            INSERT INTO paper_source_hit (id, paper_id, source, query, hit_date)
            VALUES ('hit-openreview', 'paper-1', 'openreview', 'pd disaggregation', '2026-08-02')
            """
        )
        sources = conn.execute(
            "SELECT source FROM paper_source_hit WHERE paper_id = 'paper-1' ORDER BY source"
        ).fetchall()

    assert [row["source"] for row in sources] == ["arxiv", "openreview"]


def test_job_idempotency_key_reuses_existing_target_job(initialized_db) -> None:
    with initialized_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO job (id, kind, status, target_type, target_id, idempotency_key)
            VALUES ('job-1', 'parse', 'queued', 'paper_version', 'pv-1', 'idem-1')
            """
        )
        try:
            conn.execute(
                """
                INSERT INTO job (id, kind, status, target_type, target_id, idempotency_key)
                VALUES ('job-2', 'parse', 'queued', 'paper_version', 'pv-1', 'idem-1')
                """
            )
        except sqlite3.IntegrityError:
            duplicate_rejected = True
        else:
            duplicate_rejected = False

    assert duplicate_rejected is True


def test_idempotency_key_reuse_across_routes_is_rejected(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)

        first_status, first_payload = repo.use_idempotency(
            "idem-route",
            "POST",
            "/api/v1/papers",
            {"same": True},
            lambda: (201, {"id": "paper-idem"}),
        )
        with pytest.raises(ConflictError, match="different API route"):
            repo.use_idempotency(
                "idem-route",
                "POST",
                "/api/v1/topics/aif-01",
                {"same": True},
                lambda: (200, {"id": "aif-01"}),
            )

    assert first_status == 201
    assert first_payload == {"id": "paper-idem"}


def test_async_job_idempotency_key_rejects_different_request_body(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        first = repo.create_job(
            "parse",
            "paper_version",
            "pv-1",
            {"force": False, "options": {"gpu_id": 0}},
            "idem-async-body",
        )

        with pytest.raises(ConflictError, match="different asynchronous request body"):
            repo.create_job(
                "parse",
                "paper_version",
                "pv-1",
                {"force": True, "options": {"gpu_id": 1}},
                "idem-async-body",
            )

    assert first.status == "queued"


def test_initialize_reports_legacy_artifact_duplicates_before_indexing(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-duplicates.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE artifact (
                id TEXT PRIMARY KEY,
                paper_version_id TEXT,
                patent_draft_id TEXT,
                artifact_type TEXT NOT NULL,
                uri TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                checksum TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(paper_version_id, patent_draft_id, artifact_type, uri)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO artifact (id, paper_version_id, artifact_type, uri, media_type)
            VALUES ('art-dup-1', 'pv-dup', 'markdown', 'inline://dup', 'text/markdown')
            """
        )
        conn.execute(
            """
            INSERT INTO artifact (id, paper_version_id, artifact_type, uri, media_type)
            VALUES ('art-dup-2', 'pv-dup', 'markdown', 'inline://dup', 'text/markdown')
            """
        )

    with pytest.raises(RuntimeError, match="duplicate rows exist"):
        Database(db_path).initialize()


def test_artifact_partial_unique_indexes_reject_version_and_draft_duplicates(initialized_db) -> None:
    with initialized_db.connect() as conn:
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'artifact'"
            ).fetchall()
        }
        assert "ux_artifact_version_type_uri" in indexes
        assert "ux_artifact_draft_type_uri" in indexes

        conn.execute("INSERT INTO paper (id, canonical_title) VALUES ('paper-idx', 'Index Paper')")
        conn.execute(
            "INSERT INTO paper_version (id, paper_id, version_label) VALUES ('pv-idx', 'paper-idx', 'v1')"
        )
        conn.execute(
            """
            INSERT INTO invention_candidate (
                id, title, status, source_refs_json, integration_mechanism,
                coupling_interface, data_or_control_flow, why_not_juxtaposition,
                expected_joint_effect
            )
            VALUES (
                'cand-idx', 'Index Candidate', 'approved', '[]',
                'integrated mechanism',
                'runtime control interface',
                'control signal flow',
                'not a juxtaposition because feedback changes both controls',
                'expected joint latency and accuracy effect'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO patent_draft (
                id, invention_candidate_id, case_name, version_label, status
            )
            VALUES ('draft-idx', 'cand-idx', 'Case', 'v1', 'drafted')
            """
        )
        conn.execute(
            """
            INSERT INTO artifact (id, paper_version_id, artifact_type, uri, media_type)
            VALUES ('art-pv-1', 'pv-idx', 'markdown', 'inline://same', 'text/markdown')
            """
        )
        conn.execute(
            """
            INSERT INTO artifact (id, patent_draft_id, artifact_type, uri, media_type)
            VALUES ('art-draft-1', 'draft-idx', 'docx', 'file:///tmp/same.docx', 'application/octet-stream')
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO artifact (id, paper_version_id, artifact_type, uri, media_type)
                VALUES ('art-pv-2', 'pv-idx', 'markdown', 'inline://same', 'text/markdown')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO artifact (id, patent_draft_id, artifact_type, uri, media_type)
                VALUES ('art-draft-2', 'draft-idx', 'docx', 'file:///tmp/same.docx', 'application/octet-stream')
                """
            )


def test_duplicate_version_artifact_registration_returns_existing_row(initialized_db) -> None:
    with initialized_db.connect() as conn:
        conn.execute("INSERT INTO paper (id, canonical_title) VALUES ('paper-art', 'Artifact Paper')")
        conn.execute(
            "INSERT INTO paper_version (id, paper_id, version_label) VALUES ('pv-art', 'paper-art', 'v1')"
        )
        repo = Repository(conn)
        request = ArtifactCreate(
            artifact_type="markdown",
            uri="inline://duplicate",
            media_type="text/markdown",
            metadata={"content": "# duplicate"},
        )

        first = repo.create_artifact_for_version("pv-art", request)
        second = repo.create_artifact_for_version("pv-art", request)
        count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM artifact
            WHERE paper_version_id = 'pv-art' AND artifact_type = 'markdown' AND uri = 'inline://duplicate'
            """
        ).fetchone()["count"]

    assert second.id == first.id
    assert count == 1
