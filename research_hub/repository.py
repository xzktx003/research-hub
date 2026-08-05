"""Repository layer for Research Hub sqlite storage."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .database import dumps, loads
from .models import (
    ALLOWED_JOB_STATUS_TRANSITIONS,
    ALLOWED_PAPER_STATUS_TRANSITIONS,
    ALLOWED_PATENT_DRAFT_STATUS_TRANSITIONS,
    ALLOWED_PATENT_STAGE_STATUS_TRANSITIONS,
    Artifact,
    ArtifactCreate,
    Author,
    AsyncJobResponse,
    CandidateApproveRequest,
    CandidateComponent,
    DailyDigest,
    DiscoveryRun,
    DiscoveryRunCreate,
    DraftCreateRequest,
    EvidenceAnchor,
    InventionCandidate,
    InventionCandidateCreate,
    InventionSourceRef,
    IntegrationMechanismRecord,
    Job,
    JobCancelRequest,
    JobRetryRequest,
    Organization,
    Paper,
    PaperAuthorLink,
    PaperCreate,
    PaperDetail,
    PaperIdentifier,
    PaperRelation,
    PaperReport,
    PaperSelectRequest,
    PaperSourceHitCreate,
    PaperVersion,
    PaperVersionCreate,
    PaperVenueLink,
    PatentDraft,
    PatentDraftReviseRequest,
    PatentStageRun,
    PatentStageRunCreate,
    PatentStageRunUpdate,
    PATENT_STAGE_ORDER,
    PipelineRun,
    PipelineRunCreate,
    StatsResponse,
    Topic,
    TopicAlias,
    TopicConfigVersion,
    TopicCreate,
    TopicPatch,
    TopicQuota,
    Venue,
    VersionActionRequest,
)


class NotFoundError(Exception):
    """Raised when a requested entity is absent."""

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} not found: {entity_id}")
        self.entity = entity
        self.entity_id = entity_id


class ConflictError(Exception):
    """Raised when a request conflicts with an existing idempotency record."""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def next_poll_after() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=30)).replace(microsecond=0).isoformat()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def _normalized_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def normalized_paper_dedup_key(title: str, first_author: str = "", year: str = "") -> str:
    """Deterministic cross-source dedup key for a paper.

    Normalizes the title (lowercase, punctuation stripped, whitespace
    collapsed) and folds in the normalized first author and publication year so
    the same paper surfaced by different sources (arXiv / HuggingFace / manual)
    collapses to one key. Returns a sha1 hex digest.
    """
    clean_title = re.sub(r"[^a-z0-9 ]+", " ", title.lower().strip())
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    parts = [clean_title]
    if first_author:
        auth = re.sub(r"[^a-z0-9 ]+", " ", first_author.lower().strip())
        parts.append(re.sub(r"\s+", " ", auth).strip())
    if year:
        parts.append(re.sub(r"[^0-9]", "", str(year))[:4])
    for part in list(parts):
        if not part:
            parts.remove(part)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def validate_state_transition(
    entity: str,
    current_status: str,
    next_status: str,
    allowed: dict[str, set[str]],
) -> None:
    if current_status == next_status:
        return
    if next_status not in allowed.get(current_status, set()):
        raise ConflictError(
            f"Illegal {entity} status transition: {current_status} -> {next_status}"
        )


# A paper is hidden when it belongs to at least one deleted topic and does NOT
# belong to any active (non-deleted) topic. This keeps intact papers that share
# a remaining topic, and keeps normal untagged papers visible, while hiding
# papers that are only linked to topics the user has deleted.
_PAPER_HIDDEN_BY_DELETED_TOPIC = """NOT (
    EXISTS (
        SELECT 1 FROM paper_topic ht JOIN topic ht_t ON ht_t.id = ht.topic_id
        WHERE ht.paper_id = p.id AND ht_t.deleted_at IS NOT NULL
    )
    AND NOT EXISTS (
        SELECT 1 FROM paper_topic at JOIN topic at_t ON at_t.id = at.topic_id
        WHERE at.paper_id = p.id AND at_t.deleted_at IS NULL
    )
)"""


def row_to_paper(row: sqlite3.Row) -> Paper:
    return Paper(
        id=row["id"],
        canonical_title=row["canonical_title"],
        abstract=row["abstract"],
        translated_abstract=_row_value(row, "translated_abstract", None),
        method_summary=_row_value(row, "method_summary", None),
        language=row["language"],
        first_publication_date=row["first_publication_date"],
        current_version_id=row["current_version_id"],
        status=row["status"],
        selected=bool(row["selected"]),
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_version(row: sqlite3.Row) -> PaperVersion:
    return PaperVersion(
        id=row["id"],
        paper_id=row["paper_id"],
        version_label=row["version_label"],
        source=row["source"],
        source_version_id=row["source_version_id"],
        publication_date=row["publication_date"],
        pdf_url=row["pdf_url"],
        pdf_checksum=row["pdf_checksum"],
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        name_zh=row["name_zh"],
        name_en=row["name_en"],
        parent_id=row["parent_id"],
        enabled=bool(row["enabled"]),
        config_version_id=_row_value(row, "config_version_id", None),
        daily_quota=_row_value(row, "daily_quota", None),
        deleted_at=_row_value(row, "deleted_at", None),
        aliases=loads(row["aliases_json"], []),
        rules=loads(row["rules_json"], {}),
    )


def row_to_discovery(row: sqlite3.Row) -> DiscoveryRun:
    return DiscoveryRun(
        id=row["id"],
        source=row["source"],
        status=row["status"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        topics=loads(row["topics_json"], []),
        max_results=row["max_results"],
        metadata=loads(row["metadata_json"], {}),
        job_id=row["job_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_topic_config_version(row: sqlite3.Row) -> TopicConfigVersion:
    return TopicConfigVersion(
        id=row["id"],
        label=row["label"],
        active=bool(row["active"]),
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
    )


def row_to_topic_alias(row: sqlite3.Row) -> TopicAlias:
    return TopicAlias(
        id=row["id"],
        topic_id=row["topic_id"],
        config_version_id=row["config_version_id"],
        alias=row["alias"],
        alias_type=row["alias_type"],
        weight=row["weight"],
        created_at=row["created_at"],
    )


def row_to_topic_quota(row: sqlite3.Row) -> TopicQuota:
    return TopicQuota(
        id=row["id"],
        topic_id=row["topic_id"],
        config_version_id=row["config_version_id"],
        quota_type=row["quota_type"],
        max_results=row["max_results"],
        priority=row["priority"],
        created_at=row["created_at"],
    )


def row_to_pipeline_run(row: sqlite3.Row) -> PipelineRun:
    return PipelineRun(
        id=row["id"],
        run_type=row["run_type"],
        source=row["source"],
        status=row["status"],
        config_version_id=row["config_version_id"],
        discovery_run_id=row["discovery_run_id"],
        job_id=row["job_id"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        input_counts=loads(row["input_counts_json"], {}),
        output_counts=loads(row["output_counts_json"], {}),
        error_counts=loads(row["error_counts_json"], {}),
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        id=row["id"],
        paper_version_id=row["paper_version_id"],
        patent_draft_id=row["patent_draft_id"],
        artifact_type=row["artifact_type"],
        uri=row["uri"],
        media_type=row["media_type"],
        checksum=row["checksum"],
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
    )


def row_to_report(row: sqlite3.Row) -> PaperReport:
    return PaperReport(
        id=row["id"],
        paper_version_id=row["paper_version_id"],
        summary=row["summary"],
        motivation=row["motivation"],
        method=row["method"],
        experiments=row["experiments"],
        results=row["results"],
        innovation=row["innovation"],
        limitations=row["limitations"],
        engineering_value=row["engineering_value"],
        reproduction_plan=row["reproduction_plan"],
        score=loads(row["score_json"], {}),
        evidence=loads(row["evidence_json"], []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_relation(row: sqlite3.Row) -> PaperRelation:
    return PaperRelation(
        id=row["id"],
        from_paper_id=row["from_paper_id"],
        to_paper_id=row["to_paper_id"],
        relation_type=row["relation_type"],
        reason=row["reason"],
        evidence=loads(row["evidence_json"], []),
        confidence=row["confidence"],
        created_at=row["created_at"],
    )


def row_to_candidate(row: sqlite3.Row) -> InventionCandidate:
    return InventionCandidate(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        sources=[InventionSourceRef(**item) for item in loads(row["source_refs_json"], [])],
        problem_statement=row["problem_statement"],
        integration_mechanism=row["integration_mechanism"],
        coupling_interface=_row_value(row, "coupling_interface", ""),
        data_or_control_flow=_row_value(row, "data_or_control_flow", ""),
        why_not_juxtaposition=_row_value(row, "why_not_juxtaposition", ""),
        expected_joint_effect=_row_value(row, "expected_joint_effect", ""),
        technical_effects=row["technical_effects"],
        risk_notes=row["risk_notes"],
        evidence=loads(row["evidence_json"], []),
        gate=loads(row["gate_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        idempotency_key=row["idempotency_key"],
        external_task_id=row["external_task_id"],
        request=loads(row["request_json"], {}),
        result=loads(row["result_json"], {}),
        error=loads(row["error_json"], {}),
        next_poll_after=row["next_poll_after"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_draft(row: sqlite3.Row) -> PatentDraft:
    return PatentDraft(
        id=row["id"],
        invention_candidate_id=row["invention_candidate_id"],
        case_name=row["case_name"],
        version_label=row["version_label"],
        status=row["status"],
        markdown=row["markdown"],
        self_check=loads(row["self_check_json"], {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_patent_stage_run(row: sqlite3.Row) -> PatentStageRun:
    return PatentStageRun(
        id=row["id"],
        invention_candidate_id=row["invention_candidate_id"],
        patent_draft_id=row["patent_draft_id"],
        stage=row["stage"],
        status=row["status"],
        input=loads(row["input_json"], {}),
        output=loads(row["output_json"], {}),
        artifact_id=row["artifact_id"],
        job_id=row["job_id"],
        idempotency_key=row["idempotency_key"],
        metadata=loads(row["metadata_json"], {}),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_author(row: sqlite3.Row) -> Author:
    return Author(
        id=row["id"],
        display_name=row["display_name"],
        normalized_name=row["normalized_name"],
        orcid=row["orcid"],
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
    )


def row_to_organization(row: sqlite3.Row) -> Organization:
    return Organization(
        id=row["id"],
        display_name=row["display_name"],
        normalized_name=row["normalized_name"],
        ror_id=row["ror_id"],
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
    )


def row_to_venue(row: sqlite3.Row) -> Venue:
    return Venue(
        id=row["id"],
        display_name=row["display_name"],
        normalized_name=row["normalized_name"],
        venue_type=row["venue_type"],
        metadata=loads(row["metadata_json"], {}),
        created_at=row["created_at"],
    )


def row_to_paper_author_link(row: sqlite3.Row) -> PaperAuthorLink:
    return PaperAuthorLink(
        paper_id=row["paper_id"],
        author_id=row["author_id"],
        author_order=row["author_order"],
        is_corresponding=bool(row["is_corresponding"]),
        affiliation_text=row["affiliation_text"],
        created_at=row["created_at"],
    )


def row_to_paper_venue_link(row: sqlite3.Row) -> PaperVenueLink:
    return PaperVenueLink(
        paper_id=row["paper_id"],
        venue_id=row["venue_id"],
        relation_type=row["relation_type"],
        created_at=row["created_at"],
    )


def row_to_candidate_component(row: sqlite3.Row) -> CandidateComponent:
    return CandidateComponent(
        id=row["id"],
        invention_candidate_id=row["invention_candidate_id"],
        source_ref_index=row["source_ref_index"],
        component_type=row["component_type"],
        name=row["name"],
        contribution=row["contribution"],
        evidence=[EvidenceAnchor(**item) for item in loads(row["evidence_json"], [])],
        created_at=row["created_at"],
    )


def row_to_integration_mechanism(row: sqlite3.Row) -> IntegrationMechanismRecord:
    return IntegrationMechanismRecord(
        id=row["id"],
        invention_candidate_id=row["invention_candidate_id"],
        mechanism_type=row["mechanism_type"],
        coupling_interface=row["coupling_interface"],
        data_or_control_flow=row["data_or_control_flow"],
        why_not_juxtaposition=row["why_not_juxtaposition"],
        expected_joint_effect=row["expected_joint_effect"],
        evidence=[EvidenceAnchor(**item) for item in loads(row["evidence_json"], [])],
        created_at=row["created_at"],
    )


def _row_value(row: sqlite3.Row, key: str, default: Any) -> Any:
    return row[key] if key in row.keys() else default


PATENT_CANDIDATE_FACT_FIELDS = {
    "problem_statement": "technical problem",
    "integration_mechanism": "integration mechanism",
    "coupling_interface": "coupling interface",
    "data_or_control_flow": "data/control flow",
    "why_not_juxtaposition": "non-juxtaposition rationale",
    "expected_joint_effect": "expected joint effect",
    "technical_effects": "technical effects",
}

JUXTAPOSITION_TERMS = (
    "并列",
    "拼接",
    "组合",
    "简单组合",
    "简单拼接",
    "juxtaposition",
    "side by side",
    "aggregate",
    "aggregation",
    "combine only",
)


def _text_present(value: str, *, min_length: int = 8) -> bool:
    return len(value.strip()) >= min_length


def _anchor_fields(anchor: Any) -> set[str]:
    if hasattr(anchor, "model_dump"):
        data = anchor.model_dump(mode="json")
    elif isinstance(anchor, dict):
        data = anchor
    else:
        return set()
    fields = {
        str(data.get("report_field") or "").strip(),
        str(data.get("section") or "").strip(),
    }
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        fields.add(str(metadata.get("field") or "").strip())
        fields.add(str(metadata.get("report_field") or "").strip())
    return {field for field in fields if field}


def _anchor_kind(anchor: Any) -> str:
    if hasattr(anchor, "kind"):
        return str(anchor.kind)
    if isinstance(anchor, dict):
        return str(anchor.get("kind") or "")
    return ""


def _anchor_source(anchor: Any) -> str:
    if hasattr(anchor, "source"):
        return str(anchor.source)
    if isinstance(anchor, dict):
        return str(anchor.get("source") or "")
    return ""


def _anchor_note(anchor: Any) -> str:
    if hasattr(anchor, "note"):
        return str(anchor.note)
    if isinstance(anchor, dict):
        return str(anchor.get("note") or "")
    return ""


def validate_patent_candidate_gate(data: InventionCandidateCreate) -> None:
    missing_fields = [
        name
        for name in PATENT_CANDIDATE_FACT_FIELDS
        if not _text_present(str(getattr(data, name)))
    ]
    if missing_fields:
        raise ConflictError(
            "Candidate requires structured coupling fields with concrete content: "
            + ", ".join(missing_fields)
        )

    lower_mechanism = data.integration_mechanism.lower()
    lower_coupling = data.coupling_interface.lower()
    lower_flow = data.data_or_control_flow.lower()
    lower_non_juxtaposition = data.why_not_juxtaposition.lower()
    if (
        any(term in lower_mechanism for term in JUXTAPOSITION_TERMS)
        and not any(token in lower_coupling for token in ("接口", "协议", "反馈", "控制", "触发", "interface", "protocol", "feedback", "control", "trigger"))
        and not any(token in lower_flow for token in ("流", "信号", "状态", "数据", "控制", "flow", "signal", "state", "data", "control"))
    ):
        raise ConflictError(
            "Candidate rejected as mechanical aggregation: structured coupling must describe an interface and data/control flow"
        )
    if any(term in lower_non_juxtaposition for term in ("简单拼接", "仅拼接", "仅并列", "only juxtaposition")):
        raise ConflictError("Candidate rejected as mere juxtaposition")

    fields_with_evidence: set[str] = set()
    source_evidence_count = 0
    hypothesis_fields: set[str] = set()
    for anchor in data.evidence:
        kind = _anchor_kind(anchor)
        if kind not in {"fact", "hypothesis"}:
            continue
        fields = _anchor_fields(anchor)
        fields_with_evidence.update(fields)
        if kind == "hypothesis":
            hypothesis_fields.update(fields)
        if _anchor_source(anchor).startswith(("paper:", "paper_version:", "source:")):
            source_evidence_count += 1

    missing_evidence = [
        name
        for name in PATENT_CANDIDATE_FACT_FIELDS
        if name not in fields_with_evidence
    ]
    if missing_evidence:
        raise ConflictError(
            "Candidate requires 100% fact-level provenance coverage for: "
            + ", ".join(missing_evidence)
        )
    if source_evidence_count < len(data.sources):
        raise ConflictError("Candidate requires fact-level provenance for every source contribution")
    if "expected_joint_effect" not in hypothesis_fields and "technical_effects" not in hypothesis_fields:
        raise ConflictError("Unverified technical effects must be marked with hypothesis provenance")


class Repository:
    """SQLite-backed storage operations for API routes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.last_idempotency_replayed = False
        self.last_job_created = False

    def use_idempotency(
        self,
        key: str | None,
        method: str,
        path: str,
        body: Any,
        creator: Callable[[], tuple[int, dict[str, Any]]],
    ) -> tuple[int, dict[str, Any]]:
        self.last_idempotency_replayed = False
        if not key:
            return creator()

        body_hash = stable_hash(body)
        row = self.conn.execute(
            """
            SELECT method, path, body_hash, status_code, response_json
            FROM idempotency_record
            WHERE key = ?
            """,
            (key,),
        ).fetchone()
        if row:
            if row["method"] != method or row["path"] != path:
                raise ConflictError("Idempotency-Key was reused for a different API route")
            if row["body_hash"] != body_hash:
                raise ConflictError("Idempotency-Key was reused with a different request body")
            self.last_idempotency_replayed = True
            return row["status_code"], loads(row["response_json"], {})

        status_code, response = creator()
        self.conn.execute(
            """
            INSERT INTO idempotency_record (key, method, path, body_hash, status_code, response_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, method, path, body_hash, status_code, dumps(response)),
        )
        return status_code, response

    def create_job(
        self,
        kind: str,
        target_type: str,
        target_id: str,
        request: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> AsyncJobResponse:
        self.last_job_created = False
        existing = None
        if idempotency_key:
            existing = self.conn.execute(
                """
                SELECT * FROM job
                WHERE kind = ? AND target_type = ? AND target_id = ? AND idempotency_key = ?
                """,
                (kind, target_type, target_id, idempotency_key),
            ).fetchone()
        if existing:
            if loads(existing["request_json"], {}) != request:
                raise ConflictError(
                    "Idempotency-Key was reused with a different asynchronous request body"
                )
            return AsyncJobResponse(
                job_id=existing["id"],
                status=existing["status"],
                next_poll_after=existing["next_poll_after"],
                result=loads(existing["result_json"], {}),
            )

        job_id = new_id("job")
        poll = next_poll_after()
        self.conn.execute(
            """
            INSERT INTO job (
                id, kind, status, target_type, target_id, idempotency_key,
                request_json, next_poll_after
            )
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (job_id, kind, target_type, target_id, idempotency_key, dumps(request), poll),
        )
        self.last_job_created = True
        return AsyncJobResponse(job_id=job_id, status="queued", next_poll_after=poll)

    def list_jobs(
        self,
        status: str | None = None,
        kind: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 300,
        offset: int = 0,
    ) -> list[Job]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if target_type:
            clauses.append("target_type = ?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM job {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [row_to_job(row) for row in rows]

    def count_jobs(
        self,
        status: str | None = None,
        kind: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if target_type:
            clauses.append("target_type = ?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM job {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def get_job(self, job_id: str) -> Job:
        row = self.conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise NotFoundError("job", job_id)
        return row_to_job(row)

    def retry_job(self, job_id: str, request: JobRetryRequest) -> Job:
        job = self.get_job(job_id)
        if job.status not in {
            "retryable_failed",
            "terminal_failed",
            "cancelled",
            # A discovery run may complete only partially (e.g. some topics
            # failed while others succeeded). Operators still need to be able
            # to re-trigger/re-run such a run, so treat it as retryable too.
            "partial_succeeded",
        }:
            raise ConflictError(f"Job {job_id} is {job.status} and cannot be retried")
        retry_note = {"retry_reason": request.reason, "retried_from": job.status}
        merged_request = {**job.request, "_retry": retry_note}
        # A retry is a brand-new execution instance: clear the previous run's
        # result/error/external-task linkage so the next run starts clean and
        # never reuses a stale MinerU task id or re-reports stale output.
        self.conn.execute(
            """
            UPDATE job
            SET status = 'queued', request_json = ?, result_json = '{}',
                error_json = '{}', external_task_id = NULL,
                next_poll_after = ?, updated_at = ?
            WHERE id = ?
            """,
            (dumps(merged_request), next_poll_after(), utcnow(), job_id),
        )
        return self.get_job(job_id)

    def cancel_job(self, job_id: str, request: JobCancelRequest) -> Job:
        job = self.get_job(job_id)
        if job.status in {"succeeded", "terminal_failed", "cancelled"}:
            raise ConflictError(f"Job {job_id} is {job.status} and cannot be cancelled")
        self.conn.execute(
            """
            UPDATE job
            SET status = 'cancelled', error_json = ?, next_poll_after = NULL, updated_at = ?
            WHERE id = ?
            """,
            (dumps({"cancel_reason": request.reason}), utcnow(), job_id),
        )
        self.conn.execute(
            """
            UPDATE job_attempt
            SET status = 'cancelled', error_json = ?, completed_at = ?
            WHERE id = (
                SELECT id FROM job_attempt
                WHERE job_id = ? AND completed_at IS NULL
                ORDER BY attempt_no DESC LIMIT 1
            )
            """,
            (dumps({"cancel_reason": request.reason}), utcnow(), job_id),
        )
        return self.get_job(job_id)

    def list_topics(self) -> list[Topic]:
        rows = self.conn.execute(
            "SELECT * FROM topic WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        return [row_to_topic(row) for row in rows]

    def create_topic(self, data: TopicCreate) -> Topic:
        topic_id = new_id("topic")
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO topic (
                id, name_zh, name_en, parent_id, enabled, config_version_id,
                daily_quota, aliases_json, rules_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                data.name_zh,
                data.name_en or data.name_zh,
                data.parent_id,
                1 if data.enabled else 0,
                data.daily_quota,
                dumps(data.aliases),
                dumps(data.rules),
                now,
                now,
            ),
        )
        for alias in data.aliases:
            self.add_topic_alias(topic_id, alias)
        return row_to_topic(
            self.conn.execute("SELECT * FROM topic WHERE id = ?", (topic_id,)).fetchone()
        )

    def patch_topic(self, topic_id: str, patch: TopicPatch) -> Topic:
        row = self.conn.execute("SELECT * FROM topic WHERE id = ?", (topic_id,)).fetchone()
        if not row:
            raise NotFoundError("topic", topic_id)
        current = row_to_topic(row)
        next_topic = Topic(
            id=current.id,
            name_zh=patch.name_zh if patch.name_zh is not None else current.name_zh,
            name_en=patch.name_en if patch.name_en is not None else current.name_en,
            parent_id=patch.parent_id if patch.parent_id is not None else current.parent_id,
            enabled=patch.enabled if patch.enabled is not None else current.enabled,
            config_version_id=(
                patch.config_version_id
                if patch.config_version_id is not None
                else current.config_version_id
            ),
            daily_quota=patch.daily_quota if patch.daily_quota is not None else current.daily_quota,
            aliases=patch.aliases if patch.aliases is not None else current.aliases,
            rules=patch.rules if patch.rules is not None else current.rules,
        )
        self.conn.execute(
            """
            UPDATE topic
            SET name_zh = ?, name_en = ?, parent_id = ?, enabled = ?,
                config_version_id = ?, daily_quota = ?,
                aliases_json = ?, rules_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_topic.name_zh,
                next_topic.name_en,
                next_topic.parent_id,
                1 if next_topic.enabled else 0,
                next_topic.config_version_id,
                next_topic.daily_quota,
                dumps(next_topic.aliases),
                dumps(next_topic.rules),
                utcnow(),
                topic_id,
            ),
        )
        return row_to_topic(self.conn.execute("SELECT * FROM topic WHERE id = ?", (topic_id,)).fetchone())

    def delete_topic(self, topic_id: str) -> None:
        """Soft-delete a topic: mark it deleted but keep its paper_topic
        associations so papers that belong only to this topic can still be
        identified and hidden from the UI."""
        row = self.conn.execute("SELECT * FROM topic WHERE id = ?", (topic_id,)).fetchone()
        if not row:
            raise NotFoundError("topic", topic_id)
        self.conn.execute(
            "UPDATE topic SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (utcnow(), utcnow(), topic_id),
        )

    def get_topic_digest_note(self, topic_id: str, date_value: str) -> dict[str, Any]:
        """Return the editable user note attached to a topic's daily digest."""
        self._ensure_topic_exists(topic_id)
        row = self.conn.execute(
            "SELECT body, updated_at FROM topic_digest_note WHERE topic_id = ? AND date_value = ?",
            (topic_id, date_value),
        ).fetchone()
        if not row:
            return {"topic_id": topic_id, "date_value": date_value, "body": "", "updated_at": None}
        return {
            "topic_id": topic_id,
            "date_value": date_value,
            "body": row["body"],
            "updated_at": row["updated_at"],
        }

    def set_topic_digest_note(self, topic_id: str, date_value: str, body: str) -> dict[str, Any]:
        """Persist the editable user note attached to a topic's daily digest."""
        self._ensure_topic_exists(topic_id)
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO topic_digest_note (topic_id, date_value, body, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(topic_id, date_value) DO UPDATE SET
                body = excluded.body,
                updated_at = excluded.updated_at
            """,
            (topic_id, date_value, body, now),
        )
        return {
            "topic_id": topic_id,
            "date_value": date_value,
            "body": body,
            "updated_at": now,
        }

    def create_topic_config_version(
        self,
        label: str,
        *,
        active: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> TopicConfigVersion:
        config_id = new_id("tcfg")
        self.conn.execute(
            """
            INSERT INTO topic_config_version (id, label, active, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (config_id, label, 1 if active else 0, dumps(metadata or {})),
        )
        return row_to_topic_config_version(
            self.conn.execute(
                "SELECT * FROM topic_config_version WHERE id = ?",
                (config_id,),
            ).fetchone()
        )

    def add_topic_alias(
        self,
        topic_id: str,
        alias: str,
        *,
        alias_type: str = "include",
        config_version_id: str | None = None,
        weight: float = 1.0,
    ) -> TopicAlias:
        self._ensure_topic_exists(topic_id)
        if alias_type not in {"include", "exclude", "synonym"}:
            raise ConflictError(f"Unsupported topic alias_type: {alias_type}")
        alias_id = new_id("talias")
        self.conn.execute(
            """
            INSERT INTO topic_alias (
                id, topic_id, config_version_id, alias, alias_type, weight
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id, config_version_id, alias, alias_type) DO NOTHING
            """,
            (alias_id, topic_id, config_version_id, alias, alias_type, weight),
        )
        row = self.conn.execute(
            """
            SELECT * FROM topic_alias
            WHERE topic_id = ? AND alias = ? AND alias_type = ?
              AND (config_version_id IS ? OR config_version_id = ?)
            """,
            (topic_id, alias, alias_type, config_version_id, config_version_id),
        ).fetchone()
        if not row:
            raise NotFoundError("topic_alias", alias_id)
        return row_to_topic_alias(row)

    def set_topic_quota(
        self,
        topic_id: str,
        max_results: int,
        *,
        quota_type: str = "daily",
        config_version_id: str | None = None,
        priority: int = 50,
    ) -> TopicQuota:
        self._ensure_topic_exists(topic_id)
        if max_results < 1:
            raise ConflictError("Topic quota max_results must be positive")
        quota_id = new_id("tquota")
        self.conn.execute(
            """
            INSERT INTO topic_quota (
                id, topic_id, config_version_id, quota_type, max_results, priority
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(topic_id, config_version_id, quota_type) DO UPDATE SET
                max_results = excluded.max_results,
                priority = excluded.priority
            """,
            (quota_id, topic_id, config_version_id, quota_type, max_results, priority),
        )
        row = self.conn.execute(
            """
            SELECT * FROM topic_quota
            WHERE topic_id = ? AND quota_type = ?
              AND (config_version_id IS ? OR config_version_id = ?)
            """,
            (topic_id, quota_type, config_version_id, config_version_id),
        ).fetchone()
        if not row:
            raise NotFoundError("topic_quota", quota_id)
        return row_to_topic_quota(row)

    def topic_search_contract(
        self,
        topic_id: str,
        *,
        config_version_id: str | None = None,
    ) -> dict[str, Any]:
        topic = row_to_topic(
            self._topic_row(topic_id)
        )
        alias_rows = self.conn.execute(
            """
            SELECT * FROM topic_alias
            WHERE topic_id = ? AND (config_version_id IS ? OR config_version_id = ?)
            ORDER BY alias_type, weight DESC, alias
            """,
            (topic_id, config_version_id, config_version_id),
        ).fetchall()
        aliases = [row_to_topic_alias(row) for row in alias_rows]
        quota_row = self.conn.execute(
            """
            SELECT * FROM topic_quota
            WHERE topic_id = ? AND quota_type = 'daily'
              AND (config_version_id IS ? OR config_version_id = ?)
            ORDER BY priority DESC LIMIT 1
            """,
            (topic_id, config_version_id, config_version_id),
        ).fetchone()
        include_terms = [
            item.alias for item in aliases if item.alias_type in {"include", "synonym"}
        ] or topic.aliases or [topic.name_en]
        exclude_terms = [item.alias for item in aliases if item.alias_type == "exclude"]
        rules = dict(topic.rules)
        return {
            "topic_id": topic.id,
            "config_version_id": config_version_id or topic.config_version_id,
            "include_terms": include_terms,
            "exclude_terms": exclude_terms,
            "daily_quota": (
                row_to_topic_quota(quota_row).max_results
                if quota_row
                else topic.daily_quota or int(rules.get("daily_quota") or 25)
            ),
            "priority": row_to_topic_quota(quota_row).priority if quota_row else rules.get("priority"),
        }

    def _topic_row(self, topic_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM topic WHERE id = ?", (topic_id,)).fetchone()
        if not row:
            raise NotFoundError("topic", topic_id)
        return row

    def _ensure_topic_exists(self, topic_id: str) -> None:
        row = self._topic_row(topic_id)
        if row["deleted_at"] is not None:
            raise NotFoundError("topic", topic_id)

    def create_discovery_run(self, data: DiscoveryRunCreate, idempotency_key: str | None) -> DiscoveryRun:
        run_key = stable_hash(
            {
                "source": data.source,
                "window_start": data.window_start.isoformat() if data.window_start else None,
                "window_end": data.window_end.isoformat() if data.window_end else None,
                "topics": sorted(data.topics),
                "max_results": data.max_results,
            }
        )
        canonical = self.conn.execute(
            "SELECT id FROM discovery_run WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        if canonical:
            return self.get_discovery_run(canonical["id"])
        if idempotency_key:
            existing = self.conn.execute(
                """
                SELECT target_id FROM job
                WHERE kind = 'discover' AND target_type = 'discovery_run'
                  AND idempotency_key = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
            if existing:
                return self.get_discovery_run(existing["target_id"])
        run_id = new_id("drun")
        job = self.create_job("discover", "discovery_run", run_id, data.model_dump(mode="json"), idempotency_key)
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO discovery_run (
                id, source, status, window_start, window_end, topics_json,
                max_results, metadata_json, job_id, run_key, created_at, updated_at
            )
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                data.source,
                data.window_start.isoformat() if data.window_start else None,
                data.window_end.isoformat() if data.window_end else None,
                dumps(data.topics),
                data.max_results,
                dumps(data.metadata),
                job.job_id,
                run_key,
                now,
                now,
            ),
        )
        return self.get_discovery_run(run_id)

    def get_discovery_run(self, run_id: str) -> DiscoveryRun:
        row = self.conn.execute("SELECT * FROM discovery_run WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise NotFoundError("discovery_run", run_id)
        return row_to_discovery(row)

    def create_pipeline_run(self, data: PipelineRunCreate) -> PipelineRun:
        if data.discovery_run_id:
            self.get_discovery_run(data.discovery_run_id)
        if data.job_id:
            self.get_job(data.job_id)
        run_id = new_id("prun")
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO pipeline_run (
                id, run_type, source, status, config_version_id, discovery_run_id,
                job_id, window_start, window_end, input_counts_json,
                output_counts_json, error_counts_json, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                data.run_type,
                data.source,
                data.status,
                data.config_version_id,
                data.discovery_run_id,
                data.job_id,
                data.window_start.isoformat() if data.window_start else None,
                data.window_end.isoformat() if data.window_end else None,
                dumps(data.input_counts),
                dumps(data.output_counts),
                dumps(data.error_counts),
                dumps(data.metadata),
                now,
                now,
            ),
        )
        return self.get_pipeline_run(run_id)

    def get_pipeline_run(self, run_id: str) -> PipelineRun:
        row = self.conn.execute("SELECT * FROM pipeline_run WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise NotFoundError("pipeline_run", run_id)
        return row_to_pipeline_run(row)

    def update_pipeline_run_counts(
        self,
        run_id: str,
        *,
        status: str | None = None,
        input_counts: dict[str, Any] | None = None,
        output_counts: dict[str, Any] | None = None,
        error_counts: dict[str, Any] | None = None,
    ) -> PipelineRun:
        current = self.get_pipeline_run(run_id)
        self.conn.execute(
            """
            UPDATE pipeline_run
            SET status = ?, input_counts_json = ?, output_counts_json = ?,
                error_counts_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status or current.status,
                dumps(input_counts if input_counts is not None else current.input_counts),
                dumps(output_counts if output_counts is not None else current.output_counts),
                dumps(error_counts if error_counts is not None else current.error_counts),
                utcnow(),
                run_id,
            ),
        )
        return self.get_pipeline_run(run_id)

    def list_pipeline_runs(self, *, limit: int = 100) -> list[PipelineRun]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_run ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [row_to_pipeline_run(row) for row in rows]

    def list_papers(
        self,
        topic: str | None = None,
        date_value: str | None = None,
        publication_date_value: str | None = None,
        status: str | None = None,
        source: str | None = None,
        selected: bool | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Paper]:
        clauses: list[str] = []
        params: list[Any] = []
        if selected is not None:
            clauses.append("p.selected = ?")
            params.append(1 if selected else 0)
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        if date_value:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_source_hit hd WHERE hd.paper_id = p.id AND hd.hit_date = ?)"
            )
            params.append(date_value)
        if publication_date_value:
            clauses.append("p.first_publication_date = ?")
            params.append(publication_date_value)
        if topic:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_topic pt WHERE pt.paper_id = p.id AND pt.topic_id = ?)"
            )
            params.append(topic)
        if source:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_source_hit h WHERE h.paper_id = p.id AND h.source = ?)"
            )
            params.append(source)
        clauses.append(_PAPER_HIDDEN_BY_DELETED_TOPIC)
        where = f"WHERE {' AND '.join(f'({c})' for c in clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT p.* FROM paper p {where} ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [row_to_paper(row) for row in rows]

    def count_papers(
        self,
        topic: str | None = None,
        date_value: str | None = None,
        publication_date_value: str | None = None,
        status: str | None = None,
        source: str | None = None,
        selected: bool | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        if date_value:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_source_hit hd WHERE hd.paper_id = p.id AND hd.hit_date = ?)"
            )
            params.append(date_value)
        if publication_date_value:
            clauses.append("p.first_publication_date = ?")
            params.append(publication_date_value)
        if topic:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_topic pt WHERE pt.paper_id = p.id AND pt.topic_id = ?)"
            )
            params.append(topic)
        if source:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_source_hit h WHERE h.paper_id = p.id AND h.source = ?)"
            )
            params.append(source)
        clauses.append(_PAPER_HIDDEN_BY_DELETED_TOPIC)
        where = f"WHERE {' AND '.join(f'({c})' for c in clauses)}" if clauses else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM paper p {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def list_all_papers(
        self,
        topic: str | None = None,
        status: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Paper]:
        """List the full paper corpus across all dates, most-recent first.

        Used by the library view and historical search so papers are never lost
        when the user switches the selected discovery date.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        if topic:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_topic pt WHERE pt.paper_id = p.id AND pt.topic_id = ?)"
            )
            params.append(topic)
        clauses.append(_PAPER_HIDDEN_BY_DELETED_TOPIC)
        where = f"WHERE {' AND '.join(f'({c})' for c in clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT p.* FROM paper p {where} ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [row_to_paper(row) for row in rows]

    def count_all_papers(
        self,
        topic: str | None = None,
        status: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        if topic:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_topic pt WHERE pt.paper_id = p.id AND pt.topic_id = ?)"
            )
            params.append(topic)
        clauses.append(_PAPER_HIDDEN_BY_DELETED_TOPIC)
        where = f"WHERE {' AND '.join(f'({c})' for c in clauses)}" if clauses else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM paper p {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def search_papers(
        self,
        query: str,
        limit: int = 50,
    ) -> list[Paper]:
        """Full-text-style search across ALL stored papers (any date).

        Matches title, abstract, translated abstract, method summary and topic
        name across every date, so searching works against historical papers
        rather than only the currently selected day. Ordering prefers more
        recent papers.
        """
        q = (query or "").strip()
        if not q:
            return self.list_papers()[:limit]
        like = f"%{q}%"
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT p.*
            FROM paper p
            LEFT JOIN paper_topic pt ON pt.paper_id = p.id
            LEFT JOIN topic t ON t.id = pt.topic_id
            WHERE {_PAPER_HIDDEN_BY_DELETED_TOPIC}
              AND (
                   p.canonical_title LIKE ?
                OR p.abstract LIKE ?
                OR p.translated_abstract LIKE ?
                OR p.method_summary LIKE ?
                OR t.name_zh LIKE ?
                OR t.name_en LIKE ?
              )
            ORDER BY p.first_publication_date DESC, p.created_at DESC
            LIMIT ?
            """,
            (like, like, like, like, like, like, limit),
        ).fetchall()
        return [row_to_paper(row) for row in rows]

    def create_paper(self, data: PaperCreate) -> PaperDetail:
        found = self.find_paper_by_identifiers(data.identifiers)
        # Derive a cross-source title dedup key early so both the merge and
        # insert paths store it consistently.
        first_author = ""
        if data.metadata:
            authors = data.metadata.get("authors")
            if isinstance(authors, list) and authors:
                first_author = str(authors[0])
        year = str(data.first_publication_date)[:4] if data.first_publication_date else ""
        merged_metadata = dict(data.metadata or {})
        if data.canonical_title:
            merged_metadata["dedup_key"] = normalized_paper_dedup_key(
                data.canonical_title, first_author, year
            )
        if not found and data.canonical_title and (first_author or year):
            # Title-based dedup only engages when we have a strong extra
            # signal (first author and/or publication year) beyond the bare
            # title. Without one, two distinct papers may legitimately share a
            # title, so we do not collapse them.
            found = self.find_paper_by_normalized_title(
                data.canonical_title, first_author, year
            )
        if found:
            paper_id = found
            try:
                existing = loads(self.conn.execute(
                    "SELECT metadata_json FROM paper WHERE id = ?", (paper_id,)
                ).fetchone()["metadata_json"])
            except Exception:
                existing = {}
            has_dedup = bool((existing or {}).get("dedup_key"))
            if not has_dedup:
                existing = dict(existing or {})
                existing["dedup_key"] = merged_metadata.get("dedup_key")
            self.conn.execute(
                """
                UPDATE paper
                SET canonical_title = COALESCE(NULLIF(?, ''), canonical_title),
                    abstract = COALESCE(NULLIF(?, ''), abstract),
                    translated_abstract = CASE
                        WHEN NULLIF(?, '') IS NOT NULL AND NULLIF(?, '') <> abstract THEN NULL
                        ELSE translated_abstract
                    END,
                    metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    data.canonical_title,
                    data.abstract,
                    data.abstract,
                    data.abstract,
                    dumps(existing),
                    utcnow(),
                    paper_id,
                ),
            )
        else:
            paper_id = new_id("paper")
            now = utcnow()
            self.conn.execute(
                """
                INSERT INTO paper (
                    id, canonical_title, abstract, language, first_publication_date,
                    translated_abstract, status, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    data.canonical_title,
                    data.abstract,
                    data.language,
                    str(data.first_publication_date) if data.first_publication_date else None,
                    data.status,
                    dumps(merged_metadata),
                    now,
                    now,
                ),
            )

        for identifier in data.identifiers:
            self.add_identifier(paper_id, identifier)
        for topic_id in data.topics:
            self.add_topic(paper_id, topic_id, {"source": "api"})
        version_id = None
        if data.version:
            version = self.create_paper_version(paper_id, data.version)
            version_id = version.id
        if data.source_hit:
            self.add_source_hit(paper_id, version_id, data.source_hit)
        return self.get_paper(paper_id)

    def find_paper_by_identifiers(self, identifiers: list[PaperIdentifier]) -> str | None:
        for identifier in identifiers:
            row = self.conn.execute(
                """
                SELECT paper_id FROM paper_identifier
                WHERE identifier_type = ? AND identifier_value = ?
                """,
                (identifier.type, identifier.value),
            ).fetchone()
            if row:
                return row["paper_id"]
        return None

    def find_paper_by_normalized_title(
        self, title: str, first_author: str = "", year: str = ""
    ) -> str | None:
        """Cross-source dedup lookup using the normalized title key.

        Mirrors the identifier-based lookup so the same paper surfaced by
        different sources without a shared identifier still collapses to a
        single row. It matches when the fully-normalized title is identical
        AND the extra signal (first author and/or publication year) is either
        absent or itself matches, so two distinct papers that merely share a
        title are not collapsed when we cannot disambiguate them.
        """
        if not title:
            return None
        norm = re.sub(r"[^a-z0-9 ]+", " ", title.lower().strip())
        norm = re.sub(r"\s+", " ", norm).strip()
        if not norm:
            return None
        norm_author = ""
        if first_author:
            norm_author = re.sub(r"[^a-z0-9 ]+", " ", first_author.lower().strip())
            norm_author = re.sub(r"\s+", " ", norm_author).strip()
        norm_year = re.sub(r"[^0-9]", "", str(year))[:4] if year else ""
        papers = self.conn.execute(
            "SELECT id, canonical_title, first_publication_date "
            "FROM paper ORDER BY created_at ASC"
        ).fetchall()
        for paper in papers:
            cand = re.sub(r"[^a-z0-9 ]+", " ", str(paper["canonical_title"] or "").lower().strip())
            cand = re.sub(r"\s+", " ", cand).strip()
            if cand != norm:
                continue
            # Title matches. Now apply the disambiguation signals if provided.
            if norm_author or norm_year:
                # We cannot verify author from paper row here; require at least
                # that any provided year agrees, otherwise skip.
                if norm_year:
                    pub = str(paper["first_publication_date"] or "")
                    if pub[:4] and pub[:4] != norm_year:
                        continue
                return paper["id"]
            # No disambiguation signal available => fall back to exact title.
            return paper["id"]
        return None

    def add_identifier(self, paper_id: str, identifier: PaperIdentifier) -> None:
        self.conn.execute(
            """
            INSERT INTO paper_identifier (id, paper_id, identifier_type, identifier_value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(identifier_type, identifier_value) DO NOTHING
            """,
            (new_id("pid"), paper_id, identifier.type, identifier.value),
        )

    def add_topic(self, paper_id: str, topic_id: str, evidence: dict[str, Any]) -> None:
        topic = self.conn.execute("SELECT id FROM topic WHERE id = ?", (topic_id,)).fetchone()
        if not topic:
            raise NotFoundError("topic", topic_id)
        self.conn.execute(
            """
            INSERT INTO paper_topic (paper_id, topic_id, evidence_json)
            VALUES (?, ?, ?)
            ON CONFLICT(paper_id, topic_id) DO UPDATE SET evidence_json = excluded.evidence_json
            """,
            (paper_id, topic_id, dumps(evidence)),
        )

    def upsert_author(
        self,
        display_name: str,
        *,
        orcid: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Author:
        normalized = _normalized_name(display_name)
        author_id = new_id("author")
        self.conn.execute(
            """
            INSERT INTO author (id, display_name, normalized_name, orcid, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(normalized_name, orcid) DO UPDATE SET
                display_name = excluded.display_name,
                metadata_json = excluded.metadata_json
            """,
            (author_id, display_name, normalized, orcid, dumps(metadata or {})),
        )
        row = self.conn.execute(
            """
            SELECT * FROM author
            WHERE normalized_name = ? AND (orcid IS ? OR orcid = ?)
            """,
            (normalized, orcid, orcid),
        ).fetchone()
        if not row:
            raise NotFoundError("author", author_id)
        return row_to_author(row)

    def upsert_organization(
        self,
        display_name: str,
        *,
        ror_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Organization:
        normalized = _normalized_name(display_name)
        organization_id = new_id("org")
        self.conn.execute(
            """
            INSERT INTO organization (id, display_name, normalized_name, ror_id, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(normalized_name, ror_id) DO UPDATE SET
                display_name = excluded.display_name,
                metadata_json = excluded.metadata_json
            """,
            (organization_id, display_name, normalized, ror_id, dumps(metadata or {})),
        )
        row = self.conn.execute(
            """
            SELECT * FROM organization
            WHERE normalized_name = ? AND (ror_id IS ? OR ror_id = ?)
            """,
            (normalized, ror_id, ror_id),
        ).fetchone()
        if not row:
            raise NotFoundError("organization", organization_id)
        return row_to_organization(row)

    def upsert_venue(
        self,
        display_name: str,
        *,
        venue_type: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> Venue:
        normalized = _normalized_name(display_name)
        venue_id = new_id("venue")
        self.conn.execute(
            """
            INSERT INTO venue (id, display_name, normalized_name, venue_type, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(normalized_name, venue_type) DO UPDATE SET
                display_name = excluded.display_name,
                metadata_json = excluded.metadata_json
            """,
            (venue_id, display_name, normalized, venue_type, dumps(metadata or {})),
        )
        row = self.conn.execute(
            "SELECT * FROM venue WHERE normalized_name = ? AND venue_type = ?",
            (normalized, venue_type),
        ).fetchone()
        if not row:
            raise NotFoundError("venue", venue_id)
        return row_to_venue(row)

    def link_paper_author(
        self,
        paper_id: str,
        author_id: str,
        *,
        author_order: int = 0,
        is_corresponding: bool = False,
        affiliation_text: str = "",
    ) -> PaperAuthorLink:
        self.ensure_paper_exists(paper_id)
        if not self.conn.execute("SELECT 1 FROM author WHERE id = ?", (author_id,)).fetchone():
            raise NotFoundError("author", author_id)
        self.conn.execute(
            """
            INSERT INTO paper_author (
                paper_id, author_id, author_order, is_corresponding, affiliation_text
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(paper_id, author_id) DO UPDATE SET
                author_order = excluded.author_order,
                is_corresponding = excluded.is_corresponding,
                affiliation_text = excluded.affiliation_text
            """,
            (
                paper_id,
                author_id,
                author_order,
                1 if is_corresponding else 0,
                affiliation_text,
            ),
        )
        return row_to_paper_author_link(
            self.conn.execute(
                "SELECT * FROM paper_author WHERE paper_id = ? AND author_id = ?",
                (paper_id, author_id),
            ).fetchone()
        )

    def link_author_organization(
        self,
        author_id: str,
        organization_id: str,
        *,
        role: str = "affiliation",
    ) -> None:
        if not self.conn.execute("SELECT 1 FROM author WHERE id = ?", (author_id,)).fetchone():
            raise NotFoundError("author", author_id)
        if not self.conn.execute(
            "SELECT 1 FROM organization WHERE id = ?",
            (organization_id,),
        ).fetchone():
            raise NotFoundError("organization", organization_id)
        self.conn.execute(
            """
            INSERT INTO author_organization (author_id, organization_id, role)
            VALUES (?, ?, ?)
            ON CONFLICT(author_id, organization_id, role) DO NOTHING
            """,
            (author_id, organization_id, role),
        )

    def link_paper_venue(
        self,
        paper_id: str,
        venue_id: str,
        *,
        relation_type: str = "published_in",
    ) -> PaperVenueLink:
        self.ensure_paper_exists(paper_id)
        if not self.conn.execute("SELECT 1 FROM venue WHERE id = ?", (venue_id,)).fetchone():
            raise NotFoundError("venue", venue_id)
        self.conn.execute(
            """
            INSERT INTO paper_venue (paper_id, venue_id, relation_type)
            VALUES (?, ?, ?)
            ON CONFLICT(paper_id, venue_id, relation_type) DO NOTHING
            """,
            (paper_id, venue_id, relation_type),
        )
        return row_to_paper_venue_link(
            self.conn.execute(
                """
                SELECT * FROM paper_venue
                WHERE paper_id = ? AND venue_id = ? AND relation_type = ?
                """,
                (paper_id, venue_id, relation_type),
            ).fetchone()
        )

    def create_paper_version(self, paper_id: str, data: PaperVersionCreate) -> PaperVersion:
        self.ensure_paper_exists(paper_id)
        existing = self.conn.execute(
            """
            SELECT * FROM paper_version
            WHERE paper_id = ? AND version_label = ? AND source = ?
            """,
            (paper_id, data.version_label, data.source),
        ).fetchone()
        if existing:
            return row_to_version(existing)
        version_id = new_id("pver")
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO paper_version (
                id, paper_id, version_label, source, source_version_id,
                publication_date, pdf_url, pdf_checksum, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                paper_id,
                data.version_label,
                data.source,
                data.source_version_id,
                str(data.publication_date) if data.publication_date else None,
                data.pdf_url,
                data.pdf_checksum,
                dumps(data.metadata),
                now,
                now,
            ),
        )
        self.conn.execute(
            "UPDATE paper SET current_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now, paper_id),
        )
        return self.get_paper_version(version_id)

    def add_source_hit(
        self, paper_id: str, version_id: str | None, data: PaperSourceHitCreate
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO paper_source_hit (
                id, paper_id, paper_version_id, source, query, rank, hit_date, raw_summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id, source, query, hit_date) DO NOTHING
            """,
            (
                new_id("hit"),
                paper_id,
                version_id,
                data.source,
                data.query,
                data.rank,
                str(data.hit_date) if data.hit_date else None,
                dumps(data.raw_summary),
            ),
        )

    def ensure_paper_exists(self, paper_id: str) -> None:
        if not self.conn.execute("SELECT 1 FROM paper WHERE id = ?", (paper_id,)).fetchone():
            raise NotFoundError("paper", paper_id)

    def ensure_version_exists(self, version_id: str) -> None:
        if not self.conn.execute("SELECT 1 FROM paper_version WHERE id = ?", (version_id,)).fetchone():
            raise NotFoundError("paper_version", version_id)

    def get_paper(self, paper_id: str) -> PaperDetail:
        row = self.conn.execute("SELECT * FROM paper WHERE id = ?", (paper_id,)).fetchone()
        if not row:
            raise NotFoundError("paper", paper_id)
        paper = row_to_paper(row)
        identifier_rows = self.conn.execute(
            "SELECT identifier_type, identifier_value FROM paper_identifier WHERE paper_id = ?",
            (paper_id,),
        ).fetchall()
        topic_rows = self.conn.execute(
            """
            SELECT t.* FROM topic t
            JOIN paper_topic pt ON pt.topic_id = t.id
            WHERE pt.paper_id = ?
            ORDER BY t.id
            """,
            (paper_id,),
        ).fetchall()
        current_version = None
        if paper.current_version_id:
            version_row = self.conn.execute(
                "SELECT * FROM paper_version WHERE id = ?", (paper.current_version_id,)
            ).fetchone()
            if version_row:
                current_version = row_to_version(version_row)
        return PaperDetail(
            **paper.model_dump(),
            identifiers=[
                PaperIdentifier(type=item["identifier_type"], value=item["identifier_value"])
                for item in identifier_rows
            ],
            topics=[row_to_topic(item) for item in topic_rows],
            current_version=current_version,
        )

    def get_papers_detail(self, papers: list[Paper]) -> list[PaperDetail]:
        """Bulk version of `get_paper` for list endpoints.

        Loads identifiers, topics and current versions for a batch of papers
        using one query per relation instead of O(n) queries in `get_paper`,
        eliminating the N+1 pattern on paper listing/search endpoints.
        """
        if not papers:
            return []
        ids = [p.id for p in papers]
        paper_by_id = {p.id: p for p in papers}
        markers = ",".join("?" for _ in ids)

        identifier_rows = self.conn.execute(
            f"SELECT paper_id, identifier_type, identifier_value FROM paper_identifier WHERE paper_id IN ({markers})",
            ids,
        ).fetchall()
        identifiers: dict[str, list[PaperIdentifier]] = {pid: [] for pid in ids}
        for item in identifier_rows:
            identifiers.setdefault(item["paper_id"], []).append(
                PaperIdentifier(type=item["identifier_type"], value=item["identifier_value"])
            )

        topic_rows = self.conn.execute(
            f"""
            SELECT pt.paper_id, t.* FROM topic t
            JOIN paper_topic pt ON pt.topic_id = t.id
            WHERE pt.paper_id IN ({markers})
            ORDER BY t.id
            """,
            ids,
        ).fetchall()
        topics: dict[str, list[Any]] = {pid: [] for pid in ids}
        for item in topic_rows:
            topics.setdefault(item["paper_id"], []).append(row_to_topic(item))

        version_ids = [p.current_version_id for p in papers if p.current_version_id]
        versions: dict[str, Any] = {}
        if version_ids:
            vmarkers = ",".join("?" for _ in version_ids)
            version_rows = self.conn.execute(
                f"SELECT * FROM paper_version WHERE id IN ({vmarkers})",
                version_ids,
            ).fetchall()
            for row in version_rows:
                versions[row["id"]] = row_to_version(row)

        return [
            PaperDetail(
                **paper_by_id[pid].model_dump(),
                identifiers=identifiers.get(pid, []),
                topics=topics.get(pid, []),
                current_version=versions.get(paper_by_id[pid].current_version_id),
            )
            for pid in ids
        ]

    def get_paper_workspace(self, paper_id: str) -> dict[str, Any]:
        """Return the complete read-only paper workspace in one transaction."""

        paper = self.get_paper(paper_id)
        versions = self.list_versions(paper_id)
        artifacts: list[Artifact] = []
        report: PaperReport | None = None
        if paper.current_version_id:
            artifacts = self.list_version_artifacts(paper.current_version_id)
            try:
                report = self.get_version_report(paper.current_version_id)
            except NotFoundError:
                report = None
        return {
            "paper": paper.model_dump(mode="json"),
            "versions": [item.model_dump(mode="json") for item in versions],
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "report": report.model_dump(mode="json") if report else None,
            "technical_cards": self.technical_cards(paper_id),
            "relations": [item.model_dump(mode="json") for item in self.list_relations(paper_id)],
        }

    def technical_cards(self, paper_id: str) -> list[dict[str, Any]]:
        """Derive deterministic, evidence-labelled technical cards from a report.

        The card is a projection of persisted facts; it does not invent missing
        experimental results.  Empty report fields are represented explicitly.
        """

        paper = self.get_paper(paper_id)
        if not paper.current_version_id:
            return []
        try:
            report = self.get_version_report(paper.current_version_id)
        except NotFoundError:
            return []

        problem = report.motivation.strip() or _section_excerpt(report.summary, "问题")
        method = report.method.strip() or report.innovation.strip() or _section_excerpt(
            report.summary, "方法"
        )
        if not problem:
            problem = "论文报告未单独标注技术问题；请人工从原文核验。"
        if not method:
            method = "论文报告未单独标注技术方法；请人工从原文核验。"
        components = sorted(
            {
                alias
                for topic in paper.topics
                for alias in topic.aliases
                if alias.lower() in f"{paper.canonical_title} {report.summary}".lower()
            }
        )[:8]
        evidence = [
            {
                "kind": item.kind,
                "source": item.source or paper.current_version_id,
                "note": item.note or "研读报告中的可追溯证据",
                "section": item.section,
                "page": item.page,
                "quote": item.quote,
                "quote_hash": item.quote_hash,
            }
            for item in report.evidence
        ]
        if not evidence:
            evidence = [
                {
                    "kind": "fact",
                    "source": paper.current_version_id,
                    "note": "卡片来自当前论文版本的研读报告；关键主张仍需回看原文。",
                }
            ]
        return [
            {
                "id": f"tcard_{stable_hash([paper.id, paper.current_version_id])[:24]}",
                "paper_id": paper.id,
                "paper_version_id": paper.current_version_id,
                "title": paper.canonical_title,
                "technical_problem": problem,
                "method": method,
                "system_components": components,
                "metrics": report.results or "论文报告未提供独立指标字段",
                "risks": [report.limitations] if report.limitations else ["需核验实验边界与复现条件"],
                "evidence": evidence,
            }
        ]

    def list_versions(self, paper_id: str) -> list[PaperVersion]:
        self.ensure_paper_exists(paper_id)
        rows = self.conn.execute(
            "SELECT * FROM paper_version WHERE paper_id = ? ORDER BY created_at DESC",
            (paper_id,),
        ).fetchall()
        return [row_to_version(row) for row in rows]

    def get_paper_version(self, version_id: str) -> PaperVersion:
        row = self.conn.execute("SELECT * FROM paper_version WHERE id = ?", (version_id,)).fetchone()
        if not row:
            raise NotFoundError("paper_version", version_id)
        return row_to_version(row)

    def select_paper(self, paper_id: str, request: PaperSelectRequest) -> PaperDetail:
        self.ensure_paper_exists(paper_id)
        self.conn.execute(
            "UPDATE paper SET selected = ?, updated_at = ? WHERE id = ?",
            (1 if request.selected else 0, utcnow(), paper_id),
        )
        return self.get_paper(paper_id)

    def transition_paper_status(
        self,
        paper_id: str,
        next_status: str,
        *,
        reason: str = "",
    ) -> PaperDetail:
        paper = self.get_paper(paper_id)
        validate_state_transition(
            "paper",
            paper.status,
            next_status,
            ALLOWED_PAPER_STATUS_TRANSITIONS,
        )
        metadata = dict(paper.metadata)
        transitions = list(metadata.get("status_transitions") or [])
        transitions.append(
            {
                "from": paper.status,
                "to": next_status,
                "reason": reason,
                "at": utcnow(),
            }
        )
        metadata["status_transitions"] = transitions
        self.conn.execute(
            """
            UPDATE paper
            SET status = ?, metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, dumps(metadata), utcnow(), paper_id),
        )
        return self.get_paper(paper_id)

    def version_action(
        self, version_id: str, kind: str, request: VersionActionRequest, idempotency_key: str | None
    ) -> AsyncJobResponse:
        return self.create_job(
            kind,
            "paper_version",
            version_id,
            request.model_dump(mode="json"),
            idempotency_key,
        )

    def transition_job_status(
        self,
        job_id: str,
        next_status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> Job:
        job = self.get_job(job_id)
        validate_state_transition(
            "job",
            job.status,
            next_status,
            ALLOWED_JOB_STATUS_TRANSITIONS,
        )
        self.conn.execute(
            """
            UPDATE job
            SET status = ?, result_json = ?, error_json = ?, updated_at = ?,
                next_poll_after = CASE
                    WHEN ? IN ('queued', 'running', 'retryable_failed') THEN next_poll_after
                    ELSE NULL
                END
            WHERE id = ?
            """,
            (
                next_status,
                dumps(result if result is not None else job.result),
                dumps(error if error is not None else job.error),
                utcnow(),
                next_status,
                job_id,
            ),
        )
        return self.get_job(job_id)

    def create_artifact_for_version(self, version_id: str, data: ArtifactCreate) -> Artifact:
        self.ensure_version_exists(version_id)
        artifact_id = new_id("art")
        self.conn.execute(
            """
            INSERT INTO artifact (
                id, paper_version_id, artifact_type, uri, media_type, checksum, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                artifact_id,
                version_id,
                data.artifact_type,
                data.uri,
                data.media_type,
                data.checksum,
                dumps(data.metadata),
            ),
        )
        row = self.conn.execute(
            """
            SELECT * FROM artifact
            WHERE paper_version_id = ? AND patent_draft_id IS NULL AND artifact_type = ? AND uri = ?
            """,
            (version_id, data.artifact_type, data.uri),
        ).fetchone()
        if not row:
            raise NotFoundError("artifact", artifact_id)
        artifact = row_to_artifact(row)
        source_artifact_id = str(data.metadata.get("source_artifact_id") or "")
        if source_artifact_id and source_artifact_id != artifact.id:
            source = self.conn.execute(
                "SELECT id FROM artifact WHERE id = ?",
                (source_artifact_id,),
            ).fetchone()
            if source:
                self.conn.execute(
                    """
                    INSERT INTO artifact_relation (
                        id, source_artifact_id, derived_artifact_id,
                        relation_type, generator, metadata_json
                    ) VALUES (?, ?, ?, 'derived_from', ?, ?)
                    ON CONFLICT(source_artifact_id, derived_artifact_id, relation_type) DO NOTHING
                    """,
                    (
                        new_id("arel"),
                        source_artifact_id,
                        artifact.id,
                        str(data.metadata.get("source") or ""),
                        dumps(data.metadata.get("lineage") or {}),
                    ),
                )
        return artifact

    def list_version_artifacts(self, version_id: str) -> list[Artifact]:
        self.ensure_version_exists(version_id)
        rows = self.conn.execute(
            "SELECT * FROM artifact WHERE paper_version_id = ? ORDER BY created_at DESC",
            (version_id,),
        ).fetchall()
        return [row_to_artifact(row) for row in rows]

    def list_artifacts(
        self,
        paper_version_id: str | None = None,
        patent_draft_id: str | None = None,
        artifact_type: str | None = None,
        limit: int = 300,
        offset: int = 0,
    ) -> list[Artifact]:
        clauses: list[str] = []
        params: list[Any] = []
        if paper_version_id:
            clauses.append("paper_version_id = ?")
            params.append(paper_version_id)
        if patent_draft_id:
            clauses.append("patent_draft_id = ?")
            params.append(patent_draft_id)
        if artifact_type:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM artifact {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [row_to_artifact(row) for row in rows]

    def count_artifacts(
        self,
        paper_version_id: str | None = None,
        patent_draft_id: str | None = None,
        artifact_type: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if paper_version_id:
            clauses.append("paper_version_id = ?")
            params.append(paper_version_id)
        if patent_draft_id:
            clauses.append("patent_draft_id = ?")
            params.append(patent_draft_id)
        if artifact_type:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM artifact {where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def get_artifact(self, artifact_id: str) -> Artifact:
        row = self.conn.execute("SELECT * FROM artifact WHERE id = ?", (artifact_id,)).fetchone()
        if not row:
            raise NotFoundError("artifact", artifact_id)
        return row_to_artifact(row)

    def get_version_report(self, version_id: str) -> PaperReport:
        self.ensure_version_exists(version_id)
        row = self.conn.execute(
            "SELECT * FROM paper_report WHERE paper_version_id = ?", (version_id,)
        ).fetchone()
        if not row:
            raise NotFoundError("paper_report", version_id)
        return row_to_report(row)

    def daily_digest(self, date_value: str, topic_id: str | None = None) -> DailyDigest:
        if topic_id and not self.conn.execute(
            "SELECT 1 FROM topic WHERE id = ? AND deleted_at IS NULL", (topic_id,)
        ).fetchone():
            raise NotFoundError("topic", topic_id)
        topic_join = "JOIN paper_topic scope_topic ON scope_topic.paper_id = p.id" if topic_id else ""
        topic_clause = f"AND scope_topic.topic_id = ?" if topic_id else ""
        hidden_clause = (
            ""
            if topic_id
            else f"AND {_PAPER_HIDDEN_BY_DELETED_TOPIC}"
        )
        paper_params: tuple[Any, ...] = (date_value, topic_id) if topic_id else (date_value,)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT p.*
            FROM paper p
            JOIN paper_source_hit h ON h.paper_id = p.id
            {topic_join}
            WHERE h.hit_date = ?
            {topic_clause}
            {hidden_clause}
            ORDER BY p.created_at DESC
            """,
            paper_params,
        ).fetchall()
        papers = [row_to_paper(row) for row in rows]
        source_hit_query = """
            SELECT COUNT(*) AS n
            FROM paper_source_hit h
            JOIN paper p ON p.id = h.paper_id
        """
        if topic_id:
            source_hit_query += " JOIN paper_topic pt ON pt.paper_id = p.id"
        source_hit_query += " WHERE h.hit_date = ?"
        if not topic_id:
            source_hit_query += f" AND {_PAPER_HIDDEN_BY_DELETED_TOPIC}"
        if topic_id:
            source_hit_query += " AND pt.topic_id = ?"
        counts = {
            "papers": len(papers),
            "source_hits": int(
                self.conn.execute(source_hit_query, paper_params).fetchone()["n"]
            ),
            "selected": sum(1 for paper in papers if paper.selected),
            "parsed": sum(1 for paper in papers if paper.status in {"parsed", "analyzed", "scored", "published"}),
            "analyzed": sum(1 for paper in papers if paper.status in {"analyzed", "scored", "published"}),
            "failed": sum(1 for paper in papers if "failed" in paper.status),
        }
        counts["deduplicated"] = max(0, counts["source_hits"] - counts["papers"])
        counts["job_failures"] = int(
            self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM job
                                WHERE substr(CAST(created_at AS TEXT), 1, 10) = ?
                  AND status IN ('retryable_failed', 'terminal_failed')
                """,
                (date_value,),
            ).fetchone()["n"]
        )
        counts["featured"] = counts["selected"]
        scope_clause = (
            "AND EXISTS (SELECT 1 FROM paper_topic detail_pt "
            "WHERE detail_pt.paper_id = p.id AND detail_pt.topic_id = ?)"
            if topic_id
            else ""
        )
        detail_params: tuple[Any, ...] = (date_value, topic_id) if topic_id else (date_value,)
        detail_hidden_clause = (
            ""
            if topic_id
            else f"AND {_PAPER_HIDDEN_BY_DELETED_TOPIC}"
        )
        detail_hit_rows = self.conn.execute(
            f"""
            SELECT p.id, p.canonical_title, h.source
            FROM paper p
            JOIN paper_source_hit h ON h.paper_id = p.id
            WHERE h.hit_date = ?
            {scope_clause}
            {detail_hidden_clause}
            ORDER BY p.canonical_title, h.source
            """,
            detail_params,
        ).fetchall()
        duplicate_groups: dict[str, dict[str, Any]] = {}
        for row in detail_hit_rows:
            group = duplicate_groups.setdefault(
                row["id"],
                {
                    "id": row["id"],
                    "title": row["canonical_title"],
                    "source_hits": 0,
                    "sources": set(),
                },
            )
            group["source_hits"] += 1
            group["sources"].add(row["source"])
        failed_job_rows = self.conn.execute(
            """
            SELECT id, kind, status, target_type, target_id
            FROM job
            WHERE substr(CAST(created_at AS TEXT), 1, 10) = ?
              AND status IN ('retryable_failed', 'terminal_failed')
            ORDER BY created_at DESC
            """,
            (date_value,),
        ).fetchall()
        paper_details = [
            {
                "id": paper.id,
                "title": paper.canonical_title,
                "status": paper.status,
            }
            for paper in papers
        ]
        details = {
            "papers": paper_details,
            "deduplicated": [
                {
                    "id": group["id"],
                    "title": group["title"],
                    "source_hits": group["source_hits"],
                    "duplicate_hits": group["source_hits"] - 1,
                    "sources": sorted(group["sources"]),
                }
                for group in duplicate_groups.values()
                if group["source_hits"] > 1
            ],
            "parsed": [
                item
                for item, paper in zip(paper_details, papers)
                if paper.status in {"parsed", "analyzed", "scored", "published"}
            ],
            "analyzed": [
                item
                for item, paper in zip(paper_details, papers)
                if paper.status in {"analyzed", "scored", "published"}
            ],
            "job_failures": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "target_type": row["target_type"],
                    "target_id": row["target_id"],
                }
                for row in failed_job_rows
            ],
        }
        source_rows = self.conn.execute(
            f"""
            SELECT source, COUNT(*) AS n
            FROM paper_source_hit h
            JOIN paper p ON p.id = h.paper_id
            {"JOIN paper_topic pt ON pt.paper_id = p.id" if topic_id else ""}
            WHERE h.hit_date = ?
            {"AND pt.topic_id = ?" if topic_id else f"AND {_PAPER_HIDDEN_BY_DELETED_TOPIC}"}
            GROUP BY source ORDER BY source
            """,
            paper_params,
        ).fetchall()
        topic_rows = self.conn.execute(
            f"""
            SELECT pt.topic_id, COUNT(DISTINCT pt.paper_id) AS n
            FROM paper_topic pt
            JOIN topic t ON t.id = pt.topic_id
            JOIN paper_source_hit h ON h.paper_id = pt.paper_id
            JOIN paper p ON p.id = pt.paper_id
            WHERE h.hit_date = ?
            AND t.deleted_at IS NULL
            {f"AND pt.topic_id = ?" if topic_id else f"AND {_PAPER_HIDDEN_BY_DELETED_TOPIC}"}
            GROUP BY pt.topic_id ORDER BY pt.topic_id
            """,
            paper_params,
        ).fetchall()
        ranked = sorted(
            papers,
            key=lambda paper: (
                not paper.selected,
                paper.status not in {"analyzed", "scored", "published"},
                paper.canonical_title,
            ),
        )
        return DailyDigest(
            date=date_value,
            counts=counts,
            details=details,
            source_counts={row["source"]: int(row["n"]) for row in source_rows},
            topic_distribution={row["topic_id"]: int(row["n"]) for row in topic_rows},
            reading_routes={
                "30_minutes": [paper.id for paper in ranked[:3]],
                "2_hours": [paper.id for paper in ranked[:8]],
                "half_day": [paper.id for paper in ranked[:15]],
            },
            papers=papers,
        )

    def list_relations(self, paper_id: str) -> list[PaperRelation]:
        self.ensure_paper_exists(paper_id)
        rows = self.conn.execute(
            """
            SELECT * FROM paper_relation
            WHERE from_paper_id = ? OR to_paper_id = ?
            ORDER BY confidence DESC, created_at DESC
            """,
            (paper_id, paper_id),
        ).fetchall()
        return [row_to_relation(row) for row in rows]

    def list_all_relations(self) -> list[dict[str, Any]]:
        """Return every persisted relation enriched with both endpoint paper
        titles, so the relationship view can render a full graph without
        lazily opening each paper's workspace."""
        rows = self.conn.execute(
            """
            SELECT r.id, r.from_paper_id, r.to_paper_id, r.relation_type,
                   r.reason, r.evidence_json, r.confidence, r.created_at,
                   fp.canonical_title AS from_title,
                   tp.canonical_title AS to_title
            FROM paper_relation r
            JOIN paper fp ON fp.id = r.from_paper_id
            JOIN paper tp ON tp.id = r.to_paper_id
            ORDER BY r.confidence DESC, r.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "from_paper_id": row["from_paper_id"],
                "to_paper_id": row["to_paper_id"],
                "from_title": row["from_title"],
                "to_title": row["to_title"],
                "relation_type": row["relation_type"],
                "reason": row["reason"],
                "evidence": loads(row["evidence_json"], []),
                "confidence": row["confidence"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def rebuild_relations(self, paper_id: str | None = None) -> dict[str, Any]:
        """Create an explainable deterministic relation baseline.

        It deliberately favours transparent topic/keyword evidence over opaque
        vector similarity.  A later model may enrich explanations but must not
        replace these persisted provenance anchors.
        """

        if paper_id:
            self.ensure_paper_exists(paper_id)
        rows = self.conn.execute(
            """
            SELECT p.id, p.canonical_title, p.abstract, p.first_publication_date,
                   p.current_version_id, COALESCE(r.summary, '') AS report_summary,
                   COALESCE(r.method, '') AS report_method,
                   COALESCE(r.limitations, '') AS report_limitations
            FROM paper p
            LEFT JOIN paper_report r ON r.paper_version_id = p.current_version_id
            ORDER BY p.id
            """
        ).fetchall()
        records: list[dict[str, Any]] = []
        # Batch-load all topic links and technology claims up-front so we avoid
        # an N+1 query inside the per-paper loop (each paper used to trigger two
        # extra SELECTs), which becomes quadratic-slow once the corpus grows.
        paper_ids = [row["id"] for row in rows]
        topics_by_paper: dict[str, set[str]] = {}

        if paper_ids:
            mapping: dict[str, set[str]] = {}
            for chunk_start in range(0, len(paper_ids), 500):
                chunk = paper_ids[chunk_start : chunk_start + 500]
                placeholders = ",".join("?" * len(chunk))
                topic_rows = self.conn.execute(
                    f"SELECT paper_id, topic_id FROM paper_topic WHERE paper_id IN ({placeholders}) ORDER BY paper_id, topic_id",
                    chunk,
                ).fetchall()
                for trow in topic_rows:
                    mapping.setdefault(trow["paper_id"], set()).add(trow["topic_id"])
            topics_by_paper = mapping

        claims_by_paper: dict[str, list[dict[str, Any]]] = {}
        if paper_ids:
            for chunk_start in range(0, len(paper_ids), 500):
                chunk = paper_ids[chunk_start : chunk_start + 500]
                placeholders = ",".join("?" * len(chunk))
                claim_rows = self.conn.execute(
                    f"""
                    SELECT paper_id, id, claim_type, statement, evidence_anchor_ids_json
                    FROM technology_claim
                    WHERE paper_id IN ({placeholders})
                    ORDER BY paper_id, claim_type
                    """,
                    chunk,
                ).fetchall()
                for crow in claim_rows:
                    claims_by_paper.setdefault(crow["paper_id"], []).append(
                        {
                            "id": crow["id"],
                            "claim_type": crow["claim_type"],
                            "statement": crow["statement"],
                            "evidence_anchor_ids": loads(crow["evidence_anchor_ids_json"], []),
                        }
                    )

        for row in rows:
            topics = topics_by_paper.get(row["id"], set())
            claims = claims_by_paper.get(row["id"], [])
            text = " ".join(
                str(row[key] or "")
                for key in ("canonical_title", "abstract", "report_summary", "report_method")
            )
            text = " ".join([text, *(str(claim["statement"]) for claim in claims)])
            records.append(
                {
                    "id": row["id"],
                    "date": row["first_publication_date"],
                    "topics": topics,
                    "tokens": _relation_tokens(text),
                    "text": text.lower(),
                    "title": row["canonical_title"],
                    "claims": claims,
                }
            )

        created = 0
        updated = 0
        supported = {"similar", "extends", "complements", "conflicts"}
        for index, left in enumerate(records):
            for right in records[index + 1 :]:
                if paper_id and paper_id not in {left["id"], right["id"]}:
                    continue
                relation_type, confidence, reason, evidence = _classify_relation(left, right)
                if relation_type not in supported:
                    continue
                existing = self.conn.execute(
                    """
                    SELECT id FROM paper_relation
                    WHERE from_paper_id = ? AND to_paper_id = ? AND relation_type = ?
                    """,
                    (left["id"], right["id"], relation_type),
                ).fetchone()
                if existing:
                    self.conn.execute(
                        """
                        UPDATE paper_relation
                        SET reason = ?, evidence_json = ?, confidence = ?
                        WHERE id = ?
                        """,
                        (reason, dumps(evidence), confidence, existing["id"]),
                    )
                    updated += 1
                else:
                    self.conn.execute(
                        """
                        INSERT INTO paper_relation (
                            id, from_paper_id, to_paper_id, relation_type,
                            reason, evidence_json, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("rel"),
                            left["id"],
                            right["id"],
                            relation_type,
                            reason,
                            dumps(evidence),
                            confidence,
                        ),
                    )
                    created += 1
        return {
            "scope": paper_id or "all",
            "created": created,
            "updated": updated,
            "supported_relation_types": sorted(supported),
        }

    def create_invention_candidate(self, data: InventionCandidateCreate) -> InventionCandidate:
        validate_patent_candidate_gate(data)
        if len(data.integration_mechanism.strip()) < 8:
            raise ConflictError(
                "Candidate rejected as mechanical aggregation: describe a concrete cross-paper integration mechanism"
            )
        resolved_papers: set[str] = set()
        for source in data.sources:
            if not source.paper_id and not source.paper_version_id:
                raise ConflictError("Each invention source must reference a paper_id or paper_version_id")
            resolved_paper_id = source.paper_id
            if source.paper_version_id:
                version = self.get_paper_version(source.paper_version_id)
                if resolved_paper_id and resolved_paper_id != version.paper_id:
                    raise ConflictError("paper_id and paper_version_id must identify the same paper")
                resolved_paper_id = version.paper_id
            if resolved_paper_id:
                self.ensure_paper_exists(resolved_paper_id)
                if resolved_paper_id in resolved_papers:
                    raise ConflictError("Candidate sources must reference distinct papers")
                resolved_papers.add(resolved_paper_id)
        if len(resolved_papers) < 2:
            raise ConflictError("Candidate requires at least two distinct papers")
        candidate_id = new_id("cand")
        title = data.title or "跨论文技术组合候选"
        now = utcnow()
        gate = {
            "status": "pending",
            "required": [
                "明确新增耦合或控制机制",
                "说明 coupling_interface、data_or_control_flow、why_not_juxtaposition、expected_joint_effect",
                "每个技术事实具备证据",
                "完成学术和专利查新",
                "用户确认贡献、脱敏和保护主线",
            ],
        }
        self.conn.execute(
            """
            INSERT INTO invention_candidate (
                id, title, status, source_refs_json, problem_statement,
                integration_mechanism, coupling_interface, data_or_control_flow,
                why_not_juxtaposition, expected_joint_effect, technical_effects, risk_notes,
                evidence_json, gate_json, created_at, updated_at
            )
            VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                title,
                dumps([item.model_dump() for item in data.sources]),
                data.problem_statement,
                data.integration_mechanism,
                data.coupling_interface,
                data.data_or_control_flow,
                data.why_not_juxtaposition,
                data.expected_joint_effect,
                data.technical_effects,
                data.risk_notes,
                dumps([item.model_dump(mode="json") for item in data.evidence]),
                dumps(gate),
                now,
                now,
            ),
        )
        for evidence in data.evidence:
            report_field = str(evidence.report_field or "").strip()
            source_id = str(evidence.source or "").strip()
            if not report_field or not source_id:
                continue
            source_kind = source_id.split(":", 1)[0] if ":" in source_id else "manual_input"
            self.conn.execute(
                """
                INSERT INTO claim_provenance (
                    id, invention_candidate_id, report_field, source_kind,
                    source_id, evidence_json, verified_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("prov"),
                    candidate_id,
                    report_field,
                    source_kind,
                    source_id,
                    dumps(evidence.model_dump(mode="json")),
                    "verified" if evidence.kind == "fact" else "hypothesis",
                ),
            )
        for index, source in enumerate(data.sources):
            component_name = source.technical_card_id or source.paper_version_id or source.paper_id
            self.conn.execute(
                """
                INSERT INTO candidate_component (
                    id, invention_candidate_id, source_ref_index, component_type,
                    name, contribution, evidence_json
                )
                VALUES (?, ?, ?, 'source_contribution', ?, ?, ?)
                ON CONFLICT(
                    invention_candidate_id, source_ref_index, component_type, name
                ) DO NOTHING
                """,
                (
                    new_id("ccomp"),
                    candidate_id,
                    index,
                    str(component_name or f"source-{index}"),
                    source.contribution,
                    dumps(
                        [
                            item.model_dump(mode="json")
                            for item in data.evidence
                            if (
                                source.paper_id
                                and str(item.source).endswith(source.paper_id)
                            )
                            or (
                                source.paper_version_id
                                and str(item.source).endswith(source.paper_version_id)
                            )
                        ]
                    ),
                ),
            )
        self.conn.execute(
            """
            INSERT INTO integration_mechanism_record (
                id, invention_candidate_id, mechanism_type, coupling_interface,
                data_or_control_flow, why_not_juxtaposition, expected_joint_effect,
                evidence_json
            )
            VALUES (?, ?, 'cross_paper_coupling', ?, ?, ?, ?, ?)
            """,
            (
                new_id("imech"),
                candidate_id,
                data.coupling_interface,
                data.data_or_control_flow,
                data.why_not_juxtaposition,
                data.expected_joint_effect,
                dumps(
                    [
                        item.model_dump(mode="json")
                        for item in data.evidence
                        if (item.report_field or item.section)
                        in {
                            "integration_mechanism",
                            "coupling_interface",
                            "data_or_control_flow",
                            "why_not_juxtaposition",
                            "expected_joint_effect",
                        }
                    ]
                ),
            ),
        )
        return self.get_invention_candidate(candidate_id)

    def list_candidate_components(self, candidate_id: str) -> list[CandidateComponent]:
        self.get_invention_candidate(candidate_id)
        rows = self.conn.execute(
            """
            SELECT * FROM candidate_component
            WHERE invention_candidate_id = ?
            ORDER BY source_ref_index, component_type, name
            """,
            (candidate_id,),
        ).fetchall()
        return [row_to_candidate_component(row) for row in rows]

    def list_integration_mechanisms(
        self,
        candidate_id: str,
    ) -> list[IntegrationMechanismRecord]:
        self.get_invention_candidate(candidate_id)
        rows = self.conn.execute(
            """
            SELECT * FROM integration_mechanism_record
            WHERE invention_candidate_id = ?
            ORDER BY created_at, id
            """,
            (candidate_id,),
        ).fetchall()
        return [row_to_integration_mechanism(row) for row in rows]

    def ensure_candidate_foundation_stages(self, candidate_id: str) -> list[PatentStageRun]:
        """Backfill the deterministic intake and candidate-analysis stage records."""

        candidate = self.get_invention_candidate(candidate_id)
        existing = {item.stage: item for item in self.list_patent_stage_runs(candidate_id)}
        if "intake" not in existing:
            existing["intake"] = self.record_patent_stage_run(
                candidate_id,
                PatentStageRunCreate(
                    stage="intake",
                    status="succeeded",
                    idempotency_key=f"candidate:{candidate_id}:intake",
                    input={"sources": [item.model_dump(mode="json") for item in candidate.sources]},
                    output={"source_count": len(candidate.sources), "candidate_id": candidate_id},
                ),
            )
        if "candidate_analysis" not in existing:
            existing["candidate_analysis"] = self.record_patent_stage_run(
                candidate_id,
                PatentStageRunCreate(
                    stage="candidate_analysis",
                    status="succeeded",
                    idempotency_key=f"candidate:{candidate_id}:candidate-analysis",
                    input={
                        "problem_statement": candidate.problem_statement,
                        "integration_mechanism": candidate.integration_mechanism,
                        "coupling_interface": candidate.coupling_interface,
                        "data_or_control_flow": candidate.data_or_control_flow,
                    },
                    output={
                        "gate_status": "accepted",
                        "component_count": len(self.list_candidate_components(candidate_id)),
                        "mechanism_count": len(self.list_integration_mechanisms(candidate_id)),
                    },
                ),
            )
        return [existing[stage] for stage in ("intake", "candidate_analysis")]

    def record_patent_stage_run(
        self,
        candidate_id: str,
        data: PatentStageRunCreate,
    ) -> PatentStageRun:
        self.get_invention_candidate(candidate_id)
        self._ensure_patent_stage_links(candidate_id, data.patent_draft_id, data.artifact_id, data.job_id)
        existing = self._find_patent_stage_run(candidate_id, data.stage)
        if existing:
            current = row_to_patent_stage_run(existing)
            if (
                current.idempotency_key
                and data.idempotency_key
                and current.idempotency_key == data.idempotency_key
                and current.input != data.input
            ):
                raise ConflictError(
                    "Patent stage idempotency key was reused with different input"
                )
            return self.update_patent_stage_run(
                current.id,
                PatentStageRunUpdate(
                    status=data.status,
                    artifact_id=data.artifact_id,
                    job_id=data.job_id,
                    output=data.output,
                    metadata={**current.metadata, **data.metadata},
                ),
                idempotency_key=data.idempotency_key,
            )

        self._ensure_patent_stage_order(candidate_id, data.stage)
        validate_state_transition(
            "patent_stage_run",
            "pending",
            data.status,
            ALLOWED_PATENT_STAGE_STATUS_TRANSITIONS,
        )
        stage_id = new_id("pstage")
        now = utcnow()
        started_at = now if data.status == "running" else None
        completed_at = now if data.status in {"succeeded", "failed", "skipped", "cancelled"} else None
        self.conn.execute(
            """
            INSERT INTO patent_stage_run (
                id, invention_candidate_id, patent_draft_id, stage, status,
                input_json, output_json, artifact_id, job_id, idempotency_key,
                metadata_json, started_at, completed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stage_id,
                candidate_id,
                data.patent_draft_id,
                data.stage,
                data.status,
                dumps(data.input),
                dumps(data.output),
                data.artifact_id,
                data.job_id,
                data.idempotency_key,
                dumps(data.metadata),
                started_at,
                completed_at,
                now,
                now,
            ),
        )
        return self.get_patent_stage_run(stage_id)

    def update_patent_stage_run(
        self,
        stage_run_id: str,
        data: PatentStageRunUpdate,
        *,
        idempotency_key: str | None = None,
    ) -> PatentStageRun:
        current = self.get_patent_stage_run(stage_run_id)
        if idempotency_key and current.idempotency_key and idempotency_key != current.idempotency_key:
            raise ConflictError("Patent stage idempotency key does not match existing run")
        self._ensure_patent_stage_links(
            current.invention_candidate_id,
            current.patent_draft_id,
            data.artifact_id,
            data.job_id,
        )
        next_status = data.status or current.status
        validate_state_transition(
            "patent_stage_run",
            current.status,
            next_status,
            ALLOWED_PATENT_STAGE_STATUS_TRANSITIONS,
        )
        now = utcnow()
        started_at = current.started_at
        completed_at = current.completed_at
        if next_status == "running" and started_at is None:
            started_at = now
        if next_status in {"succeeded", "failed", "skipped", "cancelled"}:
            completed_at = completed_at or now
        if next_status in {"pending", "running"}:
            completed_at = None
        output = current.output if data.output is None else data.output
        metadata = current.metadata if data.metadata is None else data.metadata
        artifact_id = data.artifact_id if data.artifact_id is not None else current.artifact_id
        job_id = data.job_id if data.job_id is not None else current.job_id
        self.conn.execute(
            """
            UPDATE patent_stage_run
            SET status = ?, output_json = ?, metadata_json = ?, artifact_id = ?,
                job_id = ?, started_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                dumps(output),
                dumps(metadata),
                artifact_id,
                job_id,
                started_at,
                completed_at,
                now,
                stage_run_id,
            ),
        )
        return self.get_patent_stage_run(stage_run_id)

    def get_patent_stage_run(self, stage_run_id: str) -> PatentStageRun:
        row = self.conn.execute(
            "SELECT * FROM patent_stage_run WHERE id = ?",
            (stage_run_id,),
        ).fetchone()
        if not row:
            raise NotFoundError("patent_stage_run", stage_run_id)
        return row_to_patent_stage_run(row)

    def list_patent_stage_runs(self, candidate_id: str) -> list[PatentStageRun]:
        self.get_invention_candidate(candidate_id)
        rows = self.conn.execute(
            """
            SELECT * FROM patent_stage_run
            WHERE invention_candidate_id = ?
            ORDER BY CASE stage
                WHEN 'intake' THEN 1
                WHEN 'candidate_analysis' THEN 2
                WHEN 'prior_art' THEN 3
                WHEN 'preview' THEN 4
                WHEN 'builder' THEN 5
                WHEN 'self_check' THEN 6
                ELSE 99
            END
            """,
            (candidate_id,),
        ).fetchall()
        return [row_to_patent_stage_run(row) for row in rows]

    def _find_patent_stage_run(
        self,
        candidate_id: str,
        stage: str,
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM patent_stage_run
            WHERE invention_candidate_id = ? AND stage = ?
            """,
            (candidate_id, stage),
        ).fetchone()

    def _ensure_patent_stage_order(self, candidate_id: str, stage: str) -> None:
        stage_index = PATENT_STAGE_ORDER.index(stage)
        required = set(PATENT_STAGE_ORDER[:stage_index])
        if not required:
            return
        rows = self.conn.execute(
            """
            SELECT stage, status FROM patent_stage_run
            WHERE invention_candidate_id = ?
            """,
            (candidate_id,),
        ).fetchall()
        completed = {
            row["stage"]
            for row in rows
            if row["stage"] in required and row["status"] in {"succeeded", "skipped"}
        }
        missing = sorted(required - completed, key=PATENT_STAGE_ORDER.index)
        if missing:
            raise ConflictError(
                f"Patent stage {stage} requires completed prior stages: "
                + ", ".join(missing)
            )

    def _ensure_patent_stage_links(
        self,
        candidate_id: str,
        patent_draft_id: str | None,
        artifact_id: str | None,
        job_id: str | None,
    ) -> None:
        if patent_draft_id:
            draft = self.get_patent_draft(patent_draft_id)
            if draft.invention_candidate_id != candidate_id:
                raise ConflictError("Patent stage draft must belong to the candidate")
        if artifact_id:
            self.get_artifact(artifact_id)
        if job_id:
            job = self.get_job(job_id)
            if job.target_id != candidate_id and job.target_id != patent_draft_id:
                raise ConflictError("Patent stage job must target the candidate or draft")

    def list_invention_candidates(self, status: str | None = None) -> list[InventionCandidate]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM invention_candidate WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM invention_candidate ORDER BY created_at DESC"
            ).fetchall()
        return [row_to_candidate(row) for row in rows]

    def get_invention_candidate(self, candidate_id: str) -> InventionCandidate:
        row = self.conn.execute(
            "SELECT * FROM invention_candidate WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not row:
            raise NotFoundError("invention_candidate", candidate_id)
        return row_to_candidate(row)

    def candidate_job(
        self, candidate_id: str, kind: str, request: dict[str, Any], idempotency_key: str | None
    ) -> AsyncJobResponse:
        self.get_invention_candidate(candidate_id)
        return self.create_job(kind, "invention_candidate", candidate_id, request, idempotency_key)

    def approve_candidate(self, candidate_id: str, request: CandidateApproveRequest) -> InventionCandidate:
        candidate = self.get_invention_candidate(candidate_id)
        gate = dict(candidate.gate)
        audit = list(gate.get("audit") or [])
        prior_art_job = self._latest_prior_art_job(candidate_id)
        if request.approved:
            missing_confirmations = []
            if not request.approver.strip():
                missing_confirmations.append("approver")
            if not request.contribution_confirmed:
                missing_confirmations.append("contribution")
            if not request.sanitization_confirmed:
                missing_confirmations.append("sanitization")
            if not request.protection_focus_confirmed:
                missing_confirmations.append("protection-focus")
            if not request.unverified_facts_confirmed:
                missing_confirmations.append("unverified-facts")
            if missing_confirmations:
                raise ConflictError(
                    "Candidate approval requires confirmations for: "
                    + ", ".join(missing_confirmations)
                )
            if prior_art_job is None or prior_art_job["status"] != "succeeded":
                if not request.override_prior_art:
                    raise ConflictError(
                        "Candidate approval requires a succeeded prior_art_check job or an explicit prior-art override"
                    )
                if not request.override_reason.strip():
                    raise ConflictError("Prior-art override requires a non-empty override_reason")
                gate["prior_art"] = {
                    "status": "overridden",
                    "override_reason": request.override_reason.strip(),
                    "notes": request.notes,
                    "approver": request.approver.strip(),
                    "overridden_at": utcnow(),
                    "job_id": prior_art_job["id"] if prior_art_job else None,
                    "job_status": prior_art_job["status"] if prior_art_job else None,
                }
                audit.append(
                    {
                        "event": "prior_art_override",
                        "at": utcnow(),
                        "reason": request.override_reason.strip(),
                        "notes": request.notes,
                        "approver": request.approver.strip(),
                    }
                )
            else:
                gate["prior_art"] = {
                    "status": "succeeded",
                    "job_id": prior_art_job["id"],
                    "checked_at": prior_art_job["updated_at"],
                }
                audit.append(
                    {
                        "event": "prior_art_verified",
                        "at": utcnow(),
                        "job_id": prior_art_job["id"],
                        "approver": request.approver.strip(),
                    }
                )
            gate["human_confirmations"] = {
                "approver": request.approver.strip(),
                "contribution": request.contribution_confirmed,
                "sanitization": request.sanitization_confirmed,
                "protection_focus": request.protection_focus_confirmed,
                "unverified_facts": request.unverified_facts_confirmed,
                "confirmed_at": utcnow(),
            }
            self.ensure_candidate_foundation_stages(candidate_id)
            stage_by_name = {item.stage: item for item in self.list_patent_stage_runs(candidate_id)}
            if "prior_art" not in stage_by_name:
                prior_art_status = "skipped" if request.override_prior_art else "succeeded"
                prior_art_output = (
                    {
                        "status": "overridden",
                        "reason": request.override_reason.strip(),
                        "approver": request.approver.strip(),
                    }
                    if request.override_prior_art
                    else loads(prior_art_job["result_json"], {})
                )
                self.record_patent_stage_run(
                    candidate_id,
                    PatentStageRunCreate(
                        stage="prior_art",
                        status=prior_art_status,
                        job_id=prior_art_job["id"] if prior_art_job else None,
                        idempotency_key=f"candidate:{candidate_id}:prior-art-approval",
                        input={"override": request.override_prior_art},
                        output=prior_art_output,
                    ),
                )
            stage_by_name = {item.stage: item for item in self.list_patent_stage_runs(candidate_id)}
            if "preview" not in stage_by_name:
                self.record_patent_stage_run(
                    candidate_id,
                    PatentStageRunCreate(
                        stage="preview",
                        status="succeeded",
                        idempotency_key=f"candidate:{candidate_id}:human-preview",
                        input={"notes": request.notes},
                        output={
                            "approver": request.approver.strip(),
                            "contribution_confirmed": request.contribution_confirmed,
                            "sanitization_confirmed": request.sanitization_confirmed,
                            "protection_focus_confirmed": request.protection_focus_confirmed,
                            "unverified_facts_confirmed": request.unverified_facts_confirmed,
                        },
                    ),
                )
        gate.update(
            {
                "status": "approved" if request.approved else "rejected",
                "notes": request.notes,
                "audit": audit,
            }
        )
        self.conn.execute(
            """
            UPDATE invention_candidate
            SET status = ?, gate_json = ?, updated_at = ?
            WHERE id = ?
            """,
            ("approved" if request.approved else "rejected", dumps(gate), utcnow(), candidate_id),
        )
        self.conn.execute(
            """
            INSERT INTO human_decision (
                id, invention_candidate_id, decision, actor, details_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                new_id("decision"),
                candidate_id,
                "approved" if request.approved else "rejected",
                request.approver.strip() or "anonymous",
                dumps(request.model_dump(mode="json")),
            ),
        )
        return self.get_invention_candidate(candidate_id)

    def _latest_prior_art_job(self, candidate_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM job
            WHERE kind = 'prior_art_check'
              AND target_type = 'invention_candidate'
              AND target_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()

    def create_patent_draft(
        self, candidate_id: str, request: DraftCreateRequest, idempotency_key: str | None
    ) -> tuple[PatentDraft, AsyncJobResponse]:
        candidate = self.get_invention_candidate(candidate_id)
        if candidate.status != "approved":
            raise ConflictError("Patent draft generation requires human approval first")
        self._ensure_candidate_output_gate(candidate)
        job = self.create_job(
            "patent_draft",
            "invention_candidate",
            candidate_id,
            request.model_dump(mode="json"),
            idempotency_key,
        )
        case_name = request.case_name or candidate.title
        request_hash = stable_hash(
            {
                "candidate_id": candidate_id,
                "case_name": case_name,
                "protection_focus": request.protection_focus,
                "notes": request.notes,
            }
        )
        existing_by_request = self._find_patent_draft_by_request_hash(candidate_id, request_hash)
        if existing_by_request:
            return existing_by_request, job
        version_label = self._timestamped_draft_version_label(case_name)
        existing = self.conn.execute(
            """
            SELECT * FROM patent_draft
            WHERE invention_candidate_id = ? AND version_label = ?
            """,
            (candidate_id, version_label),
        ).fetchone()
        if existing:
            return row_to_draft(existing), job
        draft_id = new_id("draft")
        now = utcnow()
        markdown = self.render_draft_markdown(candidate, case_name, request)
        self.conn.execute(
            """
            INSERT INTO patent_draft (
                id, invention_candidate_id, case_name, version_label,
                status, markdown, self_check_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'generated', ?, ?, ?, ?)
            """,
            (
                draft_id,
                candidate_id,
                case_name,
                version_label,
                markdown,
                dumps(
                    {
                        "legal_notice": "技术交底书草稿，不构成新颖性、创造性或授权结论。",
                        "request_hash": request_hash,
                        "candidate_id": candidate_id,
                        "fact_provenance_coverage": {
                            "coverage_percent": 100,
                            "missing_fields": [],
                        },
                    }
                ),
                now,
                now,
            ),
        )
        return self.get_patent_draft(draft_id), job

    def render_draft_markdown(
        self, candidate: InventionCandidate, case_name: str, request: DraftCreateRequest
    ) -> str:
        sources = "\n".join(
            f"- paper_id={source.paper_id or ''}, paper_version_id={source.paper_version_id or ''}, contribution={source.contribution}"
            for source in candidate.sources
        )
        return f"""# {case_name}

> 说明：本文件为 Research Hub 自动生成的技术交底书草稿，不构成法律意义上的新颖性、创造性或可授权性结论。

## 技术领域

AI Infra 论文研读中识别出的跨论文组合技术。

## 背景技术

{candidate.problem_statement or "待补充：由专利编辑根据来源论文和业务材料完善。"}

## 发明内容

### 技术问题

{candidate.problem_statement or "待补充明确技术问题。"}

### 技术方案

{candidate.integration_mechanism or "待补充组合接口、数据流、控制流和触发条件。"}

### 耦合接口

{candidate.coupling_interface}

### 数据或控制流

{candidate.data_or_control_flow}

### 非简单拼接说明

{candidate.why_not_juxtaposition}

### 技术效果

{candidate.technical_effects or "待补充已验证效果；未验证效果必须标注为待实验。"}

### 预期联合效果

【hypothesis】{candidate.expected_joint_effect}

## 实施方式

{request.protection_focus or "待补充方法、系统、装置和存储介质的实施细节。"}

## 来源证据

{sources}

## 事实级来源与假设标注

{self._candidate_provenance_lines(candidate)}

## 风险与待确认

{candidate.risk_notes or "待完成人工确认、脱敏、查新和实验验证。"}

## 修订备注

{request.notes}
"""

    def _find_patent_draft_by_request_hash(
        self, candidate_id: str, request_hash: str
    ) -> PatentDraft | None:
        rows = self.conn.execute(
            """
            SELECT * FROM patent_draft
            WHERE invention_candidate_id = ?
            ORDER BY created_at ASC
            """,
            (candidate_id,),
        ).fetchall()
        for row in rows:
            if loads(row["self_check_json"], {}).get("request_hash") == request_hash:
                return row_to_draft(row)
        return None

    @staticmethod
    def _timestamped_draft_version_label(case_name: str) -> str:
        return f"{case_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    @staticmethod
    def _candidate_provenance_lines(candidate: InventionCandidate) -> str:
        lines = []
        for anchor in candidate.evidence:
            field = anchor.report_field or anchor.section or "general"
            lines.append(f"- `{field}` [{anchor.kind}] {anchor.source}: {anchor.note}")
        return "\n".join(lines)

    @staticmethod
    def _ensure_candidate_output_gate(candidate: InventionCandidate) -> None:
        missing = [
            field
            for field in PATENT_CANDIDATE_FACT_FIELDS
            if not _text_present(str(getattr(candidate, field)))
        ]
        if missing:
            raise ConflictError(
                "Patent draft generation requires complete structured coupling fields: "
                + ", ".join(missing)
            )
        fields_with_evidence: set[str] = set()
        hypothesis_fields: set[str] = set()
        for anchor in candidate.evidence:
            if anchor.kind in {"fact", "hypothesis"}:
                fields_with_evidence.update(_anchor_fields(anchor))
            if anchor.kind == "hypothesis":
                hypothesis_fields.update(_anchor_fields(anchor))
        missing_evidence = sorted(set(PATENT_CANDIDATE_FACT_FIELDS) - fields_with_evidence)
        if missing_evidence:
            raise ConflictError(
                "Patent draft generation requires 100% fact-level provenance coverage; missing: "
                + ", ".join(missing_evidence)
            )
        if not {"expected_joint_effect", "technical_effects"} & hypothesis_fields:
            raise ConflictError("Unverified technical effects must be marked as hypothesis")

    def get_patent_draft(self, draft_id: str) -> PatentDraft:
        row = self.conn.execute("SELECT * FROM patent_draft WHERE id = ?", (draft_id,)).fetchone()
        if not row:
            raise NotFoundError("patent_draft", draft_id)
        return row_to_draft(row)

    def transition_patent_draft_status(
        self,
        draft_id: str,
        next_status: str,
        *,
        reason: str = "",
    ) -> PatentDraft:
        draft = self.get_patent_draft(draft_id)
        validate_state_transition(
            "patent_draft",
            draft.status,
            next_status,
            ALLOWED_PATENT_DRAFT_STATUS_TRANSITIONS,
        )
        self_check = dict(draft.self_check)
        transitions = list(self_check.get("status_transitions") or [])
        transitions.append(
            {
                "from": draft.status,
                "to": next_status,
                "reason": reason,
                "at": utcnow(),
            }
        )
        self_check["status_transitions"] = transitions
        self.conn.execute(
            """
            UPDATE patent_draft
            SET status = ?, self_check_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, dumps(self_check), utcnow(), draft_id),
        )
        return self.get_patent_draft(draft_id)

    def list_patent_drafts(self, candidate_id: str | None = None) -> list[PatentDraft]:
        if candidate_id:
            rows = self.conn.execute(
                """
                SELECT * FROM patent_draft
                WHERE invention_candidate_id = ?
                ORDER BY created_at DESC
                """,
                (candidate_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM patent_draft ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        return [row_to_draft(row) for row in rows]

    def draft_versions(self, draft_id: str) -> list[PatentDraft]:
        draft = self.get_patent_draft(draft_id)
        rows = self.conn.execute(
            """
            SELECT * FROM patent_draft
            WHERE invention_candidate_id = ?
            ORDER BY created_at DESC
            """,
            (draft.invention_candidate_id,),
        ).fetchall()
        return [row_to_draft(row) for row in rows]

    def draft_export(self, draft_id: str, format_value: str) -> dict[str, Any]:
        draft = self.get_patent_draft(draft_id)
        normalized = format_value.lower().lstrip(".")
        if normalized == "docx":
            row = self.conn.execute(
                """
                SELECT * FROM artifact
                WHERE patent_draft_id = ? AND artifact_type = 'patent_disclosure_docx'
                ORDER BY created_at DESC LIMIT 1
                """,
                (draft_id,),
            ).fetchone()
            if not row:
                raise ConflictError("DOCX artifact has not been generated for this draft")
            path = str(row["uri"]).removeprefix("file://")
            return {
                "draft_id": draft.id,
                "format": "docx",
                "filename": f"{draft.case_name}-{draft.version_label}.docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "path": path,
            }
        if normalized != "markdown":
            raise ConflictError("Supported patent export formats are markdown and docx")
        return {
            "draft_id": draft.id,
            "format": "markdown",
            "filename": f"{draft.case_name}-{draft.version_label}.md",
            "content_type": "text/markdown; charset=utf-8",
            "content": draft.markdown,
        }

    def list_draft_artifacts(self, draft_id: str) -> list[Artifact]:
        self.get_patent_draft(draft_id)
        rows = self.conn.execute(
            "SELECT * FROM artifact WHERE patent_draft_id = ? ORDER BY created_at DESC",
            (draft_id,),
        ).fetchall()
        return [row_to_artifact(row) for row in rows]

    def revise_draft(
        self, draft_id: str, request: PatentDraftReviseRequest, idempotency_key: str | None
    ) -> AsyncJobResponse:
        self.get_patent_draft(draft_id)
        return self.create_job("revise", "patent_draft", draft_id, request.model_dump(), idempotency_key)

    def stats(self) -> StatsResponse:
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

        job_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM job GROUP BY status ORDER BY status"
        ).fetchall()
        return StatsResponse(
            papers=count("paper"),
            paper_versions=count("paper_version"),
            artifacts=count("artifact"),
            jobs={row["status"]: int(row["n"]) for row in job_rows},
            reports=count("paper_report"),
            invention_candidates=count("invention_candidate"),
            patent_drafts=count("patent_draft"),
        )


def response_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return {"items": [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]}
    if isinstance(value, dict):
        return value
    return {"value": value}


def _section_excerpt(text: str, hint: str, limit: int = 700) -> str:
    """Return a bounded report excerpt without pretending it is structured."""

    normalized = " ".join(text.split())
    if not normalized:
        return ""
    position = normalized.find(hint)
    if position < 0:
        position = 0
    return normalized[position : position + limit]


def _relation_tokens(text: str) -> set[str]:
    stop = {
        "with",
        "from",
        "that",
        "this",
        "using",
        "based",
        "paper",
        "model",
        "models",
        "system",
        "method",
        "results",
        "通过",
        "一种",
        "论文",
        "方法",
        "系统",
        "模型",
    }
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
        if token not in stop
    }


def _classify_relation(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[str, float, str, list[dict[str, Any]]]:
    shared_topics = sorted(left["topics"] & right["topics"])
    shared_tokens = sorted(left["tokens"] & right["tokens"])
    union = left["tokens"] | right["tokens"]
    lexical = len(shared_tokens) / max(1, len(union))
    left_claims = {item["claim_type"]: item for item in left.get("claims", [])}
    right_claims = {item["claim_type"]: item for item in right.get("claims", [])}
    shared_claim_types = sorted(left_claims.keys() & right_claims.keys())
    conflict_pairs = (
        ("dense", "sparse"),
        ("lossless", "lossy"),
        ("centralized", "distributed"),
        ("static", "dynamic"),
        ("高精度", "低精度"),
        ("稠密", "稀疏"),
    )
    conflicts = [
        [first, second]
        for first, second in conflict_pairs
        if (first in left["text"] and second in right["text"])
        or (second in left["text"] and first in right["text"])
    ]
    evidence: list[dict[str, Any]] = [
        {
            "kind": "fact",
            "source": "paper_topic",
            "value": shared_topics,
            "note": "共同主题来自 Research Hub 的持久化分类记录。",
        },
        {
            "kind": "analysis",
            "source": "deterministic_relation_baseline_v1",
            "value": shared_tokens[:16],
            "note": "关系类型是可复核的规则分析，不是论文作者结论。",
        },
        {
            "kind": "fact",
            "source": "technology_claim",
            "value": [
                {
                    "claim_type": claim_type,
                    "left_claim_id": left_claims[claim_type]["id"],
                    "right_claim_id": right_claims[claim_type]["id"],
                    "left_evidence_anchor_ids": left_claims[claim_type]["evidence_anchor_ids"],
                    "right_evidence_anchor_ids": right_claims[claim_type]["evidence_anchor_ids"],
                }
                for claim_type in shared_claim_types
            ],
            "note": "结构化技术主张及其证据锚点参与关系判定。",
        },
    ]
    if conflicts:
        evidence.append(
            {
                "kind": "analysis",
                "source": "constraint_keyword_pairs",
                "value": conflicts,
                "note": "检测到可能相反的设计约束，需人工回看原文确认。",
            }
        )
        return (
            "conflicts",
            min(0.92, 0.62 + 0.08 * len(conflicts)),
            f"两篇论文在 {', '.join('/'.join(pair) for pair in conflicts)} 等约束上可能冲突；该判断需要原文复核。",
            evidence,
        )
    if shared_topics and lexical >= 0.10:
        return (
            "similar",
            min(0.95, 0.58 + lexical),
            f"共同覆盖 {', '.join(shared_topics)}，并共享 {len(shared_tokens)} 个方法或系统关键词。",
            evidence,
        )
    if shared_topics:
        left_date = left.get("date") or ""
        right_date = right.get("date") or ""
        if left_date and right_date and left_date != right_date:
            older, newer = (left, right) if left_date < right_date else (right, left)
            return (
                "extends",
                0.66,
                f"{newer['title']} 与较早的 {older['title']} 处于共同主题，但方法关键词重合较低，标记为待核验的扩展关系。",
                evidence,
            )
        return (
            "complements",
            0.64,
            f"两篇论文处于共同主题 {', '.join(shared_topics)}，但方法侧重点不同，适合作为互补组合候选。",
            evidence,
        )
    if len(shared_tokens) >= 4:
        return (
            "complements",
            min(0.78, 0.5 + len(shared_tokens) / 100),
            f"两篇论文跨主题共享 {len(shared_tokens)} 个系统关键词，可能在组件或约束层形成互补。",
            evidence,
        )
    return "", 0.0, "", evidence
