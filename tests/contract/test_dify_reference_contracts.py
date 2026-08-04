from __future__ import annotations

from research_hub.adapters.dify import DifyPaperDigestAdapter
from research_hub.adapters.types import ReadingReportRequest


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"workflow_run_id": "run-1", "data": {"outputs": {"report": {}}}}


class _FakeClient:
    captured: dict = {}

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def post(self, url: str, *, headers: dict, json: dict) -> _FakeResponse:
        self.captured = {"url": url, "headers": headers, "json": json, "timeout": self.timeout}
        type(self).captured = self.captured
        return _FakeResponse()


def test_dify_payload_uses_artifact_and_section_refs_without_inline_markdown(monkeypatch) -> None:
    monkeypatch.delenv("DIFY_INLINE_MARKDOWN", raising=False)
    monkeypatch.setattr("research_hub.adapters.dify.httpx.Client", _FakeClient)

    artifact_refs = (
        {
            "artifact_id": "art-1",
            "artifact_type": "markdown_original",
            "uri": "file:///tmp/paper.md",
            "checksum": "sha256",
        },
    )
    section_refs = (
        {
            "section_id": "section-1",
            "title": "Method",
            "start": 0,
            "end": 42,
            "char_count": 42,
        },
    )
    result = DifyPaperDigestAdapter(base_url="https://dify.example", api_key="secret").run_report(
        ReadingReportRequest(
            paper_id="paper-1",
            title="Reference Contract",
            abstract="A paper.",
            markdown="# Raw markdown must stay out of the default payload",
            pdf_url="https://example.test/paper.pdf",
            artifact_refs=artifact_refs,
            sections=section_refs,
            metadata={"paper_version_id": "pver-1", "task": "analyze"},
        )
    )

    inputs = _FakeClient.captured["json"]["inputs"]
    assert result.status == "ok"
    assert "markdown" not in inputs
    assert inputs["artifact_refs"] == list(artifact_refs)
    assert inputs["section_refs"] == list(section_refs)
    assert inputs["paper_package"] == {
        "artifact_refs": list(artifact_refs),
        "section_refs": list(section_refs),
    }


def test_dify_payload_legacy_inline_markdown_requires_explicit_flag(monkeypatch) -> None:
    monkeypatch.setenv("DIFY_INLINE_MARKDOWN", "true")
    monkeypatch.setenv("DIFY_INLINE_MARKDOWN_MAX_CHARS", "8")
    monkeypatch.setattr("research_hub.adapters.dify.httpx.Client", _FakeClient)

    DifyPaperDigestAdapter(base_url="https://dify.example", api_key="secret").run_report(
        ReadingReportRequest(
            paper_id="paper-1",
            title="Legacy",
            abstract="A paper.",
            markdown="1234567890",
        )
    )

    assert _FakeClient.captured["json"]["inputs"]["markdown"] == "12345678"


def test_dify_standard_workflow_api_receives_abstract_translation_task(monkeypatch) -> None:
    monkeypatch.setattr("research_hub.adapters.dify.httpx.Client", _FakeClient)

    result = DifyPaperDigestAdapter(
        base_url="https://dify.example",
        api_key="app-secret",
    ).run_report(
        ReadingReportRequest(
            paper_id="paper-abstract",
            title="Abstract Translation",
            abstract="An English abstract.",
            metadata={"task": "translate_abstract"},
        )
    )

    assert result.status == "ok"
    assert _FakeClient.captured["url"] == "https://dify.example/v1/workflows/run"
    assert _FakeClient.captured["headers"]["Authorization"] == "Bearer app-secret"
    inputs = _FakeClient.captured["json"]["inputs"]
    assert inputs["abstract"] == "An English abstract."
    assert inputs["task"] == "translate_abstract"
    assert inputs["metadata"]["task"] == "translate_abstract"
