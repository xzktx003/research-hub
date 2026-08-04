"""Pydantic contracts for the Research Hub API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


JsonDict = dict[str, Any]

JobKind = Literal[
    "discover",
    "download",
    "parse",
    "translate",
    "analyze",
    "relate",
    "prior_art_check",
    "patent_draft",
    "revise",
]
JobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "partial_succeeded",
    "retryable_failed",
    "terminal_failed",
    "cancelled",
]
PaperStatus = Literal[
    "new",
    "discovered",
    "downloaded",
    "parsed",
    "translated",
    "analyzed",
    "scored",
    "published",
    "rejected",
    "failed",
]
PatentDraftStatus = Literal[
    "queued",
    "generated",
    "under_review",
    "revised",
    "approved",
    "exported",
    "abandoned",
]
PatentStage = Literal[
    "intake",
    "candidate_analysis",
    "prior_art",
    "preview",
    "builder",
    "self_check",
]
PatentStageStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
]

PATENT_STAGE_ORDER: tuple[str, ...] = (
    "intake",
    "candidate_analysis",
    "prior_art",
    "preview",
    "builder",
    "self_check",
)

ALLOWED_PAPER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "new": {"discovered", "rejected", "failed"},
    "discovered": {"downloaded", "parsed", "rejected", "failed"},
    "downloaded": {"parsed", "failed"},
    "parsed": {"translated", "analyzed", "failed"},
    "translated": {"analyzed", "failed"},
    "analyzed": {"scored", "published", "failed"},
    "scored": {"published", "failed"},
    "published": set(),
    "rejected": set(),
    "failed": {"discovered"},
}
ALLOWED_JOB_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "partial_succeeded", "retryable_failed", "terminal_failed", "cancelled"},
    "succeeded": set(),
    "partial_succeeded": set(),
    "retryable_failed": {"queued", "terminal_failed", "cancelled"},
    "terminal_failed": {"queued"},
    "cancelled": {"queued"},
}
ALLOWED_PATENT_DRAFT_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"generated", "abandoned"},
    "generated": {"under_review", "revised", "approved", "abandoned"},
    "under_review": {"revised", "approved", "abandoned"},
    "revised": {"under_review", "approved", "abandoned"},
    "approved": {"exported"},
    "exported": set(),
    "abandoned": set(),
}
ALLOWED_PATENT_STAGE_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "succeeded", "skipped", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": {"pending", "running"},
    "skipped": {"pending"},
    "cancelled": {"pending"},
}


class EvidenceAnchor(BaseModel):
    """Typed provenance boundary shared by reports and patent candidates."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["fact", "analysis", "hypothesis"]
    source: str = "user"
    note: str = ""
    report_field: str | None = None
    section: str | None = None
    page: str | int | None = None
    quote: str | None = None
    quote_hash: str | None = None


class ApiError(BaseModel):
    code: str
    message: str
    details: JsonDict = Field(default_factory=dict)


class AsyncJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    next_poll_after: str | None = None
    result: JsonDict = Field(default_factory=dict)


class Job(BaseModel):
    id: str
    kind: str
    status: JobStatus
    target_type: str
    target_id: str
    idempotency_key: str | None = None
    external_task_id: str | None = None
    request: JsonDict = Field(default_factory=dict)
    result: JsonDict = Field(default_factory=dict)
    error: JsonDict = Field(default_factory=dict)
    next_poll_after: str | None = None
    created_at: str
    updated_at: str


class JobRetryRequest(BaseModel):
    reason: str = ""


class JobCancelRequest(BaseModel):
    reason: str = ""


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    database: Literal["ok"]
    schema_version: int


class StatsResponse(BaseModel):
    papers: int
    paper_versions: int
    artifacts: int
    jobs: dict[str, int]
    reports: int
    invention_candidates: int
    patent_drafts: int


class Topic(BaseModel):
    id: str
    name_zh: str
    name_en: str
    parent_id: str | None = None
    enabled: bool = True
    config_version_id: str | None = None
    daily_quota: int | None = None
    deleted_at: str | None = None
    aliases: list[str] = Field(default_factory=list)
    rules: JsonDict = Field(default_factory=dict)


class TopicCreate(BaseModel):
    name_zh: str = Field(min_length=1)
    name_en: str = ""
    parent_id: str | None = None
    enabled: bool = True
    daily_quota: int | None = Field(default=None, ge=1, le=500)
    aliases: list[str] = Field(default_factory=list)
    rules: JsonDict = Field(default_factory=dict)


