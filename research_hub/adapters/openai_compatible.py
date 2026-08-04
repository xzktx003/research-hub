"""OpenAI-compatible adapter for paper analysis and optional translation."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .types import AdapterResult, ReadingReportRequest


REPORT_FIELDS = (
    "summary",
    "motivation",
    "method",
    "experiments",
    "results",
    "innovation",
    "limitations",
    "engineering_value",
    "reproduction_plan",
)


class OpenAICompatibleResearchAdapter:
    """Generate structured research reports through ``/chat/completions``."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str,
        timeout_seconds: float = 300.0,
        max_input_chars: int = 120_000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_input_chars = max_input_chars

    def health(self) -> AdapterResult:
        if not self.base_url or not self.model:
            return AdapterResult.degraded("OpenAI-compatible model is not configured")
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
            response.raise_for_status()
        except Exception as exc:
            return AdapterResult.degraded(f"OpenAI-compatible model endpoint unavailable: {exc}")
        return AdapterResult.ok("OpenAI-compatible model endpoint is available", model=self.model)

    def run_report(self, request: ReadingReportRequest) -> AdapterResult:
        if not self.base_url or not self.model:
            return AdapterResult.degraded(
                "OpenAI-compatible model is not configured; set Base URL and model",
                paper_id=request.paper_id,
            )
        task = str(request.metadata.get("task") or "analyze")
        if task == "translate_abstract":
            return self._translate_abstract(request)
        if task == "translate":
            return self._translate(request)
        return self._analyze(request)

    def _analyze(self, request: ReadingReportRequest) -> AdapterResult:
        source = (request.markdown or request.abstract or "")[: self.max_input_chars]
        section_names = [str(item.get("title") or "") for item in request.sections if item.get("title")]
        prompt = f"""请阅读以下论文内容，输出一个 JSON 对象，不要输出 Markdown 围栏或额外解释。

JSON 必须包含字段：{', '.join(REPORT_FIELDS)}、score、evidence。
每个正文段字段都必须是中文字符串；论文没有报告的内容写“论文未报告”，不得编造。
evidence 必须是数组，并为每个非空正文段字段至少提供一条证据，格式为：
{{"kind":"fact","source":"paper_version:{request.metadata.get('paper_version_id', request.paper_id)}","report_field":"method","section":"原文章节名","quote":"简短原文摘录","note":"为什么支持该结论"}}。
score 是包含 overall（0-10）的对象。请区分论文事实、你的分析和待验证假设。

标题：{request.title}
摘要：{request.abstract}
可用章节：{', '.join(section_names) or '未识别章节'}

论文结构化内容：
{source}
"""
        response = self._chat(
            system="你是严谨的 AI Infra 论文研究员，只基于提供的论文内容生成可追溯报告。",
            user=prompt,
        )
        if response.status != "ok":
            return response
        content = str(response.data.get("content") or "")
        try:
            report = _json_object(content)
        except ValueError as exc:
            return AdapterResult.failed(
                f"Model returned an invalid structured report: {exc}",
                paper_id=request.paper_id,
            )
        return AdapterResult.ok(
            "OpenAI-compatible paper report generated",
            paper_id=request.paper_id,
            report=report,
            model=self.model,
            usage=response.data.get("usage"),
        )

    def _translate(self, request: ReadingReportRequest) -> AdapterResult:
        source = (request.markdown or "")[: self.max_input_chars]
        if not source:
            return AdapterResult.failed("No Markdown is available for translation")
        response = self._chat(
            system="你是技术论文翻译专家，保留 Markdown、公式、表格、代码和标题结构。",
            user=(
                "将以下论文 Markdown 翻译为简体中文。只输出翻译后的 Markdown，不要解释；"
                "术语首次出现时可保留英文括注。\n\n" + source
            ),
        )
        if response.status != "ok":
            return response
        return AdapterResult.ok(
            "OpenAI-compatible paper translation generated",
            markdown_zh=str(response.data.get("content") or "").strip(),
            model=self.model,
            usage=response.data.get("usage"),
        )

    def _translate_abstract(self, request: ReadingReportRequest) -> AdapterResult:
        source = (request.abstract or "").strip()[: self.max_input_chars]
        if not source:
            return AdapterResult.failed("No English abstract is available for translation")
        response = self._chat(
            system="你是严谨的学术论文翻译与研究助手。",
            user=(
                "根据以下英文论文摘要，输出一个 JSON 对象，不要输出 Markdown 围栏或额外解释。"
                "对象包含两个字段：\n"
                "1. abstract_zh：论文摘要的简体中文译文。保留模型名、方法名、指标名和必要英文术语括注；"
                "只翻译摘要内容，不补充或改写事实。\n"
                "2. method_summary：一句简体中文的方法概述，句式形如"
                "“本文提出/采用/设计 {方法}，以解决 {问题}”。只提炼摘要明确提到的方法与问题，"
                "不要编造；同样一句话，不加标点之外的修饰。\n\n"
                "论文摘要：\n" + source
            ),
        )
        if response.status != "ok":
            return response
        content = str(response.data.get("content") or "").strip()
        abstract_zh = content
        method_summary = ""
        try:
            parsed = _json_object(content)
            if isinstance(parsed, dict):
                abstract_zh = str(parsed.get("abstract_zh") or "").strip()
                method_summary = str(parsed.get("method_summary") or "").strip()
        except ValueError:
            pass
        if not abstract_zh:
            return AdapterResult.failed("Model returned no translated abstract")
        return AdapterResult.ok(
            "OpenAI-compatible abstract translation generated",
            abstract_zh=abstract_zh,
            method_summary=method_summary,
            model=self.model,
            usage=response.data.get("usage"),
        )

    def _chat(self, *, system: str, user: str) -> AdapterResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Content-Type": "application/json", **self._headers()},
                    json=payload,
                )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            return AdapterResult.degraded(f"OpenAI-compatible model unavailable: {exc}")
        if not isinstance(content, str) or not content.strip():
            return AdapterResult.failed("OpenAI-compatible model returned empty content")
        usage = data.get("usage") if isinstance(data, dict) else None
        return AdapterResult.ok(
            "OpenAI-compatible response received",
            content=content,
            response=data,
            usage=usage,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


def _json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("response does not contain a JSON object") from None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("response JSON is not an object")
    return value