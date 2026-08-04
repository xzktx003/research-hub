"""HTTP adapter for the existing Dify paper_digest workflow."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .types import AdapterResult, ReadingReportRequest


class DifyPaperDigestAdapter:
    """Call a Dify workflow through its public workflow-run API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        workflow_id: str | None = None,
        workflow_path: str | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("DIFY_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("DIFY_API_KEY") or ""
        self.workflow_id = workflow_id or os.getenv("DIFY_WORKFLOW_ID") or ""
        self.workflow_path = workflow_path or (
            f"/v1/workflows/{self.workflow_id}/run" if self.workflow_id else "/v1/workflows/run"
        )
        self.timeout_seconds = timeout_seconds

    def run_report(self, request: ReadingReportRequest) -> AdapterResult:
        if not self.base_url or not self.api_key:
            return AdapterResult.degraded(
                "Dify is not configured; set DIFY_BASE_URL and DIFY_API_KEY",
                paper_id=request.paper_id,
            )
        artifact_refs = [dict(item) for item in request.artifact_refs]
        section_refs = [dict(item) for item in request.sections]
        inputs: dict[str, Any] = {
            "paper_id": request.paper_id,
            "title": request.title,
            "abstract": request.abstract,
            "pdf_url": request.pdf_url or "",
            "artifact_refs": artifact_refs,
            "section_refs": section_refs,
            "paper_package": {
                "artifact_refs": artifact_refs,
                "section_refs": section_refs,
            },
            "metadata": dict(request.metadata),
            **request.metadata,
        }
        # Artifact/section references are the production contract. Inline
        # Markdown remains an explicit compatibility switch for legacy Dify
        # workflows and is bounded to avoid unbounded workflow variables.
        if os.getenv("DIFY_INLINE_MARKDOWN", "").lower() in {"1", "true", "yes", "on"}:
            max_chars = int(os.getenv("DIFY_INLINE_MARKDOWN_MAX_CHARS", "120000"))
            inputs["markdown"] = (request.markdown or "")[:max_chars]

        payload = {
            "inputs": {
                **inputs,
            },
            "response_mode": "blocking",
            "user": "research-hub",
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}{self.workflow_path}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except Exception as exc:
            return AdapterResult.degraded(
                f"Dify workflow unavailable: {exc}",
                paper_id=request.paper_id,
            )
        return AdapterResult.ok("Dify paper report generated", paper_id=request.paper_id, response=data)