class TopicPatch(BaseModel):
    name_zh: str | None = None
    name_en: str | None = None
    parent_id: str | None = None
    enabled: bool | None = None
    config_version_id: str | None = None
    daily_quota: int | None = Field(default=None, ge=1, le=500)
    aliases: list[str] | None = None
    rules: JsonDict | None = None


class TopicDigestNoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = ""


class TopicConfigVersion(BaseModel):
    id: str
    label: str
    active: bool = False
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str


class TopicAlias(BaseModel):
    id: str
    topic_id: str
    config_version_id: str | None = None
    alias: str
    alias_type: Literal["include", "exclude", "synonym"] = "include"
    weight: float = 1.0
    created_at: str


class TopicQuota(BaseModel):
    id: str
    topic_id: str
    config_version_id: str | None = None
    quota_type: Literal["daily", "weekly", "backfill"] = "daily"
    max_results: int = Field(ge=1, le=1000)
    priority: int = 50
    created_at: str


class Author(BaseModel):
    id: str
    display_name: str
    normalized_name: str
    orcid: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str


class Organization(BaseModel):
    id: str
    display_name: str
    normalized_name: str
    ror_id: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str


class Venue(BaseModel):
    id: str
    display_name: str
    normalized_name: str
    venue_type: str = "unknown"
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str


class PaperAuthorLink(BaseModel):
    paper_id: str
    author_id: str
    author_order: int = 0
    is_corresponding: bool = False
    affiliation_text: str = ""
    created_at: str


class PaperVenueLink(BaseModel):
    paper_id: str
    venue_id: str
    relation_type: str = "published_in"
    created_at: str


class PaperIdentifier(BaseModel):
    type: str
    value: str


class PaperCreate(BaseModel):
    canonical_title: str = Field(min_length=1)
    abstract: str = ""
    language: str = "en"
    first_publication_date: date | None = None
    status: str = "new"
    identifiers: list[PaperIdentifier] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)
    version: "PaperVersionCreate | None" = None
    source_hit: "PaperSourceHitCreate | None" = None


class Paper(BaseModel):
    id: str
    canonical_title: str
    abstract: str
    translated_abstract: str | None = None
    method_summary: str | None = None
    language: str
    first_publication_date: str | None = None
    current_version_id: str | None = None
    status: str
    selected: bool
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class PaperDetail(Paper):
    identifiers: list[PaperIdentifier] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    current_version: "PaperVersion | None" = None


class PaperVersionCreate(BaseModel):
    version_label: str = "v1"
    source: str = "manual"
    source_version_id: str | None = None
    publication_date: date | None = None
    pdf_url: str | None = None
    pdf_checksum: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class PaperVersion(BaseModel):
    id: str
    paper_id: str
    version_label: str
    source: str
    source_version_id: str | None = None
    publication_date: str | None = None
    pdf_url: str | None = None
    pdf_checksum: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class PaperSourceHitCreate(BaseModel):
    source: str
    query: str | None = None
    rank: int | None = None
    hit_date: date | None = None
    raw_summary: JsonDict = Field(default_factory=dict)


class PaperSourceHit(BaseModel):
    id: str
    paper_id: str
    paper_version_id: str | None = None
    source: str
    query: str | None = None
    rank: int | None = None
    hit_date: str | None = None
    raw_summary: JsonDict = Field(default_factory=dict)
    created_at: str


class PaperSelectRequest(BaseModel):
    selected: bool = True


class DiscoveryRunCreate(BaseModel):
    source: str = "manual"
    window_start: datetime | None = None
    window_end: datetime | None = None
    topics: list[str] = Field(default_factory=list)
    max_results: int | None = Field(default=None, ge=1, le=500)
    metadata: JsonDict = Field(default_factory=dict)


class DiscoveryRun(BaseModel):
    id: str
    source: str
    status: str
    window_start: str | None = None
    window_end: str | None = None
    topics: list[str] = Field(default_factory=list)
    max_results: int | None = None
    metadata: JsonDict = Field(default_factory=dict)
    job_id: str | None = None
    created_at: str
    updated_at: str


class PipelineRunCreate(BaseModel):
    run_type: str
    source: str = "manual"
    status: str = "queued"
    config_version_id: str | None = None
    discovery_run_id: str | None = None
    job_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    input_counts: JsonDict = Field(default_factory=dict)
    output_counts: JsonDict = Field(default_factory=dict)
    error_counts: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)


class PipelineRun(BaseModel):
    id: str
    run_type: str
    source: str
    status: str
    config_version_id: str | None = None
    discovery_run_id: str | None = None
    job_id: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    input_counts: JsonDict = Field(default_factory=dict)
    output_counts: JsonDict = Field(default_factory=dict)
    error_counts: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class VersionActionRequest(BaseModel):
    force: bool = False
    options: JsonDict = Field(default_factory=dict)


