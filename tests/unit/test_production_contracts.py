from __future__ import annotations

from datetime import datetime, timezone

from research_hub.models import ArtifactCreate, DiscoveryRunCreate
from research_hub.repository import Repository
from research_hub.services import ResearchJobService


def _seed_paper_version(conn) -> None:
    conn.execute(
        """
        INSERT INTO paper (
            id, canonical_title, first_publication_date, current_version_id,
            status, selected
        ) VALUES ('paper-contract', 'Contract Paper', '2026-08-02', 'pv-contract', 'analyzed', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO paper_version (
            id, paper_id, version_label, source, source_version_id
        ) VALUES ('pv-contract', 'paper-contract', 'v1', 'arxiv', '2608.00001v1')
        """
    )


def test_schema_contains_first_class_production_records(initialized_db) -> None:
    expected = {
        "job_attempt",
        "artifact_relation",
        "evidence_anchor",
        "technology_claim",
        "prior_art_record",
        "claim_provenance",
        "human_decision",
    }

    with initialized_db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()

    assert expected.issubset({row["name"] for row in rows})


def test_discovery_run_has_canonical_dedup_without_client_key(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        first = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="scheduled",
                window_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 2, tzinfo=timezone.utc),
                topics=["inference", "serving"],
                max_results=40,
            ),
            idempotency_key=None,
        )
        repeated = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="scheduled",
                window_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 2, tzinfo=timezone.utc),
                topics=["serving", "inference"],
                max_results=40,
            ),
            idempotency_key=None,
        )
        run_count = conn.execute("SELECT COUNT(*) AS n FROM discovery_run").fetchone()["n"]
        job_count = conn.execute(
            "SELECT COUNT(*) AS n FROM job WHERE kind = 'discover'"
        ).fetchone()["n"]

    assert repeated.id == first.id
    assert run_count == 1
    assert job_count == 1


def test_artifact_registration_persists_derivation_lineage(initialized_db) -> None:
    with initialized_db.connect() as conn:
        _seed_paper_version(conn)
        repo = Repository(conn)
        source = repo.create_artifact_for_version(
            "pv-contract",
            ArtifactCreate(
                artifact_type="markdown_original",
                uri="artifact://pv-contract/original.md",
                media_type="text/markdown",
            ),
        )
        derived = repo.create_artifact_for_version(
            "pv-contract",
            ArtifactCreate(
                artifact_type="markdown_zh",
                uri="artifact://pv-contract/zh.md",
                media_type="text/markdown",
                metadata={
                    "source_artifact_id": source.id,
                    "source": "translation",
                    "lineage": {"operation": "translate", "language": "zh-CN"},
                },
            ),
        )
        relation = conn.execute(
            """
            SELECT * FROM artifact_relation
            WHERE source_artifact_id = ? AND derived_artifact_id = ?
            """,
            (source.id, derived.id),
        ).fetchone()

    assert relation is not None
    assert relation["relation_type"] == "derived_from"
    assert relation["generator"] == "translation"


def test_job_attempt_records_terminal_outcome(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        job = repo.create_job(
            "analyze",
            "paper_version",
            "pv-contract",
            {"source": "contract-test"},
            idempotency_key="attempt-contract",
        )
        service = ResearchJobService(conn)
        service._mark_job(job.job_id, "running")
        service._mark_job(job.job_id, "retryable_failed", error={"message": "temporary"})
        attempt = conn.execute(
            "SELECT * FROM job_attempt WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()

    assert attempt is not None
    assert attempt["attempt_no"] == 1
    assert attempt["status"] == "retryable_failed"
    assert attempt["completed_at"] is not None


def test_daily_digest_exposes_source_dedup_failure_topic_and_routes(initialized_db) -> None:
    with initialized_db.connect() as conn:
        _seed_paper_version(conn)
        conn.execute(
            """
            INSERT INTO topic (id, name_zh, name_en)
            VALUES ('topic-serving', '推理服务', 'Inference Serving')
            """
        )
        conn.execute(
            "INSERT INTO paper_topic (paper_id, topic_id) VALUES ('paper-contract', 'topic-serving')"
        )
        conn.executemany(
            """
            INSERT INTO paper_source_hit (
                id, paper_id, paper_version_id, source, query, hit_date
            ) VALUES (?, 'paper-contract', 'pv-contract', ?, ?, '2026-08-02')
            """,
            [
                ("hit-arxiv", "arxiv", "serving"),
                ("hit-openreview", "openreview", "inference"),
            ],
        )
        conn.execute(
            """
            INSERT INTO job (id, kind, status, target_type, target_id, created_at, updated_at)
            VALUES (
                'job-failed-contract', 'translate', 'retryable_failed',
                'paper_version', 'pv-contract', '2026-08-02T12:00:00Z', '2026-08-02T12:00:00Z'
            )
            """
        )
        digest = Repository(conn).daily_digest("2026-08-02")

    assert digest.counts == {
        "papers": 1,
        "source_hits": 2,
        "selected": 1,
        "parsed": 1,
        "analyzed": 1,
        "failed": 0,
        "deduplicated": 1,
        "job_failures": 1,
        "featured": 1,
    }
    assert digest.source_counts == {"arxiv": 1, "openreview": 1}
    assert digest.topic_distribution == {"topic-serving": 1}
    assert digest.reading_routes == {
        "30_minutes": ["paper-contract"],
        "2_hours": ["paper-contract"],
        "half_day": ["paper-contract"],
    }
    assert digest.details["papers"] == [
        {"id": "paper-contract", "title": "Contract Paper", "status": "analyzed"}
    ]
    assert digest.details["deduplicated"] == [
        {
            "id": "paper-contract",
            "title": "Contract Paper",
            "source_hits": 2,
            "duplicate_hits": 1,
            "sources": ["arxiv", "openreview"],
        }
    ]
    assert digest.details["job_failures"][0]["id"] == "job-failed-contract"
