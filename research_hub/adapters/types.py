"""Shared adapter contracts for the Research Hub integration layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol


AdapterStatus = Literal["ok", "degraded", "failed"]


@dataclass(frozen=True)
class AdapterResult:
    """Common result envelope for integrations that may be unavailable."""

    status: AdapterStatus
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, message: str, **data: Any) -> "AdapterResult":
        return cls("ok", message, data)

    @classmethod
    def degraded(cls, message: str, **data: Any) -> "AdapterResult":
        return cls("degraded", message, data)

    @classmethod
    def failed(cls, message: str, **data: Any) -> "AdapterResult":
        return cls("failed", message, data)


@dataclass(frozen=True)
class TopicQuery:
    """A configured AI Infra topic search request."""

    topic_id: str
    display_name: str
    include_terms: tuple[str, ...]
    categories: tuple[str, ...] = ("cs.AI", "cs.LG", "cs.CL", "cs.AR", "cs.DC")
    exclude_terms: tuple[str, ...] = ()
    max_results: int = 25


@dataclass(frozen=True)
class PaperHit:
    """Normalized paper discovery hit."""

    source: str
    source_id: str
    title: str
    abstract: str
    authors: tuple[str, ...]
    published_at: datetime | None
    updated_at: datetime | None
    pdf_url: str | None
    landing_url: str | None
    categories: tuple[str, ...] = ()
    doi: str | None = None
    topic_id: str | None = None
    score_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower()}"
        return f"{self.source}:{self.source_id.lower()}"


@dataclass(frozen=True)
class ArtifactRecord:
    """File artifact registration data independent of a database implementation."""

    artifact_id: str
    kind: str
    path: Path
    size_bytes: int
    sha256: str
    content_type: str = "application/octet-stream"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReadingReportRequest:
    paper_id: str
    title: str
    abstract: str
    markdown: str | None = None
    pdf_url: str | None = None
    artifact_refs: tuple[dict[str, Any], ...] = ()
    sections: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MinerUJobRequest:
    pdf_path: Path
    artifact_id: str | None = None
    artifact_uri: str | None = None
    backend: str = "pipeline"
    language: str = "auto"
    extract: tuple[str, ...] = ("markdown", "json", "images", "tables", "formulas")
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TechnicalCard:
    card_id: str
    paper_id: str
    title: str
    technical_problem: str
    method: str
    system_components: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatentCandidate:
    candidate_id: str
    title: str
    source_cards: tuple[TechnicalCard, ...]
    technical_problem: str
    combined_solution: str
    technical_effects: tuple[str, ...]
    novelty_risks: tuple[str, ...]
    implementation_gaps: tuple[str, ...]
    gate_status: AdapterStatus
    gate_reasons: tuple[str, ...]


class DiscoveryAdapter(Protocol):
    def discover(self, topic: TopicQuery) -> AdapterResult:
        ...


class PaperReadingAdapter(Protocol):
    def run_report(self, request: ReadingReportRequest) -> AdapterResult:
        ...


class PdfParsingAdapter(Protocol):
    def submit(self, request: MinerUJobRequest) -> AdapterResult:
        ...

    def status(self, job_id: str) -> AdapterResult:
        ...