class Artifact(BaseModel):
    id: str
    paper_version_id: str | None = None
    patent_draft_id: str | None = None
    artifact_type: str
    uri: str
    media_type: str
    checksum: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str


class ArtifactCreate(BaseModel):
    artifact_type: str
    uri: str
    media_type: str = "application/octet-stream"
    checksum: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class PaperReport(BaseModel):
    id: str
    paper_version_id: str
    summary: str
    motivation: str = ""
    method: str = ""
    experiments: str = ""
    results: str = ""
    innovation: str = ""
    limitations: str = ""
    engineering_value: str = ""
    reproduction_plan: str = ""
    score: JsonDict = Field(default_factory=dict)
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    created_at: str
    updated_at: str


class PaperRelation(BaseModel):
    id: str
    from_paper_id: str
    to_paper_id: str
    relation_type: str
    reason: str
    evidence: list[JsonDict] = Field(default_factory=list)
    confidence: float
    created_at: str


class DailyDigest(BaseModel):
    date: str
    counts: dict[str, int]
    details: dict[str, list[JsonDict]] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)
    topic_distribution: dict[str, int] = Field(default_factory=dict)
    reading_routes: dict[str, list[str]] = Field(default_factory=dict)
    papers: list[Paper] = Field(default_factory=list)


class InventionSourceRef(BaseModel):
    paper_id: str | None = None
    paper_version_id: str | None = None
    technical_card_id: str | None = None
    contribution: str = ""

    @field_validator("paper_id", "paper_version_id", mode="before")
    @classmethod
    def blank_to_none(cls, value: Any) -> Any:
        if value == "":
            return None
        return value


class InventionCandidateCreate(BaseModel):
    title: str | None = None
    sources: list[InventionSourceRef] = Field(min_length=2, max_length=5)
    problem_statement: str = ""
    integration_mechanism: str = ""
    coupling_interface: str = ""
    data_or_control_flow: str = ""
    why_not_juxtaposition: str = ""
    expected_joint_effect: str = ""
    technical_effects: str = ""
    risk_notes: str = ""
    evidence: list[EvidenceAnchor] = Field(default_factory=list)


class InventionCandidate(BaseModel):
    id: str
    title: str
    status: str
    sources: list[InventionSourceRef]
    problem_statement: str
    integration_mechanism: str
    coupling_interface: str = ""
    data_or_control_flow: str = ""
    why_not_juxtaposition: str = ""
    expected_joint_effect: str = ""
    technical_effects: str
    risk_notes: str
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    gate: JsonDict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class CandidateComponent(BaseModel):
    id: str
    invention_candidate_id: str
    source_ref_index: int
    component_type: str
    name: str
    contribution: str
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    created_at: str


class IntegrationMechanismRecord(BaseModel):
    id: str
    invention_candidate_id: str
    mechanism_type: str
    coupling_interface: str
    data_or_control_flow: str
    why_not_juxtaposition: str
    expected_joint_effect: str
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    created_at: str


class CandidateApproveRequest(BaseModel):
    approved: bool = True
    notes: str = ""
    approver: str = ""
    contribution_confirmed: bool = False
    sanitization_confirmed: bool = False
    protection_focus_confirmed: bool = False
    unverified_facts_confirmed: bool = False
    override_prior_art: bool = False
    override_reason: str = ""


class DraftCreateRequest(BaseModel):
    case_name: str | None = None
    protection_focus: str = ""
    notes: str = ""


class PatentDraft(BaseModel):
    id: str
    invention_candidate_id: str
    case_name: str
    version_label: str
    status: str
    markdown: str
    self_check: JsonDict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class PatentDraftReviseRequest(BaseModel):
    section: str | None = None
    instruction: str
    notes: str = ""


class PatentStageRunCreate(BaseModel):
    stage: PatentStage
    status: PatentStageStatus = "pending"
    patent_draft_id: str | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    idempotency_key: str | None = None
    input: JsonDict = Field(default_factory=dict)
    output: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)


class PatentStageRunUpdate(BaseModel):
    status: PatentStageStatus | None = None
    artifact_id: str | None = None
    job_id: str | None = None
    output: JsonDict | None = None
    metadata: JsonDict | None = None


class PatentStageRun(BaseModel):
    id: str
    invention_candidate_id: str
    patent_draft_id: str | None = None
    stage: PatentStage
    status: PatentStageStatus
    input: JsonDict = Field(default_factory=dict)
    output: JsonDict = Field(default_factory=dict)
    artifact_id: str | None = None
    job_id: str | None = None
    idempotency_key: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str


PaperCreate.model_rebuild()
