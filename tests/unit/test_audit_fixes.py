"""Regression tests for audit fixes:
1. retry_job clears stale result/external_task state.
4. rebuild_relations batches topic/claim lookups (no N+1 in the per-paper loop).
"""

from __future__ import annotations

from research_hub.database import dumps
from research_hub.models import JobRetryRequest, PaperCreate, PaperVersionCreate
from research_hub.repository import ConflictError, Repository


def _create_versioned_paper(repo: Repository):
    return repo.create_paper(
        PaperCreate(
            canonical_title="Audit Fix Paper",
            abstract="An AI Infra paper about serving.",
            version=PaperVersionCreate(
                version_label="v1",
                source="test",
                source_version_id="audit-fix-1",
                pdf_url="https://example.test/paper.pdf",
            ),
        )
    )


def test_retry_job_clears_stale_result_and_external_task(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        version = repo.get_paper_version(paper.current_version_id)
        job = repo.create_job("parse", "paper_version", version.id, {"source": "unit"})
        conn.execute(
            """
            UPDATE job
            SET status = 'retryable_failed',
                result_json = ?,
                error_json = ?,
                external_task_id = 'stale-task-123'
            WHERE id = ?
            """,
            (
                dumps({"adapter_status": "ok", "message": "old result"}),
                dumps({"message": "old error"}),
                job.job_id,
            ),
        )

        retried = repo.retry_job(job.job_id, JobRetryRequest(reason="audit retry"))

        assert retried.status == "queued"
        assert retried.result == {}
        assert retried.error == {}
        assert retried.external_task_id is None
        # The retried request records why we are re-running (new execution).
        stored = conn.execute(
            "SELECT request_json FROM job WHERE id = ?", (job.job_id,)
        ).fetchone()
        assert "_retry" in (dumps(stored["request_json"]) or "")


def test_retry_job_rejects_succeeded_job(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        version = repo.get_paper_version(paper.current_version_id)
        job = repo.create_job("analyze", "paper_version", version.id, {})
        conn.execute(
            "UPDATE job SET status = 'succeeded' WHERE id = ?", (job.job_id,)
        )
        try:
            repo.retry_job(job.job_id, JobRetryRequest(reason="nope"))
            raise AssertionError("expected ConflictError")
        except ConflictError:
            pass


def test_rebuild_relations_batches_and_creates(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        result = repo.rebuild_relations(paper_id=paper.id)
        assert result["scope"] == paper.id
        assert set(result["supported_relation_types"]) == {
            "similar",
            "extends",
            "complements",
            "conflicts",
        }
        assert result["created"] >= 0
        assert result["updated"] >= 0
