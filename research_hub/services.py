"""Executable service layer for discovery and paper-processing jobs."""

from __future__ import annotations

import os
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .adapters.downloader import PdfDownloadAdapter
from .adapters.render_pdf import MarkdownPdfRenderAdapter
from .adapters import (
    AdapterResult,
    ArxivDiscoveryAdapter,
    CompositeDiscoveryAdapter,
    DifyPaperDigestAdapter,
    HuggingFaceDailyPapersAdapter,
    OpenAlexMetadataAdapter,
    OpenReviewDiscoveryAdapter,
    MinerUJobRequest,
    OpenAICompatibleResearchAdapter,
    ReadingReportRequest,
    TopicQuery,
)
from .adapters.mineru import MinerUApiAdapter
from .adapters.prior_art import (
    FallbackPriorArtSearchAdapter,
    LocalCnipaPriorArtAdapter,
    PriorArtSearchAdapter,
)
from .adapters.storage import file_sha256
from .adapters.openai_compatible import _json_object
from .database import dumps, loads
from .models import (
    JobRetryRequest,
    PatentStageRunCreate,
    PatentStageRunUpdate,
    PaperCreate,
    PaperIdentifier,
    PaperSourceHitCreate,
    PaperVersionCreate,
    PipelineRunCreate,
)
from .patent_service import PatentOutputService
from .repository import Repository, new_id, stable_hash, utcnow
from .runtime_config import load_runtime_config


class JobKindMismatchError(RuntimeError):
    """Raised when a job's kind does not match the executor handling it.

    This signals a data-integrity/dispatch mismatch rather than an ordinary
    adapter failure, so callers can distinguish it from a transient 500.
    """

    def __init__(self, job_id: str, actual: str, expected: str) -> None:
        super().__init__(f"Job {job_id} is {actual}, expected {expected}")
        self.job_id = job_id
        self.actual = actual
        self.expected = expected


