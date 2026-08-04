from __future__ import annotations

from pathlib import Path

import httpx

from research_hub.adapters import (
    ArxivDiscoveryAdapter,
    DifyPaperDigestAdapter,
    MinerUApiAdapter,
    MinerUJobRequest,
    MinerUWebAppAdapter,
)
from research_hub.adapters.types import TopicQuery


def test_dify_adapter_reports_degraded_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("DIFY_BASE_URL", raising=False)
    monkeypatch.delenv("DIFY_API_KEY", raising=False)

    result = DifyPaperDigestAdapter().run_report(
        request=__import__("research_hub.adapters.types", fromlist=["ReadingReportRequest"]).ReadingReportRequest(
            paper_id="paper-1",
            title="Paper",
            abstract="Abstract",
        )
    )

    assert result.status == "degraded"


def test_mineru_adapter_reports_degraded_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("MINERU_BASE_URL", raising=False)

    result = MinerUWebAppAdapter().health()

    assert result.status == "degraded"


def test_mineru_adapter_fails_when_pdf_path_is_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_BASE_URL", "http://mineru.test")

    result = MinerUWebAppAdapter().submit(MinerUJobRequest(pdf_path=tmp_path / "missing.pdf"))

    assert result.status == "failed"


def test_mineru_api_status_exposes_404_as_recoverable_metadata(monkeypatch) -> None:
    monkeypatch.setenv("MINERU_BASE_URL", "http://mineru.test")
    request = httpx.Request("GET", "http://mineru.test/tasks/missing")
    response = httpx.Response(404, request=request)

    def raise_404(_path):
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    adapter = MinerUApiAdapter()
    monkeypatch.setattr(adapter, "_get", raise_404)

    result = adapter.status("missing")

    assert result.status == "degraded"
    assert result.data["http_status"] == 404
    assert result.data["job_id"] == "missing"


def test_arxiv_adapter_degrades_when_source_request_fails(monkeypatch) -> None:
    adapter = ArxivDiscoveryAdapter(max_retries=0, request_interval_seconds=0)
    monkeypatch.setattr(adapter, "_get_with_retry", lambda _params: (_ for _ in ()).throw(TimeoutError("boom")))

    result = adapter.discover(
        TopicQuery(
            topic_id="aif-04",
            display_name="推理服务与运行时",
            include_terms=("prefill decode disaggregation",),
            max_results=1,
        )
    )

    assert result.status == "degraded"
