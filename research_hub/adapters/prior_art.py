"""Structured patent prior-art search adapter contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

import httpx

from .browser_runtime import browser_subprocess_env
from .types import AdapterResult


class PriorArtSearchAdapter:
    """Call a configured patent-search service and validate auditable records.

    The external service may wrap CNIPA public search plus an approved fallback,
    but it must return bibliographically matched public URLs and the abstract
    actually used for analysis. Missing patent coverage is never reported as a
    successful check.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("PATENT_PRIOR_ART_API_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("PATENT_PRIOR_ART_API_KEY") or ""
        self.timeout_seconds = timeout_seconds

    def search(self, query: dict[str, Any]) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded(
                "Patent prior-art service is not configured; set PATENT_PRIOR_ART_API_URL"
            )
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.post(f"{self.base_url}/search", headers=headers, json=query)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return AdapterResult.degraded(f"Patent prior-art search unavailable: {exc}")
        records = payload.get("records") if isinstance(payload, dict) else None
        try:
            validated = validate_prior_art_records(records)
        except ValueError as exc:
            return AdapterResult.failed(f"Patent prior-art contract violation: {exc}")
        return AdapterResult.ok(
            "Patent prior-art search completed",
            records=validated,
            provider=payload.get("provider"),
        )


def validate_prior_art_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("records must be a non-empty list")
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"record {index} must be an object")
        record = dict(raw)
        required = ("source", "title", "publication_number", "url", "abstract", "analysis_basis")
        missing = [field for field in required if not str(record.get(field) or "").strip()]
        if missing:
            raise ValueError(f"record {index} is missing {', '.join(missing)}")
        parsed = urlparse(str(record["url"]))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"record {index} does not have a public HTTP(S) URL")
        if record.get("bibliographic_match") is not True:
            raise ValueError(f"record {index} bibliography is not verified")
        source = str(record["source"]).lower()
        basis = str(record["analysis_basis"]).lower()
        if source in {"cnipa", "国知局"} and "abstract" not in basis and "摘要" not in basis:
            raise ValueError(f"record {index} CNIPA analysis is not based on the abstract")
        record.setdefault("source_type", "patent")
        record.setdefault("limitations", "")
        validated.append(record)
    return validated


class LocalCnipaPriorArtAdapter:
    """Run the bundled CNIPA publication search skill on the server."""

    def __init__(
        self,
        *,
        script_path: Path | None = None,
        timeout_seconds: float = 300.0,
        max_terms: int = 3,
    ) -> None:
        configured_value = os.getenv("PATENT_DISCLOSURE_ROOT", "").strip()
        configured_root = Path(configured_value).expanduser() if configured_value else None
        workspace_root = Path(__file__).resolve().parents[3] / "patent-disclosure-skill"
        skill_root = (
            configured_root
            if configured_root is not None and configured_root.is_dir()
            else workspace_root
        )
        self.script_path = (script_path or skill_root / "tools" / "cnipa_epub_search.py").resolve()
        self.timeout_seconds = timeout_seconds
        self.max_terms = max(1, min(max_terms, 8))

    def health(self) -> AdapterResult:
        if not self.script_path.is_file():
            return AdapterResult.degraded("Local CNIPA search skill is not installed")
        try:
            import playwright  # noqa: F401
        except ImportError:
            return AdapterResult.degraded("Playwright is required by the local CNIPA skill")
        return AdapterResult.ok("Local CNIPA search skill is available")

    def search(self, query: dict[str, Any]) -> AdapterResult:
        health = self.health()
        if health.status != "ok":
            return health
        terms = [
            str(term).strip()
            for term in query.get("query_terms") or []
            if str(term).strip()
        ][: self.max_terms]
        if not terms:
            terms = str(query.get("title") or "").split()[: self.max_terms]
        if not terms:
            return AdapterResult.failed("Local CNIPA search requires at least one query term")
        env = browser_subprocess_env()
        use_proxy = env.get("PATENT_CNIPA_USE_PROXY", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not use_proxy:
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ):
                env.pop(key, None)
        env.setdefault("EPUB_WAF_MAX_WAIT_SEC", str(max(30, int(self.timeout_seconds) - 30)))
        try:
            completed = subprocess.run(
                [sys.executable, str(self.script_path), *terms],
                cwd=str(self.script_path.parent),
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return AdapterResult.degraded(f"Local CNIPA search unavailable: {exc}")
        if completed.returncode != 0:
            message = completed.stderr.strip()[-1000:] or "unknown local CNIPA error"
            return AdapterResult.degraded(f"Local CNIPA search failed: {message}")
        marker = "EPUB_HITS_JSON:"
        line = next((item for item in completed.stdout.splitlines() if item.startswith(marker)), "")
        try:
            hits = json.loads(line.removeprefix(marker).strip()) if line else []
        except json.JSONDecodeError as exc:
            return AdapterResult.failed(f"Local CNIPA search returned invalid JSON: {exc}")
        records = []
        for item in hits[: int(query.get("max_results") or 20)]:
            if not isinstance(item, dict):
                continue
            publication_number = str(item.get("pub_number") or "").strip()
            title = str(item.get("title") or "").strip()
            url = str(item.get("link") or "").strip()
            abstract = str(item.get("abstract") or "").strip()
            if not publication_number or not title or not url or not abstract:
                continue
            records.append(
                {
                    "source_type": "patent",
                    "source": "cnipa",
                    "title": title,
                    "publication_number": publication_number,
                    "url": url,
                    "abstract": abstract,
                    "analysis_basis": "cnipa_publication_abstract",
                    "bibliographic_match": True,
                    "limitations": "CNIPA public publication search; claims require manual review.",
                }
            )
        try:
            validated = validate_prior_art_records(records)
        except ValueError:
            return AdapterResult.degraded(
                "Local CNIPA search returned no auditable patent records",
                query_terms=terms,
            )
        return AdapterResult.ok(
            "Local CNIPA search completed with auditable records",
            records=validated,
            query_terms=terms,
            provider="cnipa_epub",
        )


class FallbackPriorArtSearchAdapter:
    """Try an approved remote service, then the local CNIPA skill."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback

    def search(self, query: dict[str, Any]) -> AdapterResult:
        primary_result = self.primary.search(query)
        if primary_result.status == "ok":
            return primary_result
        fallback_result = self.fallback.search(query)
        if fallback_result.status == "ok":
            return AdapterResult.ok(
                fallback_result.message,
                **fallback_result.data,
                fallback_from={
                    "status": primary_result.status,
                    "message": primary_result.message,
                },
            )
        return AdapterResult.degraded(
            "Remote and local CNIPA prior-art searches are unavailable",
            primary={"status": primary_result.status, "message": primary_result.message},
            fallback={"status": fallback_result.status, "message": fallback_result.message},
        )