class ResearchJobService:
    """Run queued workflow jobs while keeping adapter failures explicit."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        discovery_adapter: Any | None = None,
        parser_adapter: Any | None = None,
        analyzer_adapter: Any | None = None,
        translator_adapter: Any | None = None,
        downloader_adapter: Any | None = None,
        renderer_adapter: Any | None = None,
        prior_art_adapter: Any | None = None,
    ) -> None:
        self.conn = conn
        self.repo = Repository(conn)
        self.runtime_config = load_runtime_config()
        self.discovery_adapter = discovery_adapter or _default_discovery_adapter()
        mineru_config = self.runtime_config["services"]["mineru"]
        self.parser_adapter = parser_adapter or MinerUApiAdapter(
            base_url=mineru_config["base_url"],
            api_key=mineru_config["api_key"],
        )
        analysis_adapter = _default_analysis_adapter(self.runtime_config)
        self.analyzer_adapter = analyzer_adapter or analysis_adapter
        self.translator_adapter = translator_adapter or analysis_adapter
        self.downloader_adapter = downloader_adapter or PdfDownloadAdapter()
        self.renderer_adapter = renderer_adapter or MarkdownPdfRenderAdapter()
        prior_art_config = self.runtime_config["services"]["prior_art"]
        if prior_art_adapter is not None:
            self.prior_art_adapter = prior_art_adapter
        else:
            local_prior_art = LocalCnipaPriorArtAdapter()
            if prior_art_config["mode"] == "remote" and prior_art_config["base_url"]:
                self.prior_art_adapter = FallbackPriorArtSearchAdapter(
                    PriorArtSearchAdapter(
                        base_url=prior_art_config["base_url"],
                        api_key=prior_art_config["api_key"],
                    ),
                    local_prior_art,
                )
            else:
                self.prior_art_adapter = local_prior_art

    def run_discovery_run(self, run_id: str) -> dict[str, Any]:
        run = self.repo.get_discovery_run(run_id)
        self._mark_discovery_run(run.id, "running")
        if run.job_id:
            self._mark_job(run.job_id, "running")
        auto_process = bool(
            run.metadata.get(
                "auto_process",
                self.runtime_config["schedule"]["auto_process"],
            )
        )
        pipeline_run = self._pipeline_for_discovery(run) if auto_process else None

        topics = self._topic_queries(run.topics, run.max_results)
        if not topics:
            result = {"run_id": run.id, "papers_seen": 0, "papers_created": 0, "errors": []}
            error = {"message": "No enabled topics matched the discovery request", "topics": run.topics}
            self._mark_discovery_run(run.id, "terminal_failed", result=result, error=error)
            if run.job_id:
                self._mark_job(run.job_id, "terminal_failed", result=result, error=error)
            return result | {"status": "terminal_failed"}

        seen = 0
        created = 0
        matched = 0
        filtered_out = 0
        enrichment_skipped = 0
        errors: list[dict[str, Any]] = []
        source_outcomes: list[dict[str, Any]] = []
        persisted: list[dict[str, str]] = []
        enqueued: list[dict[str, str]] = []

        for topic in topics:
            adapter_result = self.discovery_adapter.discover(topic)
            discovered_papers = adapter_result.data.get("papers", [])
            for key in ("sources", "failures"):
                for outcome in adapter_result.data.get(key) or []:
                    if isinstance(outcome, dict):
                        source_outcomes.append({"topic_id": topic.topic_id, **outcome})
            if adapter_result.status != "ok":
                errors.append(
                    {
                        "topic_id": topic.topic_id,
                        "status": adapter_result.status,
                        "message": adapter_result.message,
                    }
                )
                for failure in adapter_result.data.get("failures") or []:
                    if isinstance(failure, dict):
                        errors.append({"topic_id": topic.topic_id, **failure})
            if not discovered_papers:
                continue
            papers = [hit for hit in discovered_papers if _hit_in_window(hit, run.window_start, run.window_end)]
            papers.sort(key=lambda hit: str(hit.get("source_role") or "authoritative") == "enrichment")
            filtered_out += len(discovered_papers) - len(papers)
            for rank, hit in enumerate(papers, start=1):
                seen += 1
                before = self._find_existing_paper_id(hit)
                if str(hit.get("source_role") or "authoritative") == "enrichment" and not before:
                    enrichment_skipped += 1
                    continue
                detail = self._persist_paper_hit(hit, topic, rank, run)
                if before:
                    matched += 1
                else:
                    created += 1
                persisted.append(
                    {
                        "paper_id": detail.id,
                        "paper_version_id": detail.current_version_id or "",
                        "topic_id": topic.topic_id,
                    }
                )
                if auto_process and detail.current_version_id:
                    abstract_job = self._enqueue_abstract_translation(
                        paper_id=detail.id,
                        version_id=detail.current_version_id,
                        discovery_run_id=run.id,
                        pipeline_run_id=pipeline_run.id if pipeline_run else None,
                    )
                    if abstract_job:
                        enqueued.append(abstract_job)
                    queued = self._enqueue_discovered_version(
                        paper_id=detail.id,
                        version_id=detail.current_version_id,
                        discovery_run_id=run.id,
                        pipeline_run_id=pipeline_run.id if pipeline_run else None,
                    )
                    if queued:
                        enqueued.append(queued)

        result = {
            "run_id": run.id,
            "topics": [topic.topic_id for topic in topics],
            "papers_seen": seen,
            "papers_created": created,
            "papers_matched": matched,
            "papers_filtered_out": filtered_out,
            "enrichment_skipped": enrichment_skipped,
            "papers_persisted": persisted,
            "auto_process": auto_process,
            "jobs_enqueued": enqueued,
            "pipeline_run_id": pipeline_run.id if pipeline_run else None,
            "source_outcomes": source_outcomes,
            "errors": errors,
        }
        if not errors:
            status = "succeeded"
        elif seen or matched or created:
            status = "partial_succeeded"
        else:
            status = "retryable_failed"
        self._mark_discovery_run(run.id, status, result=result, error={"errors": errors} if errors else {})
        if run.job_id:
            self._mark_job(run.job_id, status, result=result, error={"errors": errors} if errors else {})
        if pipeline_run:
            self.repo.update_pipeline_run_counts(
                pipeline_run.id,
                status=status,
                input_counts={"topics": len(topics), "papers_seen": seen},
                output_counts={"papers_persisted": len(persisted), "download_jobs": len(enqueued)},
                error_counts={"discovery_errors": len(errors)},
            )
        return result | {"status": status}

    def run_download_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "download", job_id)
        self._mark_job(job.id, "running")
        version = self.repo.get_paper_version(job.target_id)
        if not version.pdf_url:
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed("Paper version has no PDF URL", paper_version_id=version.id),
            )
        artifact_root = _artifact_root()
        result = self.downloader_adapter.download(version.pdf_url, artifact_root)
        payload = self._finish_adapter_job(job.id, result)
        if result.status != "ok":
            return payload

        artifact = self.repo.create_artifact_for_version(
            version.id,
            _artifact_create(
                "pdf",
                str(result.data["path"]),
                result.data.get("content_type") or "application/pdf",
                {
                    "source_url": version.pdf_url,
                    "download_job_id": job.id,
                    "size_bytes": result.data.get("size_bytes"),
                },
                checksum=str(result.data.get("sha256") or ""),
            ),
        )
        self.conn.execute(
            "UPDATE paper SET status = 'downloaded', updated_at = ? WHERE id = ?",
            (utcnow(), version.paper_id),
        )
        parse_options = (job.request.get("options") or {}).get("parse_options") or job.request.get("parse_options") or {}
        parse_job = self.repo.create_job(
            "parse",
            "paper_version",
            version.id,
            {
                "source": "download_chain",
                "download_job_id": job.id,
                "options": parse_options,
                "after_parse": job.request.get("after_parse") or [],
                "pipeline_run_id": job.request.get("pipeline_run_id"),
            },
            idempotency_key=f"download-chain:{version.id}:parse",
        )
        final_result = {
            **payload["result"],
            "artifact_id": artifact.id,
            "parse_job_id": parse_job.job_id,
        }
        self._mark_job(job.id, "succeeded", result=final_result)
        return {"job_id": job.id, "status": "succeeded", "result": final_result, "error": {}}

    def run_parse_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "parse", job_id)
        self._mark_job(job.id, "running")
        version = self.repo.get_paper_version(job.target_id)
        pdf_path = self._resolve_pdf_path(version.id, job.request)
        if pdf_path is None:
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed(
                    "No local PDF path is available for MinerU parsing",
                    paper_version_id=version.id,
                ),
            )

        result = self._submit_mineru_parse(job, version.id, pdf_path)
        external_task_id = self._external_task_id(result)
        if result.status == "ok" and external_task_id:
            payload = {
                "job_id": job.id,
                "status": "running",
                "result": {
                    "adapter_status": result.status,
                    "message": result.message,
                    **result.data,
                },
                "error": {},
            }
            self._mark_job(
                job.id,
                "running",
                result=payload["result"],
                external_task_id=external_task_id,
                next_poll_after=_poll_after(),
            )
        else:
            payload = self._finish_adapter_job(job.id, result, external_task_id=external_task_id)
            if payload["status"] == "succeeded":
                payload["result"]["chained_jobs"] = self._enqueue_after_parse(version.id, job)
                self._mark_job(job.id, "succeeded", result=payload["result"])
        if result.status == "ok":
            paper_status = "parsed" if payload["status"] == "succeeded" else "parse_submitted"
            self.conn.execute(
                "UPDATE paper SET status = ?, updated_at = ? WHERE id = ?",
                (paper_status, utcnow(), version.paper_id),
            )
        return payload

    def poll_parse_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "parse", job_id)
        if not job.external_task_id:
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed("Parse job has no MinerU external task id", job_id=job.id),
            )
        status_result = self.parser_adapter.status(job.external_task_id)
        if status_result.status != "ok":
            if self._is_mineru_missing_task(status_result):
                return self._recover_missing_mineru_task(job, status_result)
            return self._finish_adapter_job(job.id, status_result, external_task_id=job.external_task_id)
        response = status_result.data.get("response")
        external = response if isinstance(response, dict) else {}
        state = str(external.get("status") or "").lower()
        if state in {"queued", "running", "pending", "processing"}:
            result = {
                "adapter_status": "ok",
                "message": "MinerU parse is still running",
                "response": external,
            }
            self._mark_job(
                job.id,
                "running",
                result=result,
                external_task_id=job.external_task_id,
                next_poll_after=_poll_after(),
            )
            return {"job_id": job.id, "status": "running", "result": result, "error": {}}
        if state != "completed":
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed(
                    "MinerU parse failed",
                    response=external,
                    error=external.get("error"),
                ),
                external_task_id=job.external_task_id,
            )
        if hasattr(self.parser_adapter, "fetch_result"):
            output_root = _artifact_root() / "mineru" / job.target_id / job.external_task_id
            package = self.parser_adapter.fetch_result(job.external_task_id, output_root)
            if package.status != "ok":
                if self._is_mineru_missing_task(package):
                    return self._recover_missing_mineru_task(job, package)
                return self._finish_adapter_job(
                    job.id,
                    package,
                    external_task_id=job.external_task_id,
                )
            manifest = package.data.get("manifest")
            if not isinstance(manifest, dict):
                return self._finish_adapter_job(
                    job.id,
                    AdapterResult.failed("MinerU result did not include a manifest"),
                    external_task_id=job.external_task_id,
                )
            artifacts = self._register_mineru_manifest(job.target_id, job.external_task_id, manifest)
            result = {
                "adapter_status": "ok",
                "message": "MinerU parse completed",
                "response": external,
                "manifest": manifest,
                "artifact_ids": [artifact.id for artifact in artifacts],
                "chained_jobs": self._enqueue_after_parse(job.target_id, job),
            }
            self._mark_job(
                job.id,
                "succeeded",
                result=result,
                external_task_id=job.external_task_id,
            )
            version = self.repo.get_paper_version(job.target_id)
            self.conn.execute(
                "UPDATE paper SET status = 'parsed', updated_at = ? WHERE id = ?",
                (utcnow(), version.paper_id),
            )
            return {"job_id": job.id, "status": "succeeded", "result": result, "error": {}}

        markdown_path = str(external.get("markdown_path") or "")
        if not markdown_path:
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed("MinerU completed without a markdown artifact", response=external),
                external_task_id=job.external_task_id,
            )
        artifact_root = Path(
            os.getenv(
                "RESEARCH_HUB_ARTIFACT_ROOT",
                str(Path(__file__).resolve().parents[1] / "artifacts"),
            )
        ).expanduser().resolve()
        output = artifact_root / "mineru" / job.target_id / Path(markdown_path).name
        download = self.parser_adapter.download_markdown(markdown_path, output)
        if download.status != "ok":
            return self._finish_adapter_job(job.id, download, external_task_id=job.external_task_id)
        artifact = self.repo.create_artifact_for_version(
            job.target_id,
            _artifact_create(
                "markdown_original",
                str(output),
                "text/markdown; charset=utf-8",
                {
                    "mineru_job_id": job.external_task_id,
                    "remote_markdown_path": markdown_path,
                    "checksum": file_sha256(output),
                },
                checksum=file_sha256(output),
            ),
        )
        result = {
            "adapter_status": "ok",
            "message": "MinerU parse completed",
            "response": external,
            "artifact_id": artifact.id,
            "chained_jobs": self._enqueue_after_parse(job.target_id, job),
        }
        self._mark_job(
            job.id,
            "succeeded",
            result=result,
            external_task_id=job.external_task_id,
        )
        version = self.repo.get_paper_version(job.target_id)
        self.conn.execute(
            "UPDATE paper SET status = 'parsed', updated_at = ? WHERE id = ?",
            (utcnow(), version.paper_id),
        )
        return {"job_id": job.id, "status": "succeeded", "result": result, "error": {}}

    def _submit_mineru_parse(self, job: Any, version_id: str, pdf_path: Path) -> AdapterResult:
        options = job.request.get("options", {})
        pdf_artifact = next(
            (
                item
                for item in self.repo.list_version_artifacts(version_id)
                if item.artifact_type in {"pdf", "source_pdf"}
            ),
            None,
        )
        return self.parser_adapter.submit(
            MinerUJobRequest(
                pdf_path=pdf_path,
                artifact_id=pdf_artifact.id if pdf_artifact else None,
                artifact_uri=pdf_artifact.uri if pdf_artifact else str(pdf_path),
                backend=str(options.get("backend") or "pipeline"),
                language=str(options.get("language") or "auto"),
                extract=tuple(
                    options.get("extract")
                    or ("markdown", "json", "images", "tables", "formulas")
                ),
                options={
                    key: value
                    for key, value in options.items()
                    if key not in {"gpu_id", "backend", "language", "extract", "after_parse"}
                },
            )
        )

    def _recover_missing_mineru_task(self, job: Any, missing_result: AdapterResult) -> dict[str, Any]:
        previous_task_id = str(job.external_task_id or "")
        recovery = self._mineru_recovery_metadata(job.result)
        if recovery["resubmissions"] >= _mineru_recovery_limit():
            message = "MinerU task disappeared and recovery limit was reached"
            error = {
                "missing_task_id": previous_task_id,
                "last_error": {"message": missing_result.message, **missing_result.data},
                "recovery": {
                    **recovery,
                    "exhausted": True,
                    "last_missing_task_id": previous_task_id,
                },
            }
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed(message, **error),
                external_task_id=previous_task_id,
            )

        version = self.repo.get_paper_version(job.target_id)
        pdf_path = self._resolve_pdf_path(version.id, job.request)
        if pdf_path is None:
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed(
                    "No local PDF path is available for MinerU recovery",
                    paper_version_id=version.id,
                    missing_task_id=previous_task_id,
                    recovery=recovery,
                ),
                external_task_id=previous_task_id,
            )

        submit_result = self._submit_mineru_parse(job, version.id, pdf_path)
        new_task_id = self._external_task_id(submit_result)
        if submit_result.status != "ok" or not new_task_id:
            return self._finish_adapter_job(job.id, submit_result, external_task_id=previous_task_id)

        external_task_ids = list(recovery["external_task_ids"])
        for task_id in (previous_task_id, new_task_id):
            if task_id and task_id not in external_task_ids:
                external_task_ids.append(task_id)
        recovered = {
            **recovery,
            "resubmissions": recovery["resubmissions"] + 1,
            "external_task_ids": external_task_ids,
            "events": [
                *recovery["events"],
                {
                    "reason": "mineru_task_disappeared",
                    "missing_task_id": previous_task_id,
                    "replacement_task_id": new_task_id,
                    "http_status": missing_result.data.get("http_status"),
                    "recovered_at": utcnow(),
                },
            ],
        }
        result = {
            "adapter_status": submit_result.status,
            "message": "MinerU task disappeared; resubmitted parse",
            **submit_result.data,
            "recovery": recovered,
        }
        self._mark_job(
            job.id,
            "running",
            result=result,
            external_task_id=new_task_id,
            next_poll_after=_poll_after(),
        )
        return {"job_id": job.id, "status": "running", "result": result, "error": {}}

    @staticmethod
    def _is_mineru_missing_task(result: AdapterResult) -> bool:
        if result.data.get("http_status") == 404:
            return True
        response = result.data.get("response")
        return isinstance(response, dict) and response.get("status_code") == 404

    @staticmethod
    def _mineru_recovery_metadata(result: dict[str, Any]) -> dict[str, Any]:
        existing = result.get("recovery")
        if not isinstance(existing, dict):
            existing = {}
        external_task_ids = existing.get("external_task_ids")
        events = existing.get("events")
        return {
            "resubmissions": int(existing.get("resubmissions") or 0),
            "external_task_ids": [str(item) for item in external_task_ids]
            if isinstance(external_task_ids, list)
            else [],
            "events": [item for item in events if isinstance(item, dict)]
            if isinstance(events, list)
            else [],
        }

    def _register_mineru_manifest(
        self,
        version_id: str,
        external_task_id: str,
        manifest: dict[str, Any],
    ) -> list[Any]:
        registered: list[Any] = []
        source_artifact_id = next(
            (
                artifact.id
                for artifact in self.repo.list_version_artifacts(version_id)
                if artifact.artifact_type in {"pdf", "source_pdf"}
            ),
            None,
        )
        common = {
            "mineru_job_id": external_task_id,
            "backend": manifest.get("backend"),
            "quality_warnings": manifest.get("quality_warnings") or [],
            "source_artifact_id": source_artifact_id,
        }
        for path_value in manifest.get("markdown") or []:
            path = Path(str(path_value)).expanduser().resolve()
            registered.append(
                self.repo.create_artifact_for_version(
                    version_id,
                    _artifact_create(
                        "markdown_original",
                        str(path),
                        "text/markdown; charset=utf-8",
                        common,
                        checksum=file_sha256(path),
                    ),
                )
            )
        for path_value in manifest.get("structured_json") or []:
            path = Path(str(path_value)).expanduser().resolve()
            registered.append(
                self.repo.create_artifact_for_version(
                    version_id,
                    _artifact_create(
                        "mineru_structured_json",
                        str(path),
                        "application/json",
                        common,
                        checksum=file_sha256(path),
                    ),
                )
            )
        resource_paths = [Path(str(value)).expanduser().resolve() for value in manifest.get("resources") or []]
        if resource_paths:
            resource_root = Path(str(manifest.get("root") or resource_paths[0].parent)).resolve()
            resource_manifest = {
                **common,
                "files": [str(path.relative_to(resource_root)) for path in resource_paths],
                "count": len(resource_paths),
            }
            registered.append(
                self.repo.create_artifact_for_version(
                    version_id,
                    _artifact_create(
                        "mineru_resources",
                        str(resource_root),
                        "application/vnd.research-hub.resource-directory+json",
                        resource_manifest,
                        checksum=stable_hash(resource_manifest),
                    ),
                )
            )
        return registered

    def run_analyze_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "analyze", job_id)
        self._mark_job(job.id, "running")
        version = self.repo.get_paper_version(job.target_id)
        paper = self.repo.get_paper(version.paper_id)
        markdown = self._read_markdown_artifact(version.id)
        artifact_refs, sections = self._paper_package(version.id, markdown)
        result = self.analyzer_adapter.run_report(
            ReadingReportRequest(
                paper_id=paper.id,
                title=paper.canonical_title,
                abstract=paper.abstract,
                markdown=markdown,
                pdf_url=version.pdf_url,
                artifact_refs=artifact_refs,
                sections=sections,
                metadata={"paper_version_id": version.id, "task": "analyze", **job.request},
            )
        )
        payload = self._finish_adapter_job(job.id, result)
        if result.status == "ok":
            quality = self._upsert_report(
                version.id,
                paper.id,
                paper.canonical_title,
                result,
                f"{self.runtime_config['analysis']['provider']}_analyze",
            )
            if quality["quality_status"] != "complete":
                error = {
                    "message": "Paper report did not satisfy the structured evidence contract",
                    **quality,
                }
                self._mark_job(job.id, "retryable_failed", result=payload["result"], error=error)
                self.conn.execute(
                    "UPDATE paper SET status = 'analysis_incomplete', updated_at = ? WHERE id = ?",
                    (utcnow(), paper.id),
                )
                return {
                    "job_id": job.id,
                    "status": "retryable_failed",
                    "result": payload["result"],
                    "error": error,
                }
            self.repo.rebuild_relations(paper.id)
            self.conn.execute(
                "UPDATE paper SET status = 'analyzed', updated_at = ? WHERE id = ?",
                (utcnow(), paper.id),
            )
        return payload

    def run_translate_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "translate", job_id)
        self._mark_job(job.id, "running")
        version = self.repo.get_paper_version(job.target_id)
        paper = self.repo.get_paper(version.paper_id)
        if str(job.request.get("mode") or "") == "abstract":
            result = self.translator_adapter.run_report(
                ReadingReportRequest(
                    paper_id=paper.id,
                    title=paper.canonical_title,
                    abstract=paper.abstract,
                    markdown=None,
                    pdf_url=version.pdf_url,
                    artifact_refs=(),
                    sections=(),
                    metadata={
                        "paper_version_id": version.id,
                        "task": "translate_abstract",
                        **job.request,
                    },
                )
            )
            payload = self._finish_adapter_job(job.id, result)
            translated_abstract = self._abstract_translation_output(result)
            method_summary = self._method_summary_output(result)
            if result.status == "ok" and translated_abstract:
                # Only overwrite method_summary when the provider returned a
                # non-empty one; a retry or provider switch must not silently
                # erase an existing one-line summary with an empty value.
                self.conn.execute(
                    "UPDATE paper SET translated_abstract = ?, method_summary = CASE WHEN ? = '' THEN method_summary ELSE ? END, updated_at = ? WHERE id = ?",
                    (translated_abstract, method_summary, method_summary, utcnow(), paper.id),
                )
                payload["result"]["translated_abstract"] = translated_abstract
                if method_summary:
                    payload["result"]["method_summary"] = method_summary
                self._mark_job(job.id, "succeeded", result=payload["result"])
            elif result.status == "ok":
                error = {"message": "LLM response did not contain a translated abstract"}
                self._mark_job(job.id, "retryable_failed", result=payload["result"], error=error)
                payload["status"] = "retryable_failed"
                payload["error"] = error
            return payload
        markdown = self._read_markdown_artifact(version.id)
        artifact_refs, sections = self._paper_package(version.id, markdown)
        result = self.translator_adapter.run_report(
            ReadingReportRequest(
                paper_id=paper.id,
                title=paper.canonical_title,
                abstract=paper.abstract,
                markdown=markdown,
                pdf_url=version.pdf_url,
                artifact_refs=artifact_refs,
                sections=sections,
                metadata={"paper_version_id": version.id, "task": "translate", **job.request},
            )
        )
        payload = self._finish_adapter_job(job.id, result)
        if result.status == "ok":
            translated_zh, translated_bilingual = self._translation_outputs(result, markdown)
            if translated_zh:
                source_artifact_id = next(
                    (
                        artifact.id
                        for artifact in self.repo.list_version_artifacts(version.id)
                        if artifact.artifact_type in {"markdown_original", "mineru_markdown"}
                    ),
                    None,
                )
                zh_artifact = self.repo.create_artifact_for_version(
                    version.id,
                    data=_artifact_create(
                        "markdown_zh",
                        f"inline://translation/{job.id}/zh",
                        "text/markdown; charset=utf-8",
                        {
                            "content": translated_zh,
                            "source": f"{self.runtime_config['analysis']['provider']}_translate",
                            "source_paper_version_id": version.id,
                            "alignment": "section",
                            "source_artifact_id": source_artifact_id,
                        },
                        checksum=stable_hash(translated_zh),
                    ),
                )
                bilingual_artifact = self.repo.create_artifact_for_version(
                    version.id,
                    data=_artifact_create(
                        "markdown_bilingual",
                        f"inline://translation/{job.id}/bilingual",
                        "text/markdown; charset=utf-8",
                        {
                            "content": translated_bilingual,
                            "source": f"{self.runtime_config['analysis']['provider']}_translate",
                            "source_paper_version_id": version.id,
                            "alignment": "section",
                            "source_artifact_id": source_artifact_id,
                        },
                        checksum=stable_hash(translated_bilingual),
                    ),
                )
                payload["result"].update(
                    {
                        "markdown_zh_artifact_id": zh_artifact.id,
                        "markdown_bilingual_artifact_id": bilingual_artifact.id,
                    }
                )
                if _after_translate_render_pdf(job.request):
                    render_job = self.repo.create_job(
                        "render_pdf",
                        "paper_version",
                        version.id,
                        {
                            "source": "translate_chain",
                            "translate_job_id": job.id,
                            "translation_artifact_id": zh_artifact.id,
                            "output_artifact_type": "pdf_zh",
                        },
                        idempotency_key=f"translate-chain:{version.id}:render-pdf",
                    )
                    payload["result"]["render_pdf_job_id"] = render_job.job_id
                    self._mark_job(job.id, "succeeded", result=payload["result"])
            self.conn.execute(
                "UPDATE paper SET status = 'translated', updated_at = ? WHERE id = ?",
                (utcnow(), paper.id),
            )
        return payload

    def run_render_pdf_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "render_pdf", job_id)
        self._mark_job(job.id, "running")
        version = self.repo.get_paper_version(job.target_id)
        artifact_id = str(job.request.get("translation_artifact_id") or "")
        markdown = self._artifact_text(artifact_id) if artifact_id else None
        markdown = markdown or self._read_markdown_artifact(
            version.id,
            preferred_types=(
                "markdown_zh",
                "markdown_bilingual",
                "translation_markdown",
                "markdown_original",
            ),
        )
        if not markdown:
            return self._finish_adapter_job(
                job.id,
                AdapterResult.failed("No Markdown artifact is available for PDF rendering"),
            )
        output = _artifact_root() / "rendered_pdf" / version.id / f"{job.id}.pdf"
        result = self.renderer_adapter.render(markdown, output)
        payload = self._finish_adapter_job(job.id, result)
        if result.status != "ok":
            return payload
        artifact = self.repo.create_artifact_for_version(
            version.id,
            _artifact_create(
                str(job.request.get("output_artifact_type") or "pdf_zh"),
                str(result.data["path"]),
                "application/pdf",
                {
                    "render_job_id": job.id,
                    "source": "translated_markdown",
                    "source_artifact_id": artifact_id or None,
                    "size_bytes": result.data.get("size_bytes"),
                },
                checksum=file_sha256(Path(result.data["path"])),
            ),
        )
        final_result = {**payload["result"], "artifact_id": artifact.id}
        self._mark_job(job.id, "succeeded", result=final_result)
        return {"job_id": job.id, "status": "succeeded", "result": final_result, "error": {}}

    def run_prior_art_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "prior_art_check", job_id)
        self._mark_job(job.id, "running")
        candidate = self.repo.get_invention_candidate(job.target_id)
        self.repo.ensure_candidate_foundation_stages(candidate.id)
        stage_by_name = {item.stage: item for item in self.repo.list_patent_stage_runs(candidate.id)}
        prior_art_stage = stage_by_name.get("prior_art")
        if prior_art_stage is None:
            prior_art_stage = self.repo.record_patent_stage_run(
                candidate.id,
                PatentStageRunCreate(
                    stage="prior_art",
                    status="running",
                    job_id=job.id,
                    idempotency_key=f"job:{job.id}:prior-art",
                    input=job.request,
                ),
            )
        elif prior_art_stage.status == "failed":
            prior_art_stage = self.repo.update_patent_stage_run(
                prior_art_stage.id,
                PatentStageRunUpdate(status="running", job_id=job.id, output={}),
            )
        elif prior_art_stage.status in {"skipped", "cancelled"}:
            prior_art_stage = self.repo.update_patent_stage_run(
                prior_art_stage.id,
                PatentStageRunUpdate(status="pending", job_id=job.id, output={}),
            )
            prior_art_stage = self.repo.update_patent_stage_run(
                prior_art_stage.id,
                PatentStageRunUpdate(status="running", job_id=job.id, output={}),
            )
        source_titles: list[str] = []
        for source in candidate.sources:
            paper_id = source.paper_id
            if source.paper_version_id:
                paper_id = self.repo.get_paper_version(source.paper_version_id).paper_id
            if paper_id:
                source_titles.append(self.repo.get_paper(paper_id).canonical_title)
        query_text = " ".join(
            [candidate.title, candidate.integration_mechanism, *source_titles]
        )
        terms = tuple(
            dict.fromkeys(
                token.lower()
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", query_text)
                if token.lower() not in {"with", "from", "based", "using", "paper"}
            )
        )[:8] or ("AI infrastructure",)
        academic_result = self.discovery_adapter.discover(
            TopicQuery(
                topic_id="prior-art-academic",
                display_name="Academic prior-art baseline",
                include_terms=terms,
                max_results=20,
            )
        )
        papers = academic_result.data.get("papers", [])
        patent_result = self.prior_art_adapter.search(
            {
                "candidate_id": candidate.id,
                "title": candidate.title,
                "query_terms": list(terms),
                "integration_mechanism": candidate.integration_mechanism,
                "coupling_interface": candidate.coupling_interface,
                "expected_joint_effect": candidate.expected_joint_effect,
                "max_results": 20,
            }
        )
        academic_records = [
            {
                "source_type": "academic",
                "source": str(item.get("source") or "academic"),
                "title": str(item.get("title") or ""),
                "publication_number": str(item.get("doi") or item.get("source_id") or ""),
                "url": str(item.get("landing_url") or item.get("pdf_url") or ""),
                "abstract": str(item.get("abstract") or ""),
                "analysis_basis": "abstract",
                "bibliographic_match": bool(
                    item.get("title")
                    and (item.get("source_id") or item.get("doi"))
                    and (item.get("landing_url") or item.get("pdf_url"))
                ),
                "limitations": "Academic prior art does not replace patent-database coverage.",
            }
            for item in papers
            if isinstance(item, dict)
        ]
        patent_records = patent_result.data.get("records", []) if patent_result.status == "ok" else []
        payload = {
            "coverage": "complete" if academic_result.status == "ok" and patent_result.status == "ok" else "incomplete",
            "academic_status": academic_result.status,
            "academic_results": papers,
            "prior_art_records": [*academic_records, *patent_records],
            "query_terms": list(terms),
            "patent_database_status": patent_result.status,
            "patent_database_message": patent_result.message,
            "legal_notice": "This automated search is not a novelty, inventiveness, FTO, or grantability opinion.",
        }
        if patent_result.status == "ok":
            for record in payload["prior_art_records"]:
                publication_number = str(record.get("publication_number") or "").strip()
                if not publication_number:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO prior_art_record (
                        id, invention_candidate_id, job_id, source_type, source,
                        title, publication_number, url, abstract, analysis_basis,
                        bibliographic_match, limitations, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(invention_candidate_id, source, publication_number)
                    DO UPDATE SET
                        url = excluded.url,
                        abstract = excluded.abstract,
                        analysis_basis = excluded.analysis_basis,
                        bibliographic_match = excluded.bibliographic_match,
                        limitations = excluded.limitations,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        new_id("prior"),
                        candidate.id,
                        job.id,
                        str(record.get("source_type") or "academic"),
                        str(record.get("source") or "unknown"),
                        str(record.get("title") or ""),
                        publication_number,
                        str(record.get("url") or ""),
                        str(record.get("abstract") or ""),
                        str(record.get("analysis_basis") or ""),
                        1 if record.get("bibliographic_match") is True else 0,
                        str(record.get("limitations") or ""),
                        dumps(record),
                    ),
                )
        if patent_result.status != "ok":
            error = {
                "message": "Patent prior-art coverage is incomplete",
                "patent_database_status": patent_result.status,
                "patent_database_message": patent_result.message,
            }
            self._mark_job(job.id, "retryable_failed", result=payload, error=error)
            self.repo.update_patent_stage_run(
                prior_art_stage.id,
                PatentStageRunUpdate(status="failed", job_id=job.id, output={**payload, "error": error}),
            )
            return {
                "job_id": job.id,
                "status": "retryable_failed",
                "result": payload,
                "error": error,
            }
        if academic_result.status not in {"ok", "degraded"}:
            self.repo.update_patent_stage_run(
                prior_art_stage.id,
                PatentStageRunUpdate(
                    status="failed",
                    job_id=job.id,
                    output={"academic_status": academic_result.status, "message": academic_result.message},
                ),
            )
            return self._finish_adapter_job(job.id, academic_result)
        self._mark_job(job.id, "succeeded", result=payload)
        self.repo.update_patent_stage_run(
            prior_art_stage.id,
            PatentStageRunUpdate(status="succeeded", job_id=job.id, output=payload),
        )
        return {"job_id": job.id, "status": "succeeded", "result": payload, "error": {}}

    def run_relate_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "relate", job_id)
        self._mark_job(job.id, "running")
        scope = job.target_id if job.target_type == "paper" else None
        result = self.repo.rebuild_relations(scope)
        self._mark_job(job.id, "succeeded", result=result)
        return {"job_id": job.id, "status": "succeeded", "result": result, "error": {}}

    def run_revise_job(self, job_id: str) -> dict[str, Any]:
        job = self.repo.get_job(job_id)
        self._require_job(job.kind, "revise", job_id)
        self._mark_job(job.id, "running")
        draft = self.repo.get_patent_draft(job.target_id)
        instruction = str(job.request.get("instruction") or "").strip()
        section = str(job.request.get("section") or "整体").strip()
        notes = str(job.request.get("notes") or "").strip()
        version_label = f"v-{stable_hash([draft.id, job.request])[:8]}"
        output_root = Path(
            os.getenv("RESEARCH_HUB_EXPORT_DIR", str(Path.cwd() / "exports"))
        ) / "patent_drafts"
        output = PatentOutputService(self.conn, output_root=output_root).generate_outputs(
            draft.invention_candidate_id,
            case_name=draft.case_name,
            notes=f"修订章节：{section}\n修订指令：{instruction}\n{notes}",
            version_label=version_label,
        )
        result = {
            "draft_id": output.draft.id,
            "version_label": output.version_label,
            "markdown_artifact": output.artifacts.markdown_artifact,
            "docx_artifact": output.artifacts.docx_artifact,
        }
        self._mark_job(job.id, "succeeded", result=result)
        return {"job_id": job.id, "status": "succeeded", "result": result, "error": {}}

    def run_job(self, job_id: str) -> dict[str, Any]:
        job = self._claim_job(job_id)
        if job is None:
            return self._job_snapshot(self.repo.get_job(job_id))
        if job.kind == "discover":
            return self.run_discovery_run(job.target_id)
        if job.kind == "download":
            return self.run_download_job(job.id)
        if job.kind == "parse":
            return self.run_parse_job(job.id)
        if job.kind == "analyze":
            return self.run_analyze_job(job.id)
        if job.kind == "translate":
            return self.run_translate_job(job.id)
        if job.kind == "render_pdf":
            return self.run_render_pdf_job(job.id)
        if job.kind == "prior_art_check":
            return self.run_prior_art_job(job.id)
        if job.kind == "relate":
            return self.run_relate_job(job.id)
        if job.kind == "revise":
            return self.run_revise_job(job.id)
        result = AdapterResult.failed(f"Unsupported executable job kind: {job.kind}", job_id=job.id)
        return self._finish_adapter_job(job.id, result)

    # Fast, bounded file/network jobs should always make progress even when
    # slow LLM jobs (analyze/translate) are stuck waiting on the upstream
    # model.  Ordering every queued job by created_at ASC (FIFO) plus a
    # kind-priority batch means the scheduler worker drains downloads first
    # and cannot be starved by a hung LLM call upstream.
    _FAST_JOB_KINDS = ("download", "render_pdf", "parse", "relate")
    _SLOW_JOB_KINDS = ("analyze", "translate", "prior_art_check", "revise", "discover")

    def run_queued_jobs_once(self, *, limit: int = 10) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        queued = self.repo.list_jobs(status="queued", limit=2000)
        # FIFO within each tier: oldest first so a long-stuck backlog drains.
        queued.sort(key=lambda job: (job.created_at, job.id))
        fast = [job for job in queued if job.kind in self._FAST_JOB_KINDS]
        rest = [job for job in queued if job.kind not in self._FAST_JOB_KINDS]
        selected = (fast + rest)[:limit]
        return [self.run_job(job.id) for job in selected]

    def poll_running_jobs_once(self, *, limit: int = 10) -> list[dict[str, Any]]:
        jobs = [
            job
            for job in self.repo.list_jobs(status="running", kind="parse")
            if _poll_due(job.next_poll_after)
        ][:limit]
        return [self.poll_parse_job(job.id) for job in jobs]

    def _topic_queries(self, topic_ids: list[str], max_results: int | None) -> list[TopicQuery]:
        requested = set(topic_ids)
        rows = self.conn.execute(
            "SELECT * FROM topic WHERE enabled = 1 ORDER BY id"
        ).fetchall()
        queries: list[TopicQuery] = []
        for row in rows:
            topic_id = row["id"]
            if requested and topic_id not in requested:
                continue
            aliases = tuple(loads(row["aliases_json"], []) or [row["name_en"]])
            rules = loads(row["rules_json"], {})
            categories = tuple(rules.get("arxiv_categories") or ("cs.AI", "cs.LG", "cs.CL", "cs.AR", "cs.DC"))
            quota = max_results or int(rules.get("daily_quota") or 25)
            queries.append(
                TopicQuery(
                    topic_id=topic_id,
                    display_name=row["name_zh"],
                    include_terms=aliases,
                    categories=categories,
                    max_results=quota,
                )
            )
        return queries

    def _persist_paper_hit(
        self,
        hit: dict[str, Any],
        topic: TopicQuery,
        rank: int,
        run: Any,
    ) -> Any:
        source = str(hit.get("source") or "arxiv")
        source_id = str(hit.get("source_id") or hit.get("stable_key") or hit.get("title"))
        publication_date = _date_from_value(hit.get("published_at"))
        identifiers = [PaperIdentifier(type=source, value=source_id)]
        if hit.get("doi"):
            identifiers.append(PaperIdentifier(type="doi", value=str(hit["doi"])))
        stable_key = hit.get("stable_key")
        if stable_key:
            identifiers.append(PaperIdentifier(type="stable_key", value=str(stable_key)))

        data = PaperCreate(
            canonical_title=str(hit.get("title") or source_id),
            abstract=str(hit.get("abstract") or ""),
            language="en",
            first_publication_date=publication_date,
            status="discovered",
            identifiers=identifiers,
            topics=[topic.topic_id],
            metadata={
                "authors": hit.get("authors") or [],
                "categories": hit.get("categories") or [],
                "landing_url": hit.get("landing_url"),
                "discovery_run_id": run.id,
            },
            version=PaperVersionCreate(
                version_label=_arxiv_version_label(hit),
                source=source,
                source_version_id=source_id,
                publication_date=publication_date,
                pdf_url=hit.get("pdf_url"),
                metadata={"raw": hit.get("raw") or {}, "updated_at": hit.get("updated_at")},
            ),
            source_hit=PaperSourceHitCreate(
                source=source,
                query=" OR ".join(topic.include_terms),
                rank=rank,
                hit_date=_hit_date(run),
                raw_summary=hit,
            ),
        )
        existing_paper_id = self._find_existing_paper_id(hit)
        if existing_paper_id:
            self.conn.execute(
                """
                UPDATE paper
                SET canonical_title = COALESCE(NULLIF(?, ''), canonical_title),
                    abstract = COALESCE(NULLIF(?, ''), abstract),
                    translated_abstract = CASE
                        WHEN NULLIF(?, '') IS NOT NULL AND NULLIF(?, '') <> abstract THEN NULL
                        ELSE translated_abstract
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    data.canonical_title,
                    data.abstract,
                    data.abstract,
                    data.abstract,
                    utcnow(),
                    existing_paper_id,
                ),
            )
            for identifier in data.identifiers:
                self.repo.add_identifier(existing_paper_id, identifier)
            self.repo.add_topic(
                existing_paper_id,
                topic.topic_id,
                {"source": source, "discovery_run_id": run.id},
            )
            version = self.repo.create_paper_version(existing_paper_id, data.version)
            self.repo.add_source_hit(existing_paper_id, version.id, data.source_hit)
            return self.repo.get_paper(existing_paper_id)
        return self.repo.create_paper(data)

    def _pipeline_for_discovery(self, run: Any) -> Any:
        existing = self.conn.execute(
            "SELECT id FROM pipeline_run WHERE discovery_run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run.id,),
        ).fetchone()
        if existing:
            return self.repo.get_pipeline_run(existing["id"])
        return self.repo.create_pipeline_run(
            PipelineRunCreate(
                run_type="daily-paper-intelligence",
                source=run.source,
                status="running",
                discovery_run_id=run.id,
                job_id=run.job_id,
                window_start=_datetime_from_value(run.window_start),
                window_end=_datetime_from_value(run.window_end),
                metadata={"auto_process": True},
            )
        )

    def _enqueue_discovered_version(
        self,
        *,
        paper_id: str,
        version_id: str,
        discovery_run_id: str,
        pipeline_run_id: str | None,
    ) -> dict[str, str] | None:
        version = self.repo.get_paper_version(version_id)
        if not version.pdf_url:
            return None
        existing = self.repo.list_jobs(
            kind="download",
            target_type="paper_version",
            target_id=version_id,
        )
        if existing:
            return {
                "paper_id": paper_id,
                "paper_version_id": version_id,
                "job_id": existing[0].id,
                "status": existing[0].status,
            }
        created = self.repo.create_job(
            "download",
            "paper_version",
            version_id,
            {
                "source": "discovery_chain",
                "discovery_run_id": discovery_run_id,
                "pipeline_run_id": pipeline_run_id,
                "after_parse": list(self.runtime_config["schedule"]["after_parse"]),
            },
            idempotency_key=f"discovery-chain:{version_id}:download",
        )
        return {
            "paper_id": paper_id,
            "paper_version_id": version_id,
            "job_id": created.job_id,
            "status": created.status,
        }

    def _enqueue_abstract_translation(
        self,
        *,
        paper_id: str,
        version_id: str,
        discovery_run_id: str,
        pipeline_run_id: str | None,
    ) -> dict[str, str] | None:
        paper = self.repo.get_paper(paper_id)
        abstract = paper.abstract.strip()
        if not abstract or paper.translated_abstract:
            return None
        if not self._analysis_configured():
            return None
        created = self.repo.create_job(
            "translate",
            "paper_version",
            version_id,
            {
                "source": "discovery_chain",
                "mode": "abstract",
                "paper_id": paper_id,
                "discovery_run_id": discovery_run_id,
                "pipeline_run_id": pipeline_run_id,
            },
            idempotency_key=(
                f"discovery-chain:{version_id}:translate-abstract:{stable_hash(abstract)[:16]}"
            ),
        )
        return {
            "paper_id": paper_id,
            "paper_version_id": version_id,
            "job_id": created.job_id,
            "kind": "translate_abstract",
            "status": created.status,
        }

    def _analysis_configured(self) -> bool:
        analysis = self.runtime_config.get("analysis") or {}
        provider = str(analysis.get("provider") or "openai")
        selected = analysis.get(provider) or {}
        if provider == "openai":
            return bool(selected.get("base_url") and selected.get("model"))
        if provider == "dify":
            return bool(selected.get("base_url") and selected.get("api_key"))
        return False

    def enqueue_pending_abstract_translations(self) -> list[dict[str, str]]:
        """Queue Chinese abstract translation for papers already in the repository."""

        if not self._analysis_configured():
            return []
        queued: list[dict[str, str]] = []
        for paper in self.repo.list_papers():
            if not paper.current_version_id or not paper.abstract.strip() or paper.translated_abstract:
                continue
            existing = [
                job
                for job in self.repo.list_jobs(
                    kind="translate",
                    target_type="paper_version",
                    target_id=paper.current_version_id,
                )
                if job.request.get("mode") == "abstract"
            ]
            if existing:
                latest = existing[0]
                if latest.status in {"retryable_failed", "terminal_failed", "cancelled"}:
                    retried = self.repo.retry_job(
                        latest.id,
                        JobRetryRequest(reason="LLM configuration was updated"),
                    )
                    queued.append(
                        {
                            "paper_id": paper.id,
                            "paper_version_id": paper.current_version_id,
                            "job_id": retried.id,
                            "kind": "translate_abstract",
                            "status": retried.status,
                        }
                    )
                continue
            created = self.repo.create_job(
                "translate",
                "paper_version",
                paper.current_version_id,
                {
                    "source": "runtime_config_backfill",
                    "mode": "abstract",
                    "paper_id": paper.id,
                },
                idempotency_key=(
                    f"abstract-backfill:{paper.current_version_id}:{stable_hash(paper.abstract)[:16]}"
                ),
            )
            if self.repo.last_job_created:
                queued.append(
                    {
                        "paper_id": paper.id,
                        "paper_version_id": paper.current_version_id,
                        "job_id": created.job_id,
                        "kind": "translate_abstract",
                        "status": created.status,
                    }
                )
        return queued

    def _find_existing_paper_id(self, hit: dict[str, Any]) -> str | None:
        identifiers = []
        source = str(hit.get("source") or "arxiv")
        if hit.get("source_id"):
            identifiers.append(PaperIdentifier(type=source, value=str(hit["source_id"])))
        if hit.get("doi"):
            identifiers.append(PaperIdentifier(type="doi", value=str(hit["doi"])))
        if hit.get("stable_key"):
            identifiers.append(PaperIdentifier(type="stable_key", value=str(hit["stable_key"])))
        identifier_match = self.repo.find_paper_by_identifiers(identifiers)
        if identifier_match:
            return identifier_match

        title_key = _normalized_title(str(hit.get("title") or ""))
        if not title_key:
            return None
        published = _date_from_value(hit.get("published_at"))
        year = str(published.year) if published else None
        first_author = _normalized_author((hit.get("authors") or [""])[0])
        rows = self.conn.execute(
            """
            SELECT id, canonical_title, first_publication_date, metadata_json
            FROM paper
            WHERE (? IS NULL OR substr(CAST(first_publication_date AS TEXT), 1, 4) = ?)
            """,
            (year, year),
        ).fetchall()
        for row in rows:
            if _normalized_title(row["canonical_title"]) != title_key:
                continue
            metadata = loads(row["metadata_json"], {})
            authors = metadata.get("authors") or []
            existing_author = _normalized_author(authors[0] if authors else "")
            if not first_author or not existing_author or first_author == existing_author:
                return str(row["id"])
        return None

    def _resolve_pdf_path(self, version_id: str, request: dict[str, Any]) -> Path | None:
        option_path = (request.get("options") or {}).get("pdf_path") or request.get("pdf_path")
        candidates = [option_path] if option_path else []
        rows = self.conn.execute(
            """
            SELECT uri FROM artifact
            WHERE paper_version_id = ? AND artifact_type IN ('pdf', 'source_pdf')
            ORDER BY created_at DESC
            """,
            (version_id,),
        ).fetchall()
        candidates.extend(row["uri"] for row in rows)
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(str(candidate).removeprefix("file://")).expanduser()
            if path.is_file():
                return path.resolve()
        return None

    def _read_markdown_artifact(
        self,
        version_id: str,
        *,
        preferred_types: tuple[str, ...] = (
            "markdown_original",
            "mineru_markdown",
            "markdown",
            "markdown_zh",
            "markdown_bilingual",
            "translation_markdown",
        ),
    ) -> str | None:
        placeholders = ", ".join("?" for _ in preferred_types)
        ordering = "CASE artifact_type " + " ".join(
            f"WHEN '{artifact_type}' THEN {index}"
            for index, artifact_type in enumerate(preferred_types)
        ) + " ELSE 999 END"
        rows = self.conn.execute(
            f"""
            SELECT uri, metadata_json FROM artifact
            WHERE paper_version_id = ? AND artifact_type IN ({placeholders})
            ORDER BY {ordering}, created_at DESC
            """,
            (version_id, *preferred_types),
        ).fetchall()
        for row in rows:
            metadata = loads(row["metadata_json"], {})
            content = metadata.get("content")
            if content:
                return str(content)
            uri = str(row["uri"])
            if uri.startswith("inline://"):
                continue
            path = Path(uri.removeprefix("file://")).expanduser()
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        return None

    def _artifact_text(self, artifact_id: str) -> str | None:
        if not artifact_id:
            return None
        row = self.conn.execute(
            "SELECT uri, metadata_json FROM artifact WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if not row:
            return None
        metadata = loads(row["metadata_json"], {})
        if metadata.get("content"):
            return str(metadata["content"])
        uri = str(row["uri"])
        if uri.startswith("inline://"):
            return None
        path = Path(uri.removeprefix("file://")).expanduser()
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    def _paper_package(
        self,
        version_id: str,
        markdown: str | None,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        refs = tuple(
            {
                "artifact_id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "media_type": artifact.media_type,
                "checksum": artifact.checksum,
                "uri": artifact.uri,
            }
            for artifact in self.repo.list_version_artifacts(version_id)
        )
        if not markdown:
            return refs, ()
        headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", markdown))
        sections: list[dict[str, Any]] = []
        for index, match in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
            sections.append(
                {
                    "section_id": f"section-{index + 1}",
                    "title": match.group(2).strip(),
                    "level": len(match.group(1)),
                    "source_artifact_type": "markdown_original",
                    "start": match.start(),
                    "end": end,
                    "char_count": end - match.start(),
                }
            )
        return refs, tuple(sections)

    def _upsert_report(
        self,
        version_id: str,
        paper_id: str,
        title: str,
        adapter_result: AdapterResult,
        source: str,
    ) -> dict[str, Any]:
        report, quality = self._structured_report(adapter_result)
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO paper_report (
                id, paper_version_id, summary, motivation, method, experiments,
                results, innovation, limitations, engineering_value,
                reproduction_plan, score_json, evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_version_id) DO UPDATE SET
                summary = excluded.summary,
                motivation = excluded.motivation,
                method = excluded.method,
                experiments = excluded.experiments,
                results = excluded.results,
                innovation = excluded.innovation,
                limitations = excluded.limitations,
                engineering_value = excluded.engineering_value,
                reproduction_plan = excluded.reproduction_plan,
                score_json = excluded.score_json,
                evidence_json = excluded.evidence_json,
                updated_at = excluded.updated_at
            """,
            (
                new_id("report"),
                version_id,
                report["summary"],
                report["motivation"],
                report["method"],
                report["experiments"],
                report["results"],
                report["innovation"],
                report["limitations"],
                report["engineering_value"],
                report["reproduction_plan"],
                dumps(
                    {
                        **(report.get("score") or {}),
                        "source": source,
                        "paper_id": paper_id,
                        "title": title,
                        **quality,
                    }
                ),
                dumps(report["evidence"]),
                now,
                now,
            ),
        )
        report_row = self.conn.execute(
            "SELECT id FROM paper_report WHERE paper_version_id = ?",
            (version_id,),
        ).fetchone()
        if report_row:
            report_id = str(report_row["id"])
            self.conn.execute("DELETE FROM evidence_anchor WHERE paper_report_id = ?", (report_id,))
            self.conn.execute("DELETE FROM technology_claim WHERE paper_report_id = ?", (report_id,))
            anchor_ids_by_field: dict[str, list[str]] = {}
            for item in report["evidence"]:
                anchor_id = new_id("evidence")
                report_field = str(item.get("report_field") or item.get("section") or "")
                self.conn.execute(
                    """
                    INSERT INTO evidence_anchor (
                        id, paper_report_id, report_field, kind, source,
                        section, page, quote, quote_hash, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anchor_id,
                        report_id,
                        report_field or None,
                        str(item.get("kind") or "analysis"),
                        str(item.get("source") or source),
                        item.get("section"),
                        str(item.get("page")) if item.get("page") is not None else None,
                        item.get("quote"),
                        item.get("quote_hash"),
                        dumps({"note": item.get("note") or ""}),
                    ),
                )
                anchor_ids_by_field.setdefault(report_field, []).append(anchor_id)
            for claim_type in (
                "motivation",
                "method",
                "experiments",
                "results",
                "innovation",
                "limitations",
                "engineering_value",
                "reproduction_plan",
            ):
                statement = report[claim_type]
                if statement == "论文未报告":
                    continue
                self.conn.execute(
                    """
                    INSERT INTO technology_claim (
                        id, paper_report_id, paper_id, claim_type, statement,
                        evidence_anchor_ids_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("claim"),
                        report_id,
                        paper_id,
                        claim_type,
                        statement,
                        dumps(anchor_ids_by_field.get(claim_type, [])),
                    ),
                )
        return quality

    def _structured_report(self, adapter_result: AdapterResult) -> tuple[dict[str, Any], dict[str, Any]]:
        required = (
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
        candidate: Any = adapter_result.data.get("report")
        response = adapter_result.data.get("response")
        if candidate is None and isinstance(response, dict):
            outputs = response.get("data", {}).get("outputs") if isinstance(response.get("data"), dict) else None
            if isinstance(outputs, dict):
                candidate = outputs.get("report_json") or outputs.get("report")
            candidate = candidate or response.get("report_json") or response.get("report")
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                candidate = None
        report = dict(candidate) if isinstance(candidate, dict) else {}
        if not report:
            report["summary"] = self._extract_text(adapter_result) or adapter_result.message
        missing: list[str] = []
        for field in required:
            value = str(report.get(field) or "").strip()
            if not value:
                report[field] = "论文未报告"
                missing.append(field)
            else:
                report[field] = value

        raw_evidence = report.get("evidence")
        evidence = [dict(item) for item in raw_evidence or [] if isinstance(item, dict)]
        for item in evidence:
            if item.get("quote") and not item.get("quote_hash"):
                item["quote_hash"] = stable_hash(str(item["quote"]))
        report["evidence"] = evidence
        substantive = [field for field in required if report[field] != "论文未报告"]
        anchored = {
            str(item.get("report_field") or item.get("section") or "").strip()
            for item in evidence
            if item.get("kind") == "fact"
            and (item.get("page") is not None or item.get("section") or item.get("quote"))
        }
        supported = sum(1 for field in substantive if field in anchored)
        coverage = supported / max(1, len(substantive))
        quality = {
            "quality_status": "complete" if coverage >= 0.9 else "incomplete",
            "evidence_coverage": round(coverage, 4),
            "missing_sections": missing,
            "required_evidence_coverage": 0.9,
        }
        return report, quality

    def _translation_outputs(
        self,
        adapter_result: AdapterResult,
        source_markdown: str | None,
    ) -> tuple[str, str]:
        data = adapter_result.data
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        outputs = response.get("data", {}).get("outputs") if isinstance(response.get("data"), dict) else {}
        outputs = outputs if isinstance(outputs, dict) else {}
        zh = str(
            data.get("markdown_zh")
            or outputs.get("markdown_zh")
            or self._extract_text(adapter_result)
            or ""
        ).strip()
        bilingual = str(data.get("markdown_bilingual") or outputs.get("markdown_bilingual") or "").strip()
        if not bilingual and zh:
            bilingual = (
                "# 原文\n\n"
                + (source_markdown or "论文原文结构化 Markdown 不可用。")
                + "\n\n# 中文译文\n\n"
                + zh
            )
        return zh, bilingual

    def _abstract_translation_output(self, adapter_result: AdapterResult) -> str:
        data = adapter_result.data
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        outputs = response.get("data", {}).get("outputs") if isinstance(response.get("data"), dict) else {}
        outputs = outputs if isinstance(outputs, dict) else {}
        return str(
            data.get("abstract_zh")
            or data.get("translated_abstract")
            or outputs.get("abstract_zh")
            or outputs.get("translated_abstract")
            or outputs.get("text")
            or outputs.get("answer")
            or ""
        ).strip()

    def _method_summary_output(self, adapter_result: AdapterResult) -> str:
        """Extract an optional one-line Chinese method summary from the adapter
        response. Returns '' when the provider did not return one, so the paper
        can still be shown with only its translated abstract."""
        data = adapter_result.data
        response = data.get("response") if isinstance(data.get("response"), dict) else {}
        outputs = response.get("data", {}).get("outputs") if isinstance(response.get("data"), dict) else {}
        outputs = outputs if isinstance(outputs, dict) else {}
        return str(
            data.get("method_summary")
            or outputs.get("method_summary")
            or outputs.get("one_liner")
            or ""
        ).strip()

    def _extract_text(self, adapter_result: AdapterResult) -> str:
        data = adapter_result.data
        for key in ("markdown", "summary", "text", "content"):
            if data.get(key):
                return str(data[key])
        response = data.get("response")
        if isinstance(response, dict):
            outputs = response.get("data", {}).get("outputs") if isinstance(response.get("data"), dict) else None
            if isinstance(outputs, dict):
                for key in ("markdown", "summary", "text", "content", "answer"):
                    if outputs.get(key):
                        return str(outputs[key])
            for key in ("markdown", "summary", "text", "content", "answer"):
                if response.get(key):
                    return str(response[key])
        return ""

    def _finish_adapter_job(
        self,
        job_id: str,
        adapter_result: AdapterResult,
        *,
        external_task_id: str | None = None,
    ) -> dict[str, Any]:
        status = _job_status(adapter_result.status)
        result = {"adapter_status": adapter_result.status, "message": adapter_result.message, **adapter_result.data}
        error = {} if adapter_result.status == "ok" else {"message": adapter_result.message, **adapter_result.data}
        self._mark_job(job_id, status, result=result, error=error, external_task_id=external_task_id)
        return {"job_id": job_id, "status": status, "result": result, "error": error}

    def _claim_job(self, job_id: str) -> Any | None:
        cursor = self.conn.execute(
            """
            UPDATE job
            SET status = 'running', next_poll_after = NULL, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (utcnow(), job_id),
        )
        if cursor.rowcount != 1:
            return None
        return self.repo.get_job(job_id)

    @staticmethod
    def _job_snapshot(job: Any) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "status": job.status,
            "result": job.result,
            "error": job.error,
            "next_poll_after": job.next_poll_after,
        }

    def _enqueue_after_parse(self, version_id: str, job: Any) -> list[dict[str, str]]:
        chained: list[dict[str, str]] = []
        for kind in _after_parse_jobs(job.request):
            created = self.repo.create_job(
                kind,
                "paper_version",
                version_id,
                {
                    "source": "parse_chain",
                    "parse_job_id": job.id,
                    "pipeline_run_id": job.request.get("pipeline_run_id"),
                },
                idempotency_key=f"parse-chain:{version_id}:{kind}",
            )
            chained.append({"kind": kind, "job_id": created.job_id, "status": created.status})
        return chained

    def _mark_job(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        external_task_id: str | None = None,
        next_poll_after: str | None = None,
    ) -> None:
        if status == "running":
            open_attempt = self.conn.execute(
                "SELECT id FROM job_attempt WHERE job_id = ? AND completed_at IS NULL",
                (job_id,),
            ).fetchone()
            if not open_attempt:
                attempt_no = int(
                    self.conn.execute(
                        "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS n FROM job_attempt WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()["n"]
                )
                self.conn.execute(
                    """
                    INSERT INTO job_attempt (
                        id, job_id, attempt_no, status, started_at
                    ) VALUES (?, ?, ?, 'running', ?)
                    """,
                    (new_id("attempt"), job_id, attempt_no, utcnow()),
                )
        elif status in {
            "succeeded",
            "partial_succeeded",
            "retryable_failed",
            "terminal_failed",
            "cancelled",
        }:
            self.conn.execute(
                """
                UPDATE job_attempt
                SET status = ?, error_json = ?, completed_at = ?
                WHERE id = (
                    SELECT id FROM job_attempt
                    WHERE job_id = ? AND completed_at IS NULL
                    ORDER BY attempt_no DESC LIMIT 1
                )
                """,
                (status, dumps(error or {}), utcnow(), job_id),
            )
        if status in {"retryable_failed", "terminal_failed"} and error:
            error = dict(error)
            if not error.get("llm_analysis"):
                row = self.conn.execute(
                    "SELECT kind, target_type, target_id FROM job WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if row:
                    error["llm_analysis"] = self._llm_analyze_failure(
                        kind=row["kind"],
                        target_type=row["target_type"],
                        target_id=row["target_id"],
                        error=error,
                    )
        self.conn.execute(
            """
            UPDATE job
            SET status = ?, result_json = ?, error_json = ?,
                external_task_id = COALESCE(?, external_task_id),
                next_poll_after = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                dumps(result or {}),
                dumps(error or {}),
                external_task_id,
                next_poll_after,
                utcnow(),
                job_id,
            ),
        )

    def _mark_discovery_run(
        self,
        run_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        row = self.conn.execute(
            "SELECT metadata_json FROM discovery_run WHERE id = ?",
            (run_id,),
        ).fetchone()
        metadata = loads(row["metadata_json"], {}) if row else {}
        if result:
            metadata["result"] = result
        failed = status in {"retryable_failed", "terminal_failed"}
        if failed and error:
            error = dict(error)
            error.setdefault("llm_analysis", self._llm_analyze_failure(
                kind="discover",
                target_type="discovery_run",
                target_id=run_id,
                error=error,
            ))
        if error:
            metadata["error"] = error
        self.conn.execute(
            """
            UPDATE discovery_run
            SET status = ?, metadata_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (status, dumps(metadata), utcnow(), run_id),
        )

    def _llm_analyze_failure(
        self,
        *,
        kind: str,
        target_type: str,
        target_id: str,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask the configured LLM to explain why a job/run step failed.

        Failure diagnosis is best-effort: it is skipped silently when no
        OpenAI-compatible model is configured, and any adapter/parse error is
        captured into ``analysis_error`` instead of being re-raised so it never
        breaks the job workflow itself.
        """
        analysis = self.runtime_config.get("analysis") or {}
        provider = str(analysis.get("provider") or "openai")
        if provider != "openai":
            # Dify uses a workflow, which is not a general chat API we can use
            # for ad-hoc failure diagnosis. Fall back to a rule-based summary.
            return {
                "generated_by": provider,
                "available": False,
                "reason": "当前分析提供商为 Dify，不支持即席失败诊断",
            }
        if not self._analysis_configured():
            return {
                "generated_by": "openai",
                "available": False,
                "reason": "未配置 OpenAI 兼容模型，跳过自动失败原因分析",
            }
        import logging

        logger = logging.getLogger("research_hub.failure_analysis")
        stage_label = {
            "discover": "论文发现",
            "download": "PDF 下载",
            "parse": "文档解析",
            "analyze": "LLM 研读",
            "translate": "中文翻译",
            "render_pdf": "PDF 渲染",
            "prior_art_check": "现有技术查新",
            "relate": "关系构建",
            "revise": "交底书修订",
        }.get(kind, kind)
        package = json.dumps(
            {
                "kind": kind,
                "stage": stage_label,
                "target_type": target_type,
                "target_id": target_id,
                "status": error.get("status"),
                "error": error,
            },
            ensure_ascii=False,
            default=str,
        )[:12_000]
        prompt = (
            "你是平台故障诊断专家。下面是一个失败环节的原始错误信息 JSON，"
            "请分析该环节为何失败、失败在哪个阶段、最可能的原因，以及如何修复。\n"
            "只输出一个 JSON 对象，不要 Markdown 围栏，包含四个字段：\n"
            '1. "category"：失败类别（如 network / auth / config / data / upstream / timeout / unknown）；\n'
            '2. "reason"：一句话中文总结失败原因；\n'
            '3. "detail"：更详细的成因分析（一到三句）；\n'
            '4. "suggestion"：给用户的一条具体修复建议。\n\n'
            "失败原始信息：\n"
            + package
        )
        try:
            adapter = _default_analysis_adapter(self.runtime_config)
            response = adapter._chat(
                system="你是严谨的平台故障诊断助手，只依据提供的错误信息推断原因，不编造事实。",
                user=prompt,
            )
            if response.status != "ok":
                return {
                    "generated_by": "openai",
                    "available": True,
                    "analysis_error": response.message,
                    "reason": "LLM 诊断接口调用失败",
                }
            content = str(response.data.get("content") or "").strip()
            try:
                parsed = _json_object(content)
            except Exception as exc:
                return {
                    "generated_by": "openai",
                    "available": True,
                    "analysis_error": f"无法解析诊断结果: {exc}",
                    "reason": content[:2000],
                }
            return {
                "generated_by": "openai",
                "available": True,
                "category": str(parsed.get("category") or "unknown"),
                "reason": str(parsed.get("reason") or "").strip(),
                "detail": str(parsed.get("detail") or "").strip(),
                "suggestion": str(parsed.get("suggestion") or "").strip(),
                "model": response.data.get("model"),
            }
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("LLM failure analysis crashed for %s (%s)", kind, target_id)
            return {
                "generated_by": "openai",
                "available": False,
                "analysis_error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def _require_job(actual: str, expected: str, job_id: str) -> None:
        if actual != expected:
            raise JobKindMismatchError(job_id, actual, expected)

    @staticmethod
    def _external_task_id(result: AdapterResult) -> str | None:
        response = result.data.get("response")
        if isinstance(response, dict):
            value = response.get("job_id") or response.get("task_id") or response.get("id")
            return str(value) if value else None
        value = result.data.get("job_id") or result.data.get("task_id") or result.data.get("id")
        return str(value) if value else None


def run_discovery_run(conn: sqlite3.Connection, run_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).run_discovery_run(run_id)


def run_download_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).run_download_job(job_id)


def run_parse_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).run_parse_job(job_id)


def run_analyze_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).run_analyze_job(job_id)


def run_translate_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).run_translate_job(job_id)


def run_render_pdf_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).run_render_pdf_job(job_id)


def poll_parse_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> dict[str, Any]:
    return ResearchJobService(conn, **kwargs).poll_parse_job(job_id)


def run_queued_jobs_once(conn: sqlite3.Connection, *, limit: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
    return ResearchJobService(conn, **kwargs).run_queued_jobs_once(limit=limit)


def poll_running_jobs_once(conn: sqlite3.Connection, *, limit: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
    return ResearchJobService(conn, **kwargs).poll_running_jobs_once(limit=limit)


def _default_discovery_adapter() -> Any:
    configured = os.getenv(
        "RESEARCH_HUB_DISCOVERY_SOURCES",
        "arxiv,huggingface,openreview,openalex",
    )
    requested = {value.strip().lower() for value in configured.split(",") if value.strip()}
    adapters: list[Any] = []
    if "arxiv" in requested:
        adapters.append(ArxivDiscoveryAdapter())
    if "huggingface" in requested or "hf" in requested:
        adapters.append(HuggingFaceDailyPapersAdapter())
    if "openreview" in requested:
        adapters.append(OpenReviewDiscoveryAdapter())
    if "openalex" in requested:
        adapters.append(OpenAlexMetadataAdapter())
    if not adapters:
        return ArxivDiscoveryAdapter()
    return adapters[0] if len(adapters) == 1 else CompositeDiscoveryAdapter(adapters)


def _default_analysis_adapter(config: dict[str, Any]) -> Any:
    analysis = config["analysis"]
    if analysis["provider"] == "dify":
        selected = analysis["dify"]
        return DifyPaperDigestAdapter(
            base_url=selected["base_url"],
            api_key=selected["api_key"],
            workflow_id=selected.get("workflow_id") or "",
        )
    selected = analysis["openai"]
    return OpenAICompatibleResearchAdapter(
        base_url=selected["base_url"],
        api_key=selected["api_key"],
        model=selected["model"],
    )


def _date_from_value(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _normalized_title(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _normalized_author(value: Any) -> str:
    return "".join(character.lower() for character in str(value) if character.isalnum())


def _hit_date(run: Any) -> date:
    metadata = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
    configured = _date_from_value(metadata.get("hit_date"))
    if configured:
        return configured
    if run.window_start:
        parsed = _date_from_value(run.window_start)
        if parsed:
            return parsed
    if run.window_end:
        parsed = _date_from_value(run.window_end)
        if parsed:
            return parsed
    return datetime.now(timezone.utc).date()


def _arxiv_version_label(hit: dict[str, Any]) -> str:
    entry_id = str((hit.get("raw") or {}).get("entry_id") or "")
    tail = entry_id.rsplit("/", 1)[-1]
    if "v" in tail:
        suffix = tail.rsplit("v", 1)[-1]
        if suffix.isdigit():
            return f"v{suffix}"
    return "v1"


def _job_status(adapter_status: str) -> str:
    if adapter_status == "ok":
        return "succeeded"
    if adapter_status == "degraded":
        return "retryable_failed"
    return "terminal_failed"


def _poll_after(seconds: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _poll_due(value: str | None) -> bool:
    if not value:
        return True
    try:
        due_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at <= datetime.now(timezone.utc)


def _artifact_root() -> Path:
    return Path(
        os.getenv(
            "RESEARCH_HUB_ARTIFACT_ROOT",
            str(Path(__file__).resolve().parents[1] / "artifacts"),
        )
    ).expanduser().resolve()


def _mineru_recovery_limit() -> int:
    try:
        value = int(os.getenv("RESEARCH_HUB_MINERU_RECOVERY_LIMIT", "1"))
    except ValueError:
        return 1
    return max(0, value)


def _after_parse_jobs(request: dict[str, Any]) -> list[str]:
    """Jobs chained automatically after a PDF is parsed.

    Reading reports (``analyze``) are intentionally NOT auto-chained here:
    they are generated on demand when the user opens the 研读报告 tab in the
    reader (see ``POST /paper-versions/{version_id}/analyze``), which avoids
    spending LLM tokens on every parsed paper.  Only abstract translation
    (``translate``) is chained automatically.  Set
    ``RESEARCH_HUB_AFTER_PARSE_JOBS`` to include ``analyze`` to restore the
    old auto-analyze behavior for an operator who explicitly opts in.
    """
    configured = (request.get("options") or {}).get("after_parse", request.get("after_parse"))
    if configured is None:
        configured = os.getenv("RESEARCH_HUB_AFTER_PARSE_JOBS", "translate")
    if isinstance(configured, str):
        values = [item.strip() for item in configured.split(",")]
    else:
        values = [str(item).strip() for item in configured]
    allowed = {"translate"} if not _analyze_autochain_enabled() else {"analyze", "translate"}
    return [item for item in values if item in allowed]


def _analyze_autochain_enabled() -> bool:
    """Operator opt-in flag to restore the legacy auto-analyze behavior."""
    return os.getenv("RESEARCH_HUB_AUTO_ANALYZE", "").lower() in {"1", "true", "yes"}


def _after_translate_render_pdf(request: dict[str, Any]) -> bool:
    options = request.get("options") or {}
    if "render_pdf" in options:
        return bool(options["render_pdf"])
    if "render_pdf" in request:
        return bool(request["render_pdf"])
    return os.getenv("RESEARCH_HUB_RENDER_TRANSLATION_PDF", "").lower() in {"1", "true", "yes"}


def _hit_in_window(hit: dict[str, Any], window_start: str | None, window_end: str | None) -> bool:
    published = _datetime_from_value(hit.get("published_at") or hit.get("updated_at"))
    if published is None:
        return True
    start = _datetime_from_value(window_start)
    end = _datetime_from_value(window_end)
    if start and published < start:
        return False
    if end and published >= end:
        return False
    return True


def _datetime_from_value(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _artifact_create(
    kind: str,
    uri: str,
    media_type: str,
    metadata: dict[str, Any],
    *,
    checksum: str | None = None,
) -> Any:
    from .models import ArtifactCreate

    return ArtifactCreate(
        artifact_type=kind,
        uri=uri,
        media_type=media_type,
        checksum=checksum,
        metadata=metadata,
    )
