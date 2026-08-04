from __future__ import annotations

from pathlib import Path

import httpx

from research_hub.adapters import (
    AdapterResult,
    CompositeDiscoveryAdapter,
    HuggingFaceDailyPapersAdapter,
    OpenAlexMetadataAdapter,
    OpenReviewDiscoveryAdapter,
)
from research_hub.adapters.types import TopicQuery


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "discovery"


class StaticDiscoveryAdapter:
    source = "arxiv"

    def discover(self, topic: TopicQuery) -> AdapterResult:
        return AdapterResult.ok(
            "ok",
            topic_id=topic.topic_id,
            source=self.source,
            papers=[
                {
                    "source": "arxiv",
                    "source_id": "2608.00001",
                    "stable_key": "doi:10.5555/spec.decode.runtime",
                    "title": "Speculative Decode Runtime",
                    "abstract": "A serving paper about speculative decoding and runtime scheduling.",
                    "authors": ["A. Researcher", "B. Builder"],
                    "published_at": "2026-08-02T01:00:00+00:00",
                    "updated_at": "2026-08-02T02:00:00+00:00",
                    "pdf_url": "https://arxiv.org/pdf/2608.00001",
                    "landing_url": "https://arxiv.org/abs/2608.00001",
                    "categories": ["cs.LG", "cs.DC"],
                    "doi": "10.5555/spec.decode.runtime",
                    "topic_id": topic.topic_id,
                    "score_reason": None,
                    "raw": {"entry_id": "https://arxiv.org/abs/2608.00001v1"},
                }
            ],
        )


class BrokenDiscoveryAdapter:
    source = "broken"

    def discover(self, topic: TopicQuery) -> AdapterResult:
        return AdapterResult.degraded("source timeout", topic_id=topic.topic_id, source=self.source, papers=[])


class InvalidOkDiscoveryAdapter:
    source = "invalid"

    def discover(self, topic: TopicQuery) -> AdapterResult:
        return AdapterResult.ok(
            "bad payload",
            topic_id=topic.topic_id,
            source=self.source,
            papers=[{"source": "invalid", "source_id": "bad", "abstract": "missing title"}],
        )


def topic() -> TopicQuery:
    return TopicQuery(
        topic_id="aif-03",
        display_name="推理与解码优化",
        include_terms=("speculative decoding",),
        max_results=5,
    )


def test_arxiv_hf_openreview_openalex_same_paper_normalizes_to_doi_stable_key() -> None:
    adapter = CompositeDiscoveryAdapter(
        (
            StaticDiscoveryAdapter(),
            HuggingFaceDailyPapersAdapter(fixture_path=FIXTURES / "huggingface_daily_papers.json"),
            OpenReviewDiscoveryAdapter(fixture_path=FIXTURES / "openreview_notes.json"),
            OpenAlexMetadataAdapter(fixture_path=FIXTURES / "openalex_works.json"),
        )
    )

    result = adapter.discover(topic())

    assert result.status == "ok"
    papers = result.data["papers"]
    assert [paper["source"] for paper in papers] == ["arxiv", "huggingface", "openreview", "openalex"]
    assert {paper["stable_key"] for paper in papers} == {"doi:10.5555/spec.decode.runtime"}
    assert {paper["title"] for paper in papers} == {"Speculative Decode Runtime"}
    assert all(paper["topic_id"] == "aif-03" for paper in papers)
    assert next(paper for paper in papers if paper["source"] == "openalex")["source_role"] == "enrichment"
    assert all(
        paper["source_role"] == "authoritative"
        for paper in papers
        if paper["source"] != "openalex"
    )


def test_composite_discovery_keeps_successful_sources_when_one_source_degrades() -> None:
    adapter = CompositeDiscoveryAdapter(
        (
            HuggingFaceDailyPapersAdapter(fixture_path=FIXTURES / "huggingface_daily_papers.json"),
            BrokenDiscoveryAdapter(),
        )
    )

    result = adapter.discover(topic())

    assert result.status == "degraded"
    assert [paper["source"] for paper in result.data["papers"]] == ["huggingface"]
    assert result.data["failures"] == [
        {"source": "broken", "status": "degraded", "message": "source timeout"}
    ]


def test_composite_discovery_isolates_contract_failures_from_other_sources() -> None:
    adapter = CompositeDiscoveryAdapter(
        (
            HuggingFaceDailyPapersAdapter(fixture_path=FIXTURES / "huggingface_daily_papers.json"),
            InvalidOkDiscoveryAdapter(),
        )
    )

    result = adapter.discover(topic())

    assert result.status == "degraded"
    assert [paper["source"] for paper in result.data["papers"]] == ["huggingface"]
    assert result.data["failures"][0]["source"] == "invalid"
    assert "missing required field(s): title" in result.data["failures"][0]["message"]


def test_unknown_source_fields_are_preserved_in_raw_payloads() -> None:
    result = HuggingFaceDailyPapersAdapter(
        fixture_path=FIXTURES / "huggingface_daily_papers.json"
    ).discover(topic())

    assert result.status == "ok"
    paper = result.data["papers"][0]
    assert paper["raw"]["unexpected_trending_field"] == {"kept": True}


def test_missing_required_source_fields_fail_loud() -> None:
    result = OpenReviewDiscoveryAdapter(
        fixture_path=FIXTURES / "openreview_missing_required.json"
    ).discover(topic())

    assert result.status == "failed"
    assert "missing required field(s): title" in result.message


def test_http_source_retries_429_and_preserves_successful_result() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "2608.00001",
                    "title": "Speculative Decode Runtime",
                    "abstract": "Speculative decoding for runtime scheduling.",
                    "authors": ["A. Researcher"],
                }
            ],
            request=request,
        )

    result = HuggingFaceDailyPapersAdapter(
        api_url="https://example.test/daily-papers",
        max_retries=1,
        retry_base_seconds=0.01,
        transport=httpx.MockTransport(handler),
    ).discover(topic())

    assert calls == 2
    assert result.status == "ok"
    assert result.data["papers"][0]["source_id"] == "2608.00001"


def test_openreview_uses_offset_pagination_until_requested_limit() -> None:
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset", "0")
        offsets.append(offset)
        index = int(offset)
        note = {
            "id": f"note-{index}",
            "forum": f"forum-{index}",
            "content": {
                "title": {"value": f"Speculative Decode Runtime {index}"},
                "abstract": {"value": "Speculative decoding for runtime scheduling."},
                "authors": {"value": ["A. Researcher"]},
            },
        }
        return httpx.Response(200, json={"notes": [note]}, request=request)

    paged_topic = TopicQuery(
        topic_id="aif-03",
        display_name="推理与解码优化",
        include_terms=("speculative decoding",),
        max_results=2,
    )
    result = OpenReviewDiscoveryAdapter(
        api_url="https://example.test/openreview",
        page_size=1,
        transport=httpx.MockTransport(handler),
    ).discover(paged_topic)

    assert offsets == ["0", "1"]
    assert result.status == "ok"
    assert len(result.data["papers"]) == 2
