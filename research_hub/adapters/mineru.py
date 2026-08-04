"""Adapters for the official MinerU async API/router and legacy WebApp."""

from __future__ import annotations

import os
import mimetypes
from pathlib import Path
from pathlib import PurePosixPath
import zipfile
from typing import Any

import httpx

from .types import AdapterResult, MinerUJobRequest


class MinerUApiAdapter:
    """Use MinerU's official ``POST /tasks`` asynchronous contract.

    Both ``mineru-api`` and ``mineru-router`` expose the same task/status/result
    endpoints. Resource selection stays server-side; product callers never
    choose a physical GPU id.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("MINERU_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("MINERU_API_KEY") or ""
        self.timeout_seconds = timeout_seconds

    def health(self) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL")
        try:
            data = self._get("/health")
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU health check unavailable: {exc}")
        return AdapterResult.ok("MinerU API/router health check succeeded", response=data)

    def submit(self, request: MinerUJobRequest) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL")
        pdf_path = Path(request.pdf_path).expanduser().resolve()
        if not pdf_path.is_file():
            return AdapterResult.failed(f"PDF does not exist: {pdf_path}", pdf_path=str(pdf_path))

        form = {
            "lang_list": "ch" if request.language in {"auto", "zh", "ch"} else request.language,
            "backend": request.backend,
            "parse_method": str(request.options.get("parse_method") or "auto"),
            "formula_enable": "true" if "formulas" in request.extract else "false",
            "table_enable": "true" if "tables" in request.extract else "false",
            "return_md": "true" if "markdown" in request.extract else "false",
            "return_middle_json": "true" if "json" in request.extract else "false",
            "return_content_list": "true" if "json" in request.extract else "false",
            "return_images": "true" if "images" in request.extract else "false",
            "response_format_zip": "true",
            "return_original_file": "false",
        }
        try:
            mime_type = mimetypes.guess_type(pdf_path.name)[0] or "application/pdf"
            with pdf_path.open("rb") as handle, httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = client.post(
                    f"{self.base_url}/tasks",
                    headers=self._headers(),
                    data=form,
                    files={"files": (pdf_path.name, handle, mime_type)},
                )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return AdapterResult.degraded(
                f"MinerU task submission unavailable: {exc}",
                artifact_id=request.artifact_id,
            )
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if not isinstance(task_id, str) or not task_id:
            return AdapterResult.failed("MinerU returned an invalid task payload", response=data)
        return AdapterResult.ok("MinerU task submitted", response=data, task_id=task_id)

    def status(self, job_id: str) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL", job_id=job_id)
        try:
            data = self._get(f"/tasks/{job_id}")
        except httpx.HTTPStatusError as exc:
            return AdapterResult.degraded(
                f"MinerU task status unavailable: {exc}",
                job_id=job_id,
                http_status=exc.response.status_code,
            )
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU task status unavailable: {exc}", job_id=job_id)
        return AdapterResult.ok("MinerU task status fetched", response=data)

    def fetch_result(self, job_id: str, output_dir: Path | str) -> AdapterResult:
        """Download the result ZIP, extract safely, and return a file manifest."""

        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL", job_id=job_id)
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        archive = destination / "mineru-result.zip"
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.get(
                    f"{self.base_url}/tasks/{job_id}/result",
                    headers=self._headers(),
                )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "zip" not in content_type and not response.content.startswith(b"PK"):
                return AdapterResult.failed(
                    "MinerU task result is not a ZIP package",
                    content_type=content_type,
                )
            archive.write_bytes(response.content)
            self._safe_extract(archive, destination)
        except httpx.HTTPStatusError as exc:
            return AdapterResult.degraded(
                f"MinerU result download unavailable: {exc}",
                job_id=job_id,
                http_status=exc.response.status_code,
            )
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU result download unavailable: {exc}", job_id=job_id)
        finally:
            archive.unlink(missing_ok=True)

        files = sorted(path for path in destination.rglob("*") if path.is_file())
        markdown = [path for path in files if path.suffix.lower() == ".md"]
        structured_json = [path for path in files if path.suffix.lower() == ".json"]
        images = [
            path
            for path in files
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
        ]
        if not markdown or not structured_json:
            return AdapterResult.failed(
                "MinerU result package is missing required Markdown or structured JSON",
                files=[str(path.relative_to(destination)) for path in files],
            )
        manifest = {
            "task_id": job_id,
            "root": str(destination),
            "markdown": [str(path) for path in markdown],
            "structured_json": [str(path) for path in structured_json],
            "resources": [str(path) for path in images],
            "files": [str(path) for path in files],
        }
        return AdapterResult.ok("MinerU result package downloaded", manifest=manifest)

    def _get(self, path: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(f"{self.base_url}{path}", headers=self._headers())
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    @staticmethod
    def _safe_extract(archive: Path, destination: Path) -> None:
        root = destination.resolve()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"Unsafe MinerU ZIP entry: {member.filename}")
                target = (root / Path(*relative.parts)).resolve()
                if target != root and root not in target.parents:
                    raise ValueError(f"Unsafe MinerU ZIP entry: {member.filename}")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    output.write(source.read())


class MinerUWebAppAdapter:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 60.0) -> None:
        self.base_url = (base_url or os.getenv("MINERU_BASE_URL") or "").rstrip("/")
        self.timeout_seconds = timeout_seconds

    def health(self) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL")
        try:
            data = self._get("/api/health")
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU health check unavailable: {exc}")
        return AdapterResult.ok("MinerU health check succeeded", response=data)

    def submit(self, request: MinerUJobRequest) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL")
        pdf_path = Path(request.pdf_path).expanduser().resolve()
        if not pdf_path.is_file():
            return AdapterResult.failed(f"PDF does not exist: {pdf_path}", pdf_path=str(pdf_path))
        try:
            data = self._post("/api/jobs", {"pdf_path": str(pdf_path), "gpu_id": request.gpu_id})
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU job submission unavailable: {exc}", pdf_path=str(pdf_path))
        return AdapterResult.ok("MinerU job submitted", response=data)

    def status(self, job_id: str) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL", job_id=job_id)
        try:
            data = self._get(f"/api/jobs/{job_id}")
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU job status unavailable: {exc}", job_id=job_id)
        return AdapterResult.ok("MinerU job status fetched", response=data)

    def download_markdown(self, markdown_path: str, output_path: Path | str) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL")
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.get(
                    f"{self.base_url}/api/markdown/download",
                    params={"path": markdown_path},
                )
            response.raise_for_status()
            output.write_bytes(response.content)
        except Exception as exc:
            return AdapterResult.degraded(
                f"MinerU markdown download unavailable: {exc}",
                markdown_path=markdown_path,
            )
        return AdapterResult.ok(
            "MinerU markdown downloaded",
            path=str(output),
            markdown_path=markdown_path,
        )

    def _get(self, path: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}{path}")
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
        response.raise_for_status()
        return response.json()
