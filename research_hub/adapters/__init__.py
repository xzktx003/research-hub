"""Research Hub integration adapters."""

from .arxiv import AI_INFRA_TOPICS, ArxivDiscoveryAdapter
from .dify import DifyPaperDigestAdapter
from .discovery import (
    CompositeDiscoveryAdapter,
    DiscoveryContractError,
    HuggingFaceDailyPapersAdapter,
    OpenAlexMetadataAdapter,
    OpenReviewDiscoveryAdapter,
)
from .mineru import MinerUApiAdapter, MinerUWebAppAdapter
from .openai_compatible import OpenAICompatibleResearchAdapter
from .patent import PatentEngineAdapter
from .pdf_preflight import PdfPreflightIssue, preflight_markdown_for_pdf
from .storage import FileArtifactStore
from .types import (
    AdapterResult,
    ArtifactRecord,
    MinerUJobRequest,
    PaperHit,
    PatentCandidate,
    ReadingReportRequest,
    TechnicalCard,
    TopicQuery,
)

__all__ = [
    "AI_INFRA_TOPICS",
    "AdapterResult",
    "ArtifactRecord",
    "ArxivDiscoveryAdapter",
    "CompositeDiscoveryAdapter",
    "DifyPaperDigestAdapter",
    "DiscoveryContractError",
    "FileArtifactStore",
    "HuggingFaceDailyPapersAdapter",
    "MinerUApiAdapter",
    "MinerUJobRequest",
    "MinerUWebAppAdapter",
    "OpenAlexMetadataAdapter",
    "OpenAICompatibleResearchAdapter",
    "OpenReviewDiscoveryAdapter",
    "PaperHit",
    "PatentCandidate",
    "PatentEngineAdapter",
    "PdfPreflightIssue",
    "ReadingReportRequest",
    "TechnicalCard",
    "TopicQuery",
    "preflight_markdown_for_pdf",
]
