"""arXiv Atom API discovery adapter with conservative retry behavior."""

from __future__ import annotations

import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .types import AdapterResult, PaperHit, TopicQuery


ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
DEFAULT_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_USER_AGENT = (
    "ResearchHub/0.1 (AI Infra paper discovery; contact: local-admin@example.invalid)"
)


AI_INFRA_TOPICS: tuple[TopicQuery, ...] = (
    TopicQuery(
        "quantization_pruning_compression",
        "量化、剪枝与模型压缩",
        ("quantization", "pruning", "model compression", "low bit", "fp8", "int4", "kv cache quantization"),
    ),
    TopicQuery(
        "efficient_model_architecture",
        "高效模型架构",
        ("mixture of experts", "sparse model", "linear attention", "state space model", "mamba", "early exit"),
    ),
    TopicQuery(
        "inference_decoding_serving",
        "推理与解码优化",
        ("speculative decoding", "paged attention", "continuous batching", "prefill decode disaggregation", "kv cache"),
    ),
    TopicQuery(
        "kernel_compiler_optimization",
        "算子与编译优化",
        ("kernel fusion", "flashattention", "triton", "cuda kernel", "mlir", "compiler optimization"),
    ),
    TopicQuery(
        "distributed_training",
        "分布式训练",
        ("tensor parallel", "pipeline parallel", "expert parallel", "fsdp", "zero optimizer", "checkpointing"),
    ),
    TopicQuery(
        "hardware_system_design",
        "异构硬件与系统设计",
        ("ai accelerator", "npu", "hbm", "cxl", "rdma", "nvlink", "chiplet", "hardware software co-design"),
    ),
)


class ArxivDiscoveryAdapter:
    source = "arxiv"
    _rate_limit_lock = threading.Lock()
    _last_request_at_by_url: dict[str, float] = {}

    def __init__(
        self,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = 30.0,
        request_interval_seconds: float = 3.0,
        max_retries: int = 4,
        retry_base_seconds: float = 2.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.max_retries = max(0, max_retries)
        self.retry_base_seconds = max(0.1, retry_base_seconds)
        self.user_agent = user_agent

    def discover(self, topic: TopicQuery) -> AdapterResult:
        query = self._build_query(topic)
        params = {
            "search_query": query,
            "start": "0",
            "max_results": str(topic.max_results),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            response_text = self._get_with_retry(params)
            hits = self._parse_feed(response_text, topic.topic_id)
        except Exception as exc:
            return AdapterResult.degraded(
                f"arXiv discovery unavailable: {exc}",
                topic_id=topic.topic_id,
                source="arxiv",
            )
        return AdapterResult.ok(
            f"discovered {len(hits)} arXiv papers",
            topic_id=topic.topic_id,
            papers=[_paper_hit_to_dict(hit) for hit in hits],
        )

    def _build_query(self, topic: TopicQuery) -> str:
        category_clause = " OR ".join(f"cat:{category}" for category in topic.categories)
        term_clause = " OR ".join(
            f'all:"{term}"' if " " in term else f"all:{term}"
            for term in topic.include_terms
        )
        query = f"({category_clause}) AND ({term_clause})"
        if topic.exclude_terms:
            excludes = " AND ".join(
                f'NOT all:"{term}"' if " " in term else f"NOT all:{term}"
                for term in topic.exclude_terms
            )
            query = f"{query} AND {excludes}"
        return query

    def _get_with_retry(self, params: dict[str, str]) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._respect_rate_limit()
            try:
                with httpx.Client(timeout=self.timeout_seconds, headers={"User-Agent": self.user_agent}) as client:
                    response = client.get(self.api_url, params=params)
                response.raise_for_status()
                return response.text
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                retry_after = _retry_after_seconds(getattr(exc, "response", None))
                delay = retry_after if retry_after is not None else self.retry_base_seconds * (2**attempt)
                time.sleep(min(delay, 60.0))
        assert last_error is not None
        raise last_error

    def _respect_rate_limit(self) -> None:
        with self._rate_limit_lock:
            last_request_at = self._last_request_at_by_url.get(self.api_url, 0.0)
            elapsed = time.monotonic() - last_request_at
            if elapsed < self.request_interval_seconds:
                time.sleep(self.request_interval_seconds - elapsed)
            self._last_request_at_by_url[self.api_url] = time.monotonic()

    def _parse_feed(self, text: str, topic_id: str) -> list[PaperHit]:
        root = ET.fromstring(text)
        hits: list[PaperHit] = []
        for entry in root.findall(f"{ATOM}entry"):
            arxiv_url = _child_text(entry, f"{ATOM}id")
            source_id = arxiv_url.rsplit("/", 1)[-1].strip()
            versionless_id = source_id.split("v", 1)[0]
            links = entry.findall(f"{ATOM}link")
            pdf_url = None
            landing_url = arxiv_url
            for link in links:
                href = link.attrib.get("href")
                if not href:
                    continue
                if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                    pdf_url = href
                elif link.attrib.get("rel") == "alternate":
                    landing_url = href
            authors = tuple(
                _child_text(author, f"{ATOM}name")
                for author in entry.findall(f"{ATOM}author")
                if _child_text(author, f"{ATOM}name")
            )
            categories = tuple(
                category.attrib["term"]
                for category in entry.findall(f"{ATOM}category")
                if category.attrib.get("term")
            )
            doi = _child_text(entry, f"{ARXIV}doi") or None
            hits.append(
                PaperHit(
                    source="arxiv",
                    source_id=versionless_id,
                    title=_clean_ws(_child_text(entry, f"{ATOM}title")),
                    abstract=_clean_ws(_child_text(entry, f"{ATOM}summary")),
                    authors=authors,
                    published_at=_parse_datetime(_child_text(entry, f"{ATOM}published")),
                    updated_at=_parse_datetime(_child_text(entry, f"{ATOM}updated")),
                    pdf_url=pdf_url,
                    landing_url=landing_url,
                    categories=categories,
                    doi=doi,
                    topic_id=topic_id,
                    raw={"entry_id": arxiv_url},
                )
            )
        return hits


def _child_text(element: ET.Element, name: str) -> str:
    child = element.find(name)
    return (child.text or "").strip() if child is not None else ""


def _clean_ws(value: str) -> str:
    return " ".join(value.split())


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


def _retry_after_seconds(response: Any) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _paper_hit_to_dict(hit: PaperHit) -> dict[str, Any]:
    return {
        "source": hit.source,
        "source_id": hit.source_id,
        "stable_key": hit.stable_key,
        "title": hit.title,
        "abstract": hit.abstract,
        "authors": list(hit.authors),
        "published_at": hit.published_at.isoformat() if hit.published_at else None,
        "updated_at": hit.updated_at.isoformat() if hit.updated_at else None,
        "pdf_url": hit.pdf_url,
        "landing_url": hit.landing_url,
        "categories": list(hit.categories),
        "doi": hit.doi,
        "topic_id": hit.topic_id,
        "score_reason": hit.score_reason,
        "raw": hit.raw,
    }
