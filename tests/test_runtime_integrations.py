from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys

import httpx

from research_hub.adapters import AdapterResult, ReadingReportRequest
from research_hub.adapters.openai_compatible import OpenAICompatibleResearchAdapter
from research_hub.adapters.patent import PatentEngineAdapter
from research_hub.adapters.prior_art import LocalCnipaPriorArtAdapter
from research_hub.runtime_config import (
    load_runtime_config,
    public_runtime_config,
    update_runtime_config,
)


def test_runtime_config_persists_secrets_server_side_with_restricted_permissions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config" / "runtime.json"

    saved = update_runtime_config(
        {
            "analysis": {
                "provider": "openai",
                "unknown_provider": {"api_key": "must-not-persist"},
                "openai": {
                    "base_url": "http://model.internal/v1/",
                    "api_key": "secret-value",
                    "model": "research-model",
                },
            },
            "schedule": {"daily_hour": 8, "after_parse": ["analyze", "translate"]},
        },
        path=path,
    )

    assert saved["analysis"]["openai"]["base_url"] == "http://model.internal/v1"
    assert load_runtime_config(path)["analysis"]["openai"]["api_key"] == "secret-value"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    public = public_runtime_config(saved)
    assert "unknown_provider" not in saved["analysis"]
    assert "api_key" not in public["analysis"]["openai"]
    assert public["analysis"]["openai"]["api_key_configured"] is True

    update_runtime_config(
        {"analysis": {"openai": {"api_key": None, "model": "research-model-v2"}}},
        path=path,
    )
    reloaded = load_runtime_config(path)
    assert reloaded["analysis"]["openai"]["api_key"] == "secret-value"
    assert reloaded["analysis"]["openai"]["model"] == "research-model-v2"


def test_openai_compatible_adapter_parses_structured_report(monkeypatch) -> None:
    report = {
        "summary": "摘要",
        "motivation": "动机",
        "method": "方法",
        "experiments": "实验",
        "results": "结果",
        "innovation": "创新",
        "limitations": "局限",
        "engineering_value": "工程价值",
        "reproduction_plan": "复现计划",
        "score": {"overall": 8},
        "evidence": [
            {
                "kind": "fact",
                "source": "paper_version:pv-test",
                "report_field": "method",
                "section": "Method",
                "quote": "source quote",
            }
        ],
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"```json\n{json.dumps(report)}\n```"}}]},
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "research_hub.adapters.openai_compatible.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    adapter = OpenAICompatibleResearchAdapter(
        base_url="http://model.test/v1",
        api_key="model-key",
        model="research-model",
    )

    result = adapter.run_report(
        ReadingReportRequest(
            paper_id="paper-test",
            title="Test Paper",
            abstract="Abstract",
            markdown="# Method\nsource quote",
            metadata={"paper_version_id": "pv-test", "task": "analyze"},
        )
    )

    assert result.status == "ok"
    assert result.data["report"]["method"] == "方法"
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer model-key"


def test_openai_compatible_adapter_translates_abstract_without_api_key(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "这是自动生成的中文摘要。"}}]},
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "research_hub.adapters.openai_compatible.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    adapter = OpenAICompatibleResearchAdapter(
        base_url="http://model.test/v1",
        model="translation-model",
    )

    result = adapter.run_report(
        ReadingReportRequest(
            paper_id="paper-test",
            title="Translation Test",
            abstract="An English abstract.",
            metadata={"task": "translate_abstract"},
        )
    )

    assert result.status == "ok"
    assert result.data["abstract_zh"] == "这是自动生成的中文摘要。"
    assert "authorization" not in requests[0].headers
    payload = json.loads(requests[0].content)
    assert "An English abstract." in payload["messages"][1]["content"]


def test_local_cnipa_adapter_uses_current_interpreter(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "cnipa_epub_search.py"
    script.write_text("# test fixture\n", encoding="utf-8")
    adapter = LocalCnipaPriorArtAdapter(script_path=script)
    monkeypatch.setattr(adapter, "health", lambda: AdapterResult.ok("ready"))
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        hit = {
            "title": "一种联合调度方法",
            "pub_number": "CN123456789A",
            "link": "http://epub.cnipa.gov.cn/patent/CN123456789A",
            "abstract": "本发明公开了一种联合调度和反馈控制方法。",
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"EPUB_HITS_JSON: {json.dumps([hit], ensure_ascii=False)}\n",
            stderr="",
        )

    monkeypatch.setattr("research_hub.adapters.prior_art.subprocess.run", fake_run)

    result = adapter.search({"query_terms": ["调度"], "max_results": 5})

    assert result.status == "ok"
    assert calls[0][0][0] == sys.executable
    assert "HTTP_PROXY" not in calls[0][1]["env"]
    assert result.data["records"][0]["publication_number"] == "CN123456789A"


def test_local_cnipa_adapter_does_not_treat_cwd_as_unset_skill_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("PATENT_DISCLOSURE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    adapter = LocalCnipaPriorArtAdapter()

    assert adapter.script_path.name == "cnipa_epub_search.py"
    assert adapter.script_path.parent.name == "tools"
    assert adapter.script_path.parent.parent.name == "patent-disclosure-skill"


def test_patent_docx_export_renders_mermaid_figures(monkeypatch, tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    browser_libs = tmp_path / "chromium-libs"
    browser_libs.mkdir()
    monkeypatch.setenv("RESEARCH_HUB_CHROMIUM_LIB_DIR", str(browser_libs))
    docx_tool = tools / "md_to_docx.py"
    renderer = tools / "mermaid_render.py"
    docx_tool.write_text("# test fixture\n", encoding="utf-8")
    renderer.write_text("# test fixture\n", encoding="utf-8")
    markdown = tmp_path / "disclosure.md"
    markdown.write_text(
        "# 技术交底书\n\n```mermaid\nflowchart LR\nA --> B\n```\n",
        encoding="utf-8",
    )
    docx = tmp_path / "disclosure.docx"
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        markdown.write_text(
            markdown.read_text(encoding="utf-8")
            + "<!-- ![图示 1](mermaid_figures/fig_001.png) -->\n",
            encoding="utf-8",
        )
        docx.write_bytes(b"docx fixture")
        return subprocess.CompletedProcess(command, 0, stdout="rendered", stderr="")

    monkeypatch.setattr("research_hub.adapters.patent.subprocess.run", fake_run)

    result = PatentEngineAdapter(md_to_docx_path=docx_tool).export_docx(markdown, docx)

    assert result.status == "ok"
    assert calls[0][0][1] == str(renderer)
    assert calls[0][1]["env"]["LD_LIBRARY_PATH"].split(":", 1)[0] == str(browser_libs)
    assert result.data["rendered_mermaid"] == 1
    assert docx.is_file()


def test_runtime_config_env_backfilled_marker_uses_dotenv_fallback(monkeypatch, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_BASE_URL=http://llm.local/v1\nLLM_MODEL=translator\n", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_HUB_ENV_FILE", str(env))
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    config = load_runtime_config(tmp_path / "empty-runtime.json")
    assert config["analysis"]["openai"]["base_url"] == "http://llm.local/v1"
    assert config["analysis"]["openai"]["model"] == "translator"
    assert public_runtime_config(config)["env_backfilled"] is True


def test_runtime_config_env_backfilled_false_without_dotenv(tmp_path: Path) -> None:
    config = load_runtime_config(tmp_path / "empty-runtime.json")
    assert public_runtime_config(config)["env_backfilled"] is False