"""Fixture-first multi-source paper discovery adapters."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .retry import RetryConfig, run_with_retry
from .types import AdapterResult, PaperHit, TopicQuery


DEFAULT_HF_DAILY_PAPERS_URL = "https://huggingface.co/api/daily_papers"
DEFAULT_OPENREVIEW_NOTES_URL = "https://api2.openreview.net/notes"
DEFAULT_OPENALEX_WORKS_URL = "https://api.openalex.org/works"


class DiscoveryContractError(ValueError):
    """Raised when a source payload cannot satisfy the normalized paper contract."""


class FixtureBackedDiscoveryAdapter:
    """Base class for adapters that should prefer deterministic fixture payloads."""

    source: str
    source_role = "authoritative"

    def __init__(
        self,
        *,
        fixture_path: Path | str | None = None,
        api_url: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.fixture_path = Path(fixture_path) if fixture_path is not None else None
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.retry_base_seconds = max(0.01, retry_base_seconds)
        self.transport = transport

    def discover(self, topic: TopicQuery) -> AdapterResult:
        try:
            payload = self._load_payload(topic)
            hits = [hit for hit in self._parse_payload(payload, topic) if _matches_topic(hit, topic)]
        except DiscoveryContractError as exc:
            return AdapterResult.failed(
                f"{self.source} discovery contract violation: {exc}",
                topic_id=topic.topic_id,
                source=self.source,
            )
        except Exception as exc:
            return AdapterResult.degraded(
                f"{self.source} discovery unavailable: {exc}",
                topic_id=topic.topic_id,
                source=self.source,
            )
        return AdapterResult.ok(
            f"discovered {len(hits)} {self.source} papers",
            topic_id=topic.topic_id,
            source=self.source,
            papers=[paper_hit_to_dict(hit, source_role=self.source_role) for hit in hits],
        )

    def _load_payload(self, topic: TopicQuery) -> Any:
        if self.fixture_path is not None:
            return json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return self._request_json(self._request_params(topic))

    def _request_json(self, params: Mapping[str, str]) -> Any:
        return run_with_retry(
            lambda: self._get_json(dict(params)),
            config=RetryConfig(
                max_attempts=self.max_retries + 1,
                base_delay=self.retry_base_seconds,
                max_delay=60.0,
                jitter=0.0,
            ),
        )

    def _get_json(self, params: dict[str, str]) -> Any:
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            response = client.get(self.api_url, params=params)
        response.raise_for_status()
        return response.json()

    def _request_params(self, topic: TopicQuery) -> dict[str, str]:
        return {"query": " ".join(topic.include_terms), "limit": str(topic.max_results)}

    def _parse_payload(self, payload: Any, topic: TopicQuery) -> list[PaperHit]:
        raise NotImplementedError


class HuggingFaceDailyPapersAdapter(FixtureBackedDiscoveryAdapter):
    """Hugging Face Daily Papers trending discovery."""

    source = "huggingface"

    def __init__(
        self,
        *,
        fixture_path: Path | str | None = None,
        api_url: str = DEFAULT_HF_DAILY_PAPERS_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            fixture_path=fixture_path,
            api_url=api_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            transport=transport,
        )

    def _parse_payload(self, payload: Any, topic: TopicQuery) -> list[PaperHit]:
        items = _items_from_payload(payload, "papers", "daily_papers", "results")
        return [self._paper_from_item(item, topic) for item in items[: topic.max_results]]

    def _paper_from_item(self, item: Mapping[str, Any], topic: TopicQuery) -> PaperHit:
        paper = _mapping_or_empty(item.get("paper"))
        merged = {**paper, **item}
        source_id = _first_str(
            merged,
            "source_id",
            "arxiv_id",
            "arxivId",
            "id",
            "paper_id",
        )
        title = _first_str(merged, "title", "paper_title")
        abstract = _first_str(merged, "abstract", "summary", "description")
        _require_fields(self.source, source_id=source_id, title=title, abstract=abstract)
        return PaperHit(
            source=self.source,
            source_id=source_id,
            title=title,
            abstract=abstract,
            authors=_authors(merged.get("authors") or merged.get("paper_authors")),
            published_at=_parse_datetime(_first_str(merged, "published_at", "publishedAt", "date")),
            updated_at=_parse_datetime(_first_str(merged, "updated_at", "updatedAt")),
            pdf_url=_first_str(merged, "pdf_url", "pdfUrl", "pdf"),
            landing_url=_first_str(merged, "landing_url", "url", "html_url"),
            categories=tuple(_as_str_list(merged.get("categories") or merged.get("tags"))),
            doi=_normalize_doi(_first_str(merged, "doi")),
            topic_id=topic.topic_id,
            score_reason=_score_reason(merged, ("votes", "upvotes", "likes")),
            raw=dict(item),
        )


class OpenReviewDiscoveryAdapter(FixtureBackedDiscoveryAdapter):
    """OpenReview note discovery adapter."""

    source = "openreview"

    def __init__(
        self,
        *,
        fixture_path: Path | str | None = None,
        api_url: str = DEFAULT_OPENREVIEW_NOTES_URL,
        timeout_seconds: float = 30.0,
        invitation: str | None = None,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        page_size: int = 1000,
    ) -> None:
        super().__init__(
            fixture_path=fixture_path,
            api_url=api_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            transport=transport,
        )
        self.invitation = invitation
        self.page_size = max(1, min(page_size, 1000))

    def _request_params(self, topic: TopicQuery) -> dict[str, str]:
        params = {"limit": str(topic.max_results)}
        if self.invitation:
            params["invitation"] = self.invitation
        return params

    def _load_payload(self, topic: TopicQuery) -> Any:
        if self.fixture_path is not None:
            return super()._load_payload(topic)
        collected: list[Mapping[str, Any]] = []
        offset = 0
        while len(collected) < topic.max_results:
            page_size = min(self.page_size, topic.max_results - len(collected))
            params = self._request_params(topic) | {"limit": str(page_size), "offset": str(offset)}
            payload = self._request_json(params)
            page = _items_from_payload(payload, "notes", "results")
            collected.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
        return {"notes": collected}

    def _parse_payload(self, payload: Any, topic: TopicQuery) -> list[PaperHit]:
        items = _items_from_payload(payload, "notes", "results")
        hits = []
        for item in items:
            hit = self._paper_from_note(item, topic)
            # The OpenReview /notes endpoint has no free-text query parameter,
            # so apply the topic keyword filter after fetching so results match
            # the requested topic instead of returning the wall of latest notes.
            if _matches_topic(hit, topic):
                hits.append(hit)
            if len(hits) >= topic.max_results:
                break
        return hits

    def _paper_from_note(self, item: Mapping[str, Any], topic: TopicQuery) -> PaperHit:
        content = _mapping_or_empty(item.get("content"))
        source_id = _first_str(item, "id", "forum", "number")
        forum_id = _first_str(item, "forum") or source_id
        title = _content_str(content, "title") or _first_str(item, "title")
        abstract = _content_str(content, "abstract") or _first_str(item, "abstract")
        _require_fields(self.source, source_id=source_id, title=title, abstract=abstract)
        authors_value = _content_value(content, "authors") or _content_value(content, "authorids")
        return PaperHit(
            source=self.source,
            source_id=source_id,
            title=title,
            abstract=abstract,
            authors=_authors(authors_value),
            published_at=_timestamp_ms(item.get("pdate") or item.get("cdate")),
            updated_at=_timestamp_ms(item.get("mdate")),
            pdf_url=_content_str(content, "pdf") or None,
            landing_url=f"https://openreview.net/forum?id={forum_id}",
            categories=tuple(_as_str_list(_content_value(content, "keywords"))),
            doi=_normalize_doi(_content_str(content, "doi") or _first_str(item, "doi")),
            topic_id=topic.topic_id,
            raw=dict(item),
        )


class OpenAlexMetadataAdapter(FixtureBackedDiscoveryAdapter):
    """OpenAlex metadata enrichment discovery adapter."""

    source = "openalex"
    source_role = "enrichment"

    def __init__(
        self,
        *,
        fixture_path: Path | str | None = None,
        api_url: str = DEFAULT_OPENALEX_WORKS_URL,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        page_size: int = 200,
    ) -> None:
        super().__init__(
            fixture_path=fixture_path,
            api_url=api_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
            transport=transport,
        )
        self.page_size = max(1, min(page_size, 200))

    def _request_params(self, topic: TopicQuery) -> dict[str, str]:
        return {"search": " ".join(topic.include_terms), "per-page": str(topic.max_results)}

    def _load_payload(self, topic: TopicQuery) -> Any:
        if self.fixture_path is not None:
            return super()._load_payload(topic)
        collected: list[Mapping[str, Any]] = []
        cursor = "*"
        while len(collected) < topic.max_results and cursor:
            page_size = min(self.page_size, topic.max_results - len(collected))
            params = self._request_params(topic) | {"per-page": str(page_size), "cursor": cursor}
            payload = self._request_json(params)
            page = _items_from_payload(payload, "results", "works")
            collected.extend(page)
            meta = _mapping_or_empty(payload.get("meta") if isinstance(payload, Mapping) else None)
            next_cursor = _first_str(meta, "next_cursor")
            if len(page) < page_size or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return {"results": collected}

    def _parse_payload(self, payload: Any, topic: TopicQuery) -> list[PaperHit]:
        items = _items_from_payload(payload, "results", "works")
        return [self._paper_from_work(item, topic) for item in items[: topic.max_results]]

    def _paper_from_work(self, item: Mapping[str, Any], topic: TopicQuery) -> PaperHit:
        source_id = _first_str(item, "id", "openalex_id")
        title = _first_str(item, "title", "display_name")
        abstract = _first_str(item, "abstract")
        if not abstract and isinstance(item.get("abstract_inverted_index"), Mapping):
            abstract = _abstract_from_inverted_index(item["abstract_inverted_index"])
        _require_fields(self.source, source_id=source_id, title=title, abstract=abstract)
        primary_location = _mapping_or_empty(item.get("primary_location"))
        best_oa_location = _mapping_or_empty(item.get("best_oa_location"))
        return PaperHit(
            source=self.source,
            source_id=source_id,
            title=title,
            abstract=abstract,
            authors=_openalex_authors(item.get("authorships")),
            published_at=_parse_datetime(_first_str(item, "publication_date")),
            updated_at=_parse_datetime(_first_str(item, "updated_date")),
            pdf_url=_first_str(best_oa_location, "pdf_url") or _first_str(primary_location, "pdf_url"),
            landing_url=_first_str(primary_location, "landing_page_url") or source_id,
            categories=tuple(_openalex_topics(item)),
            doi=_normalize_doi(_first_str(item, "doi")),
            topic_id=topic.topic_id,
            score_reason=_score_reason(item, ("cited_by_count",)),
            raw=dict(item),
        )


class CompositeDiscoveryAdapter:
    """Run independent discovery sources and return all successful hits."""

    def __init__(self, adapters: Sequence[Any]) -> None:
        self.adapters = tuple(adapters)

    def discover(self, topic: TopicQuery) -> AdapterResult:
        papers: list[dict[str, Any]] = []
        sources: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        for adapter in self.adapters:
            source = str(getattr(adapter, "source", adapter.__class__.__name__))
            try:
                result = adapter.discover(topic)
            except Exception as exc:
                result = AdapterResult.degraded(str(exc), source=source, topic_id=topic.topic_id)
            if result.status == "ok":
                try:
                    source_papers = _validated_paper_dicts(result.data.get("papers", []), source)
                except DiscoveryContractError as exc:
                    failures.append({"source": source, "status": "failed", "message": str(exc)})
                    continue
                source_role = str(getattr(adapter, "source_role", "authoritative"))
                for paper in source_papers:
                    paper.setdefault("source_role", source_role)
                papers.extend(source_papers)
                sources.append({"source": source, "status": "ok", "message": result.message})
            else:
                failures.append({"source": source, "status": result.status, "message": result.message})

        if papers or (sources and not failures):
            status = "ok" if not failures else "degraded"
            message = f"discovered {len(papers)} papers from {len(sources)} successful sources"
            return AdapterResult(
                status,
                message,
                {
                    "topic_id": topic.topic_id,
                    "papers": papers,
                    "sources": sources,
                    "failures": failures,
                },
            )
        return AdapterResult.failed(
            "all discovery sources failed",
            topic_id=topic.topic_id,
            papers=[],
            sources=sources,
            failures=failures,
        )


def paper_hit_to_dict(hit: PaperHit, *, source_role: str = "authoritative") -> dict[str, Any]:
    return {
        "source": hit.source,
        "source_role": source_role,
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


def _items_from_payload(payload: Any, *keys: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = None
        for key in keys:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                items = candidate
                break
        if items is None:
            raise DiscoveryContractError(f"expected one of {', '.join(keys)} to contain a list")
    else:
        raise DiscoveryContractError("expected JSON object or list payload")
    if not all(isinstance(item, Mapping) for item in items):
        raise DiscoveryContractError("expected every paper item to be an object")
    return list(items)


def _validated_paper_dicts(papers: Any, source: str) -> list[dict[str, Any]]:
    if not isinstance(papers, list):
        raise DiscoveryContractError(f"{source} returned non-list papers")
    validated: list[dict[str, Any]] = []
    for index, paper in enumerate(papers):
        if not isinstance(paper, dict):
            raise DiscoveryContractError(f"{source} paper {index} is not an object")
        _require_fields(
            source,
            source_id=str(paper.get("source_id") or ""),
            title=str(paper.get("title") or ""),
            abstract=str(paper.get("abstract") or ""),
        )
        if paper.get("source") != source:
            raise DiscoveryContractError(f"{source} paper {index} has wrong source {paper.get('source')!r}")
        validated.append(paper)
    return validated


def _require_fields(source: str, **fields: str | None) -> None:
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise DiscoveryContractError(f"{source} paper missing required field(s): {', '.join(missing)}")


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_str(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (Mapping, list, tuple)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _content_value(content: Mapping[str, Any], key: str) -> Any:
    value = content.get(key)
    if isinstance(value, Mapping) and "value" in value:
        return value["value"]
    return value


def _content_str(content: Mapping[str, Any], key: str) -> str:
    value = _content_value(content, key)
    return value.strip() if isinstance(value, str) else ""


def _authors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return ()
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, Mapping):
            name = _first_str(item, "name", "display_name", "full_name")
            if name:
                names.append(name)
    return tuple(names)


def _openalex_authors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for authorship in value:
        if not isinstance(authorship, Mapping):
            continue
        author = _mapping_or_empty(authorship.get("author"))
        name = _first_str(author, "display_name")
        if name:
            names.append(name)
    return tuple(names)


def _openalex_topics(item: Mapping[str, Any]) -> list[str]:
    concepts = item.get("concepts")
    if not isinstance(concepts, list):
        return []
    return [
        _first_str(concept, "display_name")
        for concept in concepts
        if isinstance(concept, Mapping) and _first_str(concept, "display_name")
    ]


def _abstract_from_inverted_index(index: Mapping[str, Any]) -> str:
    positions: dict[int, str] = {}
    for token, offsets in index.items():
        if not isinstance(token, str) or not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int):
                positions[offset] = token
    return " ".join(positions[position] for position in sorted(positions))


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _normalize_doi(value: str) -> str | None:
    if not value:
        return None
    doi = value.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    return doi.lower() or None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _score_reason(item: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if item.get(key) is not None:
            return f"{key}={item[key]}"
    return None


def _matches_topic(hit: PaperHit, topic: TopicQuery) -> bool:
    haystack = " ".join((hit.title, hit.abstract, *hit.categories)).casefold()
    if any(term.casefold() in haystack for term in topic.exclude_terms):
        return False
    return not topic.include_terms or any(term.casefold() in haystack for term in topic.include_terms)
