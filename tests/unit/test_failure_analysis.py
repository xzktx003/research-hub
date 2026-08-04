"""Tests for LLM-driven failure analysis.

The ResearchJobService now attempts to summarize why a job/run failed using
the configured OpenAI-compatible model. These tests pin the behavior:
  * when the model is not configured -> graceful skip, never raise;
  * when a model returns a structured diagnosis -> it is persisted
    in the job's error.llm_analysis (or discovery run error);
  * when the model call itself fails -> a truthful analysis_error is
    recorded instead of crashing the job workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from research_hub import services as services_module
from research_hub.adapters import AdapterResult
from research_hub.models import PaperCreate, PaperVersionCreate
from research_hub.repository import Repository
from research_hub.services import ResearchJobService


logging.disable(logging.CRITICAL)


class FailingDownloader:
    def download(self, url: str, artifact_root: Path) -> AdapterResult:
        return AdapterResult.failed("boom", http_status=404, error="nope")


def _create_versioned_paper(repo: Repository):
    return repo.create_paper(
        PaperCreate(
            canonical_title="Failure Analysis Paper",
            abstract="An AI Infra paper about serving.",
            version=PaperVersionCreate(
                version_label="v1",
                source="test",
                source_version_id="failure-1",
                pdf_url="https://example.test/paper.pdf",
            ),
        )
    )


class DiagnosisAdapter:
    """Stand-in for OpenAICompatibleResearchAdapter with a scripted _chat."""

    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.user_prompts: list[str] = []

    def _chat(self, *, system: str, user: str) -> AdapterResult:
        self.user_prompts.append(user)
        return self.result


_FAILING_JSON = (
    '{"category":"network","reason":"OpenReview 403 导致发现失败",'
    '"detail":"OpenReview 拒绝匿名访问，arXiv 又缺少对应摘要，因此该主题没有可用结果。",'
    '"suggestion":"检查 OpenReview 网络与凭据；或暂时切换发现源到 arXiv 直连。"}'
)


def _configure_openai(monkeypatch, tmp_path: Path, *, model: str = "gpt") -> Path:
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "provider": "openai",
                    "openai": {"base_url": "http://llm.local/v1", "api_key": "", "model": model},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(config_path))
    return config_path


def _stub_default_analysis_adapter(adapter):
    """Patch the module-level adapter factory used by failure diagnosis."""
    original = services_module._default_analysis_adapter
    services_module._default_analysis_adapter = lambda config: adapter
    return original


def test_llm_analysis_skips_when_model_unconfigured(initialized_db, monkeypatch, tmp_path: Path) -> None:
    # No ANALYSIS config at all -> _analysis_configured() is False, and the
    # failure path must not raise.
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text('{"analysis":{"provider":"openai"}}', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(config_path))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.create_job("download", "paper_version", paper.current_version_id or "", {"source": "unit"})
        service = ResearchJobService(conn, downloader_adapter=FailingDownloader())
        payload = service.run_job(job.job_id)

        stored = conn.execute(
            "SELECT error_json FROM job WHERE id = ?", (job.job_id,)
        ).fetchone()
        error = json.loads(stored["error_json"])
        assert payload["status"] in {"retryable_failed", "terminal_failed"}
        assert error["llm_analysis"]["available"] is False
        assert "未配置" in error["llm_analysis"]["reason"]


def test_llm_analysis_persists_structured_diagnosis(initialized_db, monkeypatch, tmp_path: Path) -> None:
    _configure_openai(monkeypatch, tmp_path)
    adapter = DiagnosisAdapter(AdapterResult.ok("diagnosed", content=_FAILING_JSON, model="gpt"))
    original = _stub_default_analysis_adapter(adapter)
    try:
        with initialized_db.connect() as conn:
            repo = Repository(conn)
            paper = _create_versioned_paper(repo)
            job = repo.create_job("download", "paper_version", paper.current_version_id or "", {"source": "unit"})
            service = ResearchJobService(conn, downloader_adapter=FailingDownloader())
            payload = service.run_job(job.job_id)

            stored = conn.execute(
                "SELECT error_json FROM job WHERE id = ?", (job.job_id,)
            ).fetchone()
            error = json.loads(stored["error_json"])
            analysis = error["llm_analysis"]
            assert analysis["available"] is True
            assert analysis["category"] == "network"
            assert "OpenReview 403" in analysis["reason"]
            # The prompt received real failure context.
            assert any("download" in p for p in adapter.user_prompts)
    finally:
        services_module._default_analysis_adapter = original


def test_llm_analysis_crash_degrades_gracefully(initialized_db, monkeypatch, tmp_path: Path) -> None:
    _configure_openai(monkeypatch, tmp_path)

    class ExplodingAdapter:
        def _chat(self, **kwargs):
            raise RuntimeError("simulated outage")

    original = _stub_default_analysis_adapter(ExplodingAdapter())
    try:
        with initialized_db.connect() as conn:
            repo = Repository(conn)
            paper = _create_versioned_paper(repo)
            job = repo.create_job("download", "paper_version", paper.current_version_id or "", {"source": "unit"})
            service = ResearchJobService(conn, downloader_adapter=FailingDownloader())
            payload = service.run_job(job.job_id)

            stored = conn.execute(
                "SELECT error_json FROM job WHERE id = ?", (job.job_id,)
            ).fetchone()
            error = json.loads(stored["error_json"])
            assert payload["status"] in {"retryable_failed", "terminal_failed"}
            assert "simulated outage" in error["llm_analysis"]["analysis_error"]
    finally:
        services_module._default_analysis_adapter = original


def test_dify_provider_has_no_adhoc_diagnosis(initialized_db, monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "runtime-config.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis": {
                    "provider": "dify",
                    "dify": {"base_url": "http://dify.local", "api_key": "k", "workflow_id": "w"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(config_path))

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(repo)
        job = repo.create_job("download", "paper_version", paper.current_version_id or "", {"source": "unit"})
        service = ResearchJobService(conn, downloader_adapter=FailingDownloader())
        payload = service.run_job(job.job_id)

        stored = conn.execute(
            "SELECT error_json FROM job WHERE id = ?", (job.job_id,)
        ).fetchone()
        error = json.loads(stored["error_json"])
        assert payload["status"] in {"retryable_failed", "terminal_failed"}
        assert error["llm_analysis"]["reason"] == "当前分析提供商为 Dify，不支持即席失败诊断"
