"""FastAPI application for the unified Research Hub control plane."""

from __future__ import annotations

import os
from pathlib import Path
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from config.settings import Settings, get_settings

from .adapters import PatentEngineAdapter, TopicQuery
from .adapters.mineru import MinerUApiAdapter
from .adapters.prior_art import LocalCnipaPriorArtAdapter
from .auth import (
    ANONYMOUS_PRINCIPAL,
    AuthConfig,
    PERMISSION_ADMIN,
    PERMISSION_JOBS_MANAGE,
    PERMISSION_PATENT_WRITE,
    PERMISSION_RESEARCH_WRITE,
    Principal,
    authenticate_api_key,
    build_auth_config,
)
from .database import SCHEMA_VERSION, dumps
from .models import (
    ArtifactCreate,
    CandidateApproveRequest,
    DiscoveryRunCreate,
    DraftCreateRequest,
    HealthResponse,
    InventionCandidateCreate,
    JobCancelRequest,
    JobRetryRequest,
    PaperCreate,
    PaperSelectRequest,
    PatentDraftReviseRequest,
    TopicCreate,
    TopicDigestNoteUpdate,
    TopicPatch,
    VersionActionRequest,
)
from .observability import (
    METRICS,
    collect_job_metrics,
    current_trace_context,
    dead_letter_payload,
    replay_dead_letter_job,
    trace_context,
    trace_from_headers,
)
from .repository import ConflictError, NotFoundError, Repository
from .patent_service import PatentOutputService
from .postgres_runtime import create_database_from_env
from .runtime_config import load_runtime_config, public_runtime_config, update_runtime_config
from .services import ResearchJobService
from .workflows import workflow_payload


class JobActionRequest(BaseModel):
    action: str
    reason: str = ""


class RuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    auth_config = build_auth_config(
        legacy_api_key=settings.api_key,
        admin_api_key=settings.admin_api_key,
        researcher_api_key=settings.researcher_api_key,
        patent_editor_api_key=settings.patent_editor_api_key,
        read_only_api_key=settings.read_only_api_key,
    )
    if settings.public_mode and not auth_config.write_enabled:
        raise RuntimeError(
            "A write-capable API key is required when RESEARCH_HUB_PUBLIC=true"
        )
    database = create_database_from_env(settings.database_path)
    database.initialize()

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.settings = settings
    app.state.database = database
    app.state.auth_config = auth_config

    workspace_root = Path(__file__).resolve().parents[2]
    export_root = Path(
        os.environ.get(
            "RESEARCH_HUB_EXPORT_DIR",
            str(Path.cwd() / "exports"),
        )
    ).expanduser().resolve()
    artifact_root = Path(
        os.environ.get(
            "RESEARCH_HUB_ARTIFACT_ROOT",
            str(workspace_root / "research-platform" / "artifacts"),
        )
    ).expanduser().resolve()
    downloadable_roots = tuple(
        path.resolve()
        for path in (
            workspace_root / "mineru_service" / "project" / "daliy_pdf",
            export_root,
            artifact_root,
        )
    )
    hidden_artifact_metadata_keys = {
        "path",
        "manifest_path",
        "local_pdf_path",
        "mineru_markdown_path",
        "remote_markdown_path",
        "summary_path",
    }

    def public_artifact_payload(artifact: Any) -> dict[str, Any]:
        payload = artifact.model_dump(mode="json") if hasattr(artifact, "model_dump") else dict(artifact)
        payload.pop("uri", None)
        payload["download_url"] = f"/api/v1/artifacts/{payload['id']}/download"
        payload["metadata"] = sanitize_artifact_metadata(payload.get("metadata", {}))
        return payload

    def public_job_payload(job: Any) -> dict[str, Any]:
        payload = job.model_dump(mode="json") if hasattr(job, "model_dump") else dict(job)
        for key in ("request", "result", "error"):
            if key in payload:
                payload[key] = sanitize_stage_audit(payload.get(key, {}))
        return payload

    def sanitize_artifact_metadata(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: sanitize_artifact_metadata(item)
                for key, item in value.items()
                if key not in hidden_artifact_metadata_keys and not _is_raw_artifact_location(item)
            }
        if isinstance(value, list):
            return [
                sanitize_artifact_metadata(item)
                for item in value
                if not _is_raw_artifact_location(item)
            ]
        return value

    def public_patent_stage_payload(stage: Any) -> dict[str, Any]:
        payload = stage.model_dump(mode="json")
        for key in ("input", "output", "metadata"):
            payload[key] = sanitize_stage_audit(payload.get(key, {}))
        return payload

    def sanitize_stage_audit(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: sanitize_stage_audit(item)
                for key, item in value.items()
                if key not in hidden_artifact_metadata_keys and not _is_local_artifact_location(item)
            }
        if isinstance(value, list):
            return [
                sanitize_stage_audit(item)
                for item in value
                if not _is_local_artifact_location(item)
            ]
        return value

    def _is_local_artifact_location(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        if value.startswith("file://"):
            return True
        return Path(value).expanduser().is_absolute()

    def _is_raw_artifact_location(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        if value.startswith(("file://", "http://", "https://")):
            return True
        return Path(value).expanduser().is_absolute()

    def execute_job(job_id: str) -> None:
        """Run an accepted job with its own durable database transaction."""

        with database.connect() as conn:
            ResearchJobService(conn).run_job(job_id)

    app.mount(
        "/static",
        StaticFiles(directory=str(settings.static_dir), check_dir=False),
        name="static",
    )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "not_found",
                    "message": str(exc),
                    "details": {"entity": exc.entity, "id": exc.entity_id},
                }
            },
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "conflict", "message": str(exc), "details": {}}},
        )

    @app.middleware("http")
    async def attach_audit_principal(request: Request, call_next: Any) -> Response:
        principal = principal_from_headers(
            auth_config,
            x_api_key=request.headers.get("X-API-Key"),
            authorization=request.headers.get("Authorization"),
            reject_invalid=False,
        )
        request.state.audit_principal = principal
        return await call_next(request)

    @app.middleware("http")
    async def bind_trace(request: Request, call_next: Any) -> Response:
        started_at = time.monotonic()
        with trace_context(**trace_from_headers(request.headers)):
            response = await call_next(request)
            context = current_trace_context()
            response.headers["X-Trace-Id"] = context["trace_id"]
            if request_id := context.get("request_id"):
                response.headers["X-Request-Id"] = request_id
            METRICS.observe_duration_ms(
                "research_hub_http_request",
                started_at,
                method=request.method,
                path=request.url.path,
                status=str(response.status_code),
            )
            return response

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        with database.connect() as conn:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        return HealthResponse(
            status="ok",
            app=settings.app_name,
            database="ok",
            schema_version=int(row["value"]) if row else SCHEMA_VERSION,
        )

    @app.get("/api/v1/health", response_model=HealthResponse)
    def api_health() -> HealthResponse:
        return health()

    @app.get("/api/v1/adapter-health")
    def adapter_health() -> dict[str, Any]:
        runtime = load_runtime_config()
        analysis = runtime["analysis"]
        openai = analysis["openai"]
        dify_config = analysis["dify"]
        if analysis["provider"] == "openai":
            analysis_configured = bool(openai["base_url"] and openai["model"])
            analysis_message = (
                f"OpenAI-compatible model configured: {openai['model']}"
                if analysis_configured
                else "Set model Base URL and model name in Settings"
            )
        else:
            analysis_configured = bool(dify_config["base_url"] and dify_config["api_key"])
            analysis_message = (
                "Dify workflow configured"
                if analysis_configured
                else "Set Dify Base URL and API key in Settings"
            )
        mineru_config = runtime["services"]["mineru"]
        mineru = MinerUApiAdapter(
            base_url=mineru_config["base_url"],
            api_key=mineru_config["api_key"],
            timeout_seconds=5.0,
        )
        mineru_result = mineru.health()
        mineru_response = (
            mineru_result.data.get("response")
            if isinstance(mineru_result.data, dict)
            else None
        )
        mineru_meta = (
            mineru_response.get("meta")
            if isinstance(mineru_response, dict)
            else None
        )
        mineru_resolved = (
            mineru_meta.get("resolved_url")
            if isinstance(mineru_meta, dict)
            else None
        )
        mineru_configured = str(mineru_config.get("base_url") or "").strip()
        mineru_message = mineru_result.message
        if mineru_result.status == "ok":
            if mineru_resolved and mineru_resolved != mineru_configured:
                mineru_message = (
                    f"MinerU 已可用；自动发现并连接到 {mineru_resolved}"
                    f"（配置地址 {mineru_configured or '未设置'}）"
                )
        prior_art_config = runtime["services"]["prior_art"]
        if prior_art_config["mode"] == "local":
            prior_art_result = LocalCnipaPriorArtAdapter().health()
            prior_art_status = prior_art_result.status
            prior_art_message = prior_art_result.message
        else:
            prior_art_status = "online" if prior_art_config["base_url"] else "degraded"
            prior_art_message = (
                "remote prior-art service configured"
                if prior_art_config["base_url"]
                else "remote prior-art service is not configured"
            )
        patent_tool = PatentEngineAdapter().md_to_docx_path.expanduser().resolve()
        return {
            "items": [
                {
                    "name": "arxiv",
                    "status": "online",
                    "message": "arXiv discovery adapter is available; live requests use rate limiting and backoff.",
                },
                {
                    "name": "analysis",
                    "status": "online" if analysis_configured else "degraded",
                    "message": analysis_message,
                },
                {
                    "name": "mineru",
                    "status": mineru_result.status,
                    "message": mineru_message,
                    "detail": {
                        "configured_url": mineru_configured,
                        "resolved_url": mineru_resolved,
                        "used_discovered": bool(
                            mineru_resolved and mineru_resolved != mineru_configured
                        ),
                    },
                },
                {
                    "name": "patent",
                    "status": "online" if patent_tool.is_file() else "degraded",
                    "message": "local disclosure and DOCX tools available" if patent_tool.is_file() else "patent tool not found",
                },
                {
                    "name": "prior_art",
                    "status": prior_art_status,
                    "message": prior_art_message,
                },
            ]
        }

    def principal_from_headers(
        auth_config: AuthConfig,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None, alias="Authorization"),
        reject_invalid: bool = True,
    ) -> Principal:
        principal = authenticate_api_key(
            auth_config,
            x_api_key=x_api_key,
            authorization=authorization,
        )
        if principal is None:
            if not reject_invalid:
                return ANONYMOUS_PRINCIPAL
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        return principal

    def require_permission(permission: str):
        def dependency(
            request: Request,
            x_api_key: str | None = Header(default=None, alias="X-API-Key"),
            authorization: str | None = Header(default=None, alias="Authorization"),
        ) -> Principal:
            principal = principal_from_headers(
                auth_config,
                x_api_key=x_api_key,
                authorization=authorization,
            )
            request.state.audit_principal = principal
            if not principal.authenticated:
                raise HTTPException(status_code=401, detail="Invalid or missing API key")
            if not principal.can(permission):
                raise HTTPException(status_code=403, detail="Insufficient role permissions")
            return principal

        return dependency

    def repo() -> Repository:
        with database.connect() as conn:
            yield Repository(conn)

    def admin_repo(_: Principal = Depends(require_permission(PERMISSION_ADMIN))) -> Repository:
        with database.connect() as conn:
            yield Repository(conn)

    def jobs_repo(_: Principal = Depends(require_permission(PERMISSION_JOBS_MANAGE))) -> Repository:
        with database.connect() as conn:
            yield Repository(conn)

    def patent_repo(_: Principal = Depends(require_permission(PERMISSION_PATENT_WRITE))) -> Repository:
        with database.connect() as conn:
            yield Repository(conn)

    def research_repo(_: Principal = Depends(require_permission(PERMISSION_RESEARCH_WRITE))) -> Repository:
        """Protect mutations while keeping the public research UI readable."""

        with database.connect() as conn:
            yield Repository(conn)

    @app.get("/api/v1/metrics")
    def metrics(repository: Repository = Depends(repo)) -> dict[str, Any]:
        collect_job_metrics(repository.conn, registry=METRICS)
        return METRICS.snapshot()

    @app.get("/api/v1/stats")
    def stats(repository: Repository = Depends(repo)) -> dict[str, Any]:
        return repository.stats().model_dump(mode="json")

    @app.get("/api/v1/runtime-config")
    def get_runtime_config() -> dict[str, Any]:
        return {
            **public_runtime_config(load_runtime_config()),
            "platform": {
                "api_base": "/api/v1",
                "public_mode": settings.public_mode,
                "write_auth_required": auth_config.write_enabled,
            },
        }

    @app.put("/api/v1/runtime-config")
    def put_runtime_config(
        request: Request,
        body: RuntimeConfigUpdate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(admin_repo),
    ) -> JSONResponse:
        body_payload = body.model_dump(mode="python", exclude_unset=True)

        def save_config() -> tuple[int, dict[str, Any]]:
            saved = update_runtime_config(body_payload)
            queued = (
                ResearchJobService(repository.conn).enqueue_pending_abstract_translations()
                if "analysis" in body_payload
                else []
            )
            return 200, {
                **public_runtime_config(saved),
                "abstract_translation_jobs_queued": len(queued),
                "abstract_translation_jobs": queued,
            }

        status, payload = repository.use_idempotency(
            idempotency_key,
            "PUT",
            request.url.path,
            body_payload,
            save_config,
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/workflows")
    def workflows(repository: Repository = Depends(repo)) -> dict[str, Any]:
        return sanitize_stage_audit(workflow_payload(repository, load_runtime_config()))

    @app.get("/api/v1/topics")
    def list_topics(repository: Repository = Depends(repo)) -> dict[str, Any]:
        return {"items": [item.model_dump(mode="json") for item in repository.list_topics()]}

    @app.get("/api/v1/topics/{topic_id}/digest")
    def topic_digest(
        topic_id: str,
        date: str = Query(...),
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        return repository.daily_digest(date, topic_id=topic_id).model_dump(mode="json")

    @app.get("/api/v1/topics/{topic_id}/digest-note")
    def topic_digest_note(
        topic_id: str,
        date: str = Query(...),
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        return repository.get_topic_digest_note(topic_id, date)

    @app.put("/api/v1/topics/{topic_id}/digest-note")
    def update_topic_digest_note(
        request: Request,
        topic_id: str,
        date: str = Query(...),
        body: TopicDigestNoteUpdate | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(admin_repo),
    ) -> JSONResponse:
        payload = {"topic_id": topic_id, "date_value": date, "body": (body.body if body else "")}
        status, response = repository.use_idempotency(
            idempotency_key,
            "PUT",
            request.url.path,
            payload,
            lambda: (200, repository.set_topic_digest_note(topic_id, date, payload["body"])),
        )
        return JSONResponse(status_code=status, content=response)

    @app.post("/api/v1/topics", status_code=201)
    def create_topic(
        request: Request,
        body: TopicCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(admin_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (201, repository.create_topic(body).model_dump(mode="json")),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.patch("/api/v1/topics/{topic_id}")
    def patch_topic(
        request: Request,
        topic_id: str,
        patch: TopicPatch,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(admin_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "PATCH",
            request.url.path,
            patch.model_dump(mode="json"),
            lambda: (200, repository.patch_topic(topic_id, patch).model_dump(mode="json")),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.delete("/api/v1/topics/{topic_id}")
    def delete_topic(
        request: Request,
        topic_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(admin_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "DELETE",
            request.url.path,
            {"topic_id": topic_id},
            lambda: (200, repository.delete_topic(topic_id) or {"deleted": topic_id}),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.post("/api/v1/discovery-runs", status_code=202)
    def create_discovery_run(
        request: Request,
        background_tasks: BackgroundTasks,
        body: DiscoveryRunCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (202, repository.create_discovery_run(body, idempotency_key).model_dump(mode="json")),
        )
        job_id = payload.get("job_id")
        if status == 202 and job_id and not repository.last_idempotency_replayed:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job_id)
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/discovery-runs/{run_id}")
    def get_discovery_run(run_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return repository.get_discovery_run(run_id).model_dump(mode="json")

    @app.get("/api/v1/papers")
    def list_papers(
        topic: str | None = None,
        date: str | None = None,
        publication_date: str | None = None,
        status: str | None = None,
        source: str | None = None,
        selected: bool | None = None,
        all: bool | None = Query(default=None, alias="all"),
        limit: int | None = Query(default=None, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        repository: Repository = Depends(repo),
    ) -> list[dict[str, Any]]:
        if all:
            # Full corpus across every date (ordered most-recent first) for the
            # library view and historical search.
            all_limit = limit if limit is not None else 5000
            papers = repository.list_all_papers(topic=topic, status=status, limit=all_limit, offset=offset)
        else:
            papers = repository.list_papers(
                topic=topic,
                date_value=date,
                publication_date_value=publication_date,
                status=status,
                source=source,
                selected=selected,
                limit=limit if limit is not None else 5000,
                offset=offset,
            )
        return [paper.model_dump(mode="json") for paper in repository.get_papers_detail(papers)]

    @app.get("/api/v1/papers/search")
    def search_papers(
        q: str = Query(default="", min_length=0, max_length=200),
        online: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=200),
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        """Search stored papers (all dates), optionally falling back to a live
        online discovery when nothing matches locally.

        - `q`: keyword matched against title / abstract / translated abstract /
          method summary / topic name across the whole library (historical papers
          included, not just the selected day).
        - `online=true`: when the local search returns no matches, run an arXiv
          discovery for the query and return the hits flagged with `remote: true`.
        """
        query = (q or "").strip()
        local = [paper.model_dump(mode="json") for paper in repository.get_papers_detail(repository.search_papers(query, limit=limit))]
        if local:
            return {"query": query, "items": local, "total": len(local), "remote": False, "remote_searched": False}
        if not query or not online:
            return {"query": query, "items": [], "total": 0, "remote": False, "remote_searched": False}
        # Local search found nothing -> try live discovery so the UI can offer
        # results for papers not yet in the library.
        remote: list[dict[str, Any]] = []
        remote_error: str | None = None
        try:
            with database.connect() as conn:
                service = ResearchJobService(conn)
                result = service.discovery_adapter.discover(
                    TopicQuery(
                        topic_id="web-search",
                        display_name="Manual web search",
                        include_terms=tuple(term.strip() for term in query.split() if term.strip()) or (query,),
                        max_results=limit,
                    )
                )
            for hit in result.data.get("papers", []):
                if not isinstance(hit, dict):
                    continue
                remote.append(
                    {
                        "id": "",
                        "paper_id": "",
                        "canonical_title": str(hit.get("title") or ""),
                        "title": str(hit.get("title") or ""),
                        "abstract": str(hit.get("abstract") or ""),
                        "translated_abstract": None,
                        "method_summary": None,
                        "language": "en",
                        "first_publication_date": _iso_date(hit.get("published_at")),
                        "current_version_id": None,
                        "status": "discovered",
                        "selected": False,
                        "metadata": {
                            "authors": hit.get("authors") or [],
                            "categories": hit.get("categories") or [],
                            "landing_url": hit.get("landing_url"),
                            "pdf_url": hit.get("pdf_url"),
                        },
                        "identifiers": [
                            {"type": hit.get("source") or "arxiv", "value": str(hit.get("source_id") or "")}
                        ],
                        "topics": [],
                        "remote": True,
                        "source": hit.get("source") or "arxiv",
                    }
                )
            if result.status != "ok" and not remote:
                remote_error = result.message
        except Exception as exc:  # pragma: no cover - defensive
            remote_error = str(exc)
        return {
            "query": query,
            "items": remote,
            "total": len(remote),
            "remote": True,
            "remote_searched": True,
            "remote_error": remote_error,
        }


    @app.post("/api/v1/papers", status_code=201)
    def create_paper(
        request: Request,
        body: PaperCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (201, repository.create_paper(body).model_dump(mode="json")),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/papers/{paper_id}")
    def get_paper(paper_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return repository.get_paper(paper_id).model_dump(mode="json")

    @app.get("/api/v1/papers/{paper_id}/workspace")
    def get_paper_workspace(
        paper_id: str, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        workspace = repository.get_paper_workspace(paper_id)
        workspace["artifacts"] = [
            public_artifact_payload(item) for item in workspace.get("artifacts", [])
        ]
        return workspace

    @app.get("/api/v1/papers/{paper_id}/versions")
    def list_versions(paper_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return {
            "items": [item.model_dump(mode="json") for item in repository.list_versions(paper_id)]
        }

    @app.post("/api/v1/paper-versions/{version_id}/download", status_code=202)
    def download_version(
        version_id: str,
        background_tasks: BackgroundTasks,
        body: VersionActionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> dict[str, Any]:
        job = repository.version_action(version_id, "download", body, idempotency_key)
        if job.status == "queued" and repository.last_job_created:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job.job_id)
        return public_job_payload(job)

    @app.get("/api/v1/paper-versions/{version_id}/document")
    def read_version_document(
        version_id: str,
        repository: Repository = Depends(repo),
    ) -> Response:
        artifacts = repository.list_version_artifacts(version_id)
        pdf = next(
            (
                artifact
                for artifact in artifacts
                if artifact.artifact_type in {"pdf", "source_pdf"}
                or artifact.media_type == "application/pdf"
            ),
            None,
        )
        if pdf is None:
            raise HTTPException(
                status_code=404,
                detail="PDF is not stored on this server; create a download job first",
            )
        return artifact_file_response(pdf, inline=True)

    @app.post("/api/v1/papers/{paper_id}/select")
    def select_paper(
        request: Request,
        paper_id: str,
        body: PaperSelectRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (200, repository.select_paper(paper_id, body).model_dump(mode="json")),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.post("/api/v1/paper-versions/{version_id}/parse", status_code=202)
    def parse_version(
        version_id: str,
        background_tasks: BackgroundTasks,
        body: VersionActionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> dict[str, Any]:
        job = repository.version_action(version_id, "parse", body, idempotency_key)
        if job.status == "queued" and repository.last_job_created:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job.job_id)
        return public_job_payload(job)

    @app.post("/api/v1/paper-versions/{version_id}/translate", status_code=202)
    def translate_version(
        version_id: str,
        background_tasks: BackgroundTasks,
        body: VersionActionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> dict[str, Any]:
        job = repository.version_action(version_id, "translate", body, idempotency_key)
        if job.status == "queued" and repository.last_job_created:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job.job_id)
        return public_job_payload(job)

    @app.post("/api/v1/paper-versions/{version_id}/analyze", status_code=202)
    def analyze_version(
        version_id: str,
        background_tasks: BackgroundTasks,
        body: VersionActionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> dict[str, Any]:
        job = repository.version_action(version_id, "analyze", body, idempotency_key)
        if job.status == "queued" and repository.last_job_created:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job.job_id)
        return public_job_payload(job)

    @app.get("/api/v1/paper-versions/{version_id}/artifacts")
    def list_version_artifacts(
        version_id: str, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        return {
            "items": [
                public_artifact_payload(item)
                for item in repository.list_version_artifacts(version_id)
            ]
        }

    @app.post("/api/v1/paper-versions/{version_id}/artifacts", status_code=201)
    def create_version_artifact(
        request: Request,
        version_id: str,
        body: ArtifactCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (201, public_artifact_payload(repository.create_artifact_for_version(version_id, body))),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/paper-versions/{version_id}/report")
    def get_version_report(version_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return repository.get_version_report(version_id).model_dump(mode="json")

    @app.get("/api/v1/daily-digests/{date_value}")
    def daily_digest(date_value: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return repository.daily_digest(date_value).model_dump(mode="json")

    @app.get("/api/v1/papers/{paper_id}/relations")
    def list_relations(paper_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return {
            "items": [item.model_dump(mode="json") for item in repository.list_relations(paper_id)]
        }

    @app.get("/api/v1/relations")
    def list_all_relations(repository: Repository = Depends(research_repo)) -> dict[str, Any]:
        return {
            "items": repository.list_all_relations(),
            "total": int(
                repository.conn.execute("SELECT COUNT(*) AS n FROM paper_relation").fetchone()["n"]
            ),
        }

    @app.post("/api/v1/papers/{paper_id}/relations/rebuild")
    def rebuild_paper_relations(
        request: Request,
        paper_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            {},
            lambda: (200, repository.rebuild_relations(paper_id)),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.post("/api/v1/relations/rebuild")
    def rebuild_all_relations(
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(research_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            {},
            lambda: (200, repository.rebuild_relations()),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/jobs")
    def list_jobs(
        status: str | None = None,
        kind: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = Query(default=300, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        jobs = repository.list_jobs(
            status=status, kind=kind, target_type=target_type, target_id=target_id,
            limit=limit, offset=offset,
        )
        total = repository.count_jobs(
            status=status, kind=kind, target_type=target_type, target_id=target_id,
        )
        return {"items": [public_job_payload(job) for job in jobs], "total": total}

    @app.get("/api/v1/jobs/dead-letter")
    def dead_letters(
        limit: int = Query(default=100, ge=1, le=1000),
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        return sanitize_stage_audit(dead_letter_payload(repository.conn, limit=limit))

    @app.post("/api/v1/jobs/dead-letter/{job_id}/replay")
    def replay_dead_letter(
        request: Request,
        job_id: str,
        body: JobRetryRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(admin_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (200, replay_dead_letter_job(repository.conn, job_id, reason=body.reason)),
        )
        return JSONResponse(status_code=status, content=sanitize_stage_audit(payload))

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return public_job_payload(repository.get_job(job_id))

    @app.post("/api/v1/jobs/{job_id}")
    def job_action(
        request: Request,
        background_tasks: BackgroundTasks,
        job_id: str,
        body: JobActionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(jobs_repo),
    ) -> JSONResponse:
        def apply_action() -> tuple[int, dict[str, Any]]:
            if body.action == "retry":
                return 200, public_job_payload(
                    repository.retry_job(job_id, JobRetryRequest(reason=body.reason))
                )
            if body.action == "cancel":
                return 200, public_job_payload(
                    repository.cancel_job(job_id, JobCancelRequest(reason=body.reason))
                )
            raise ConflictError("Unsupported job action; use retry or cancel")

        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            apply_action,
        )
        if (
            body.action == "retry"
            and payload.get("status") == "queued"
            and not repository.last_idempotency_replayed
        ):
            repository.conn.commit()
            background_tasks.add_task(execute_job, job_id)
        return JSONResponse(status_code=status, content=payload)

    @app.post("/api/v1/jobs/{job_id}/retry")
    def retry_job(
        request: Request,
        background_tasks: BackgroundTasks,
        job_id: str,
        body: JobRetryRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(jobs_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (200, public_job_payload(repository.retry_job(job_id, body))),
        )
        if payload.get("status") == "queued" and not repository.last_idempotency_replayed:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job_id)
        return JSONResponse(status_code=status, content=payload)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(
        request: Request,
        job_id: str,
        body: JobCancelRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(jobs_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (200, public_job_payload(repository.cancel_job(job_id, body))),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/artifacts")
    def list_artifacts(
        paper_version_id: str | None = None,
        patent_draft_id: str | None = None,
        artifact_type: str | None = None,
        limit: int = Query(default=300, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        items = repository.list_artifacts(
            paper_version_id=paper_version_id,
            patent_draft_id=patent_draft_id,
            artifact_type=artifact_type,
            limit=limit,
            offset=offset,
        )
        total = repository.count_artifacts(
            paper_version_id=paper_version_id,
            patent_draft_id=patent_draft_id,
            artifact_type=artifact_type,
        )
        return {
            "items": [public_artifact_payload(item) for item in items],
            "total": total,
        }

    def artifact_file_response(artifact: Any, *, inline: bool) -> Response:
        uri = artifact.uri
        if uri.startswith("inline://"):
            content = str(artifact.metadata.get("content", ""))
            return Response(content=content, media_type=artifact.media_type)
        if uri.startswith("http://") or uri.startswith("https://"):
            raise HTTPException(status_code=403, detail="Remote artifact redirects are not supported")
        path = Path(uri.removeprefix("file://")).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not any(path == root or root in path.parents for root in downloadable_roots):
            raise HTTPException(status_code=403, detail="Artifact path is outside approved storage roots")
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file is not available on this host")
        return FileResponse(
            path,
            media_type=artifact.media_type,
            filename=path.name,
            content_disposition_type="inline" if inline else "attachment",
        )

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    def download_artifact(
        artifact_id: str,
        download: bool = Query(default=False),
        repository: Repository = Depends(repo),
    ) -> Response:
        artifact = repository.get_artifact(artifact_id)
        inline = not download and (
            artifact.media_type == "application/pdf"
            or artifact.media_type.startswith(("text/", "image/"))
        )
        return artifact_file_response(artifact, inline=inline)

    @app.post("/api/v1/invention-candidates", status_code=201)
    def create_invention_candidate(
        request: Request,
        body: InventionCandidateCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(patent_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (201, repository.create_invention_candidate(body).model_dump(mode="json")),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/invention-candidates")
    def list_invention_candidates(
        status: str | None = None, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in repository.list_invention_candidates(status=status)
            ]
        }

    @app.get("/api/v1/invention-candidates/{candidate_id}")
    def get_invention_candidate(
        candidate_id: str, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        return repository.get_invention_candidate(candidate_id).model_dump(mode="json")

    @app.get("/api/v1/invention-candidates/{candidate_id}/stages")
    def invention_candidate_stages(
        candidate_id: str, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        repository.get_invention_candidate(candidate_id)
        return {
            "items": [
                public_patent_stage_payload(item)
                for item in repository.list_patent_stage_runs(candidate_id)
            ]
        }

    @app.post("/api/v1/invention-candidates/{candidate_id}/prior-art-check", status_code=202)
    def prior_art_check(
        candidate_id: str,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(patent_repo),
    ) -> dict[str, Any]:
        job = repository.candidate_job(
            candidate_id, "prior_art_check", {}, idempotency_key
        )
        if job.status == "queued" and repository.last_job_created:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job.job_id)
        return public_job_payload(job)

    @app.post("/api/v1/invention-candidates/{candidate_id}/approve")
    def approve_candidate(
        request: Request,
        candidate_id: str,
        body: CandidateApproveRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(patent_repo),
    ) -> JSONResponse:
        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            lambda: (200, repository.approve_candidate(candidate_id, body).model_dump(mode="json")),
        )
        return JSONResponse(status_code=status, content=payload)

    @app.post("/api/v1/invention-candidates/{candidate_id}/draft", status_code=202)
    def create_draft(
        request: Request,
        candidate_id: str,
        body: DraftCreateRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(patent_repo),
    ) -> JSONResponse:
        def create() -> tuple[int, dict[str, Any]]:
            output = PatentOutputService(
                repository.conn,
                output_root=export_root / "patent_drafts",
            ).generate_outputs(
                candidate_id,
                case_name=body.case_name,
                protection_focus=body.protection_focus,
                notes=body.notes,
            )
            job = repository.create_job(
                "patent_draft",
                "invention_candidate",
                candidate_id,
                body.model_dump(mode="json"),
                idempotency_key,
            )
            result = {
                "draft_id": output.draft.id,
                "version_label": output.version_label,
                "artifacts": {
                    "markdown": sanitize_artifact_metadata(output.artifacts.markdown_artifact),
                    "docx": sanitize_artifact_metadata(output.artifacts.docx_artifact),
                },
            }
            repository.conn.execute(
                """
                UPDATE job
                SET status = 'succeeded', result_json = ?, error_json = '{}',
                    next_poll_after = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (dumps(result), job.job_id),
            )
            saved_job = repository.get_job(job.job_id)
            return 202, {
                "job": public_job_payload(saved_job),
                "draft": repository.get_patent_draft(output.draft.id).model_dump(mode="json"),
                "artifacts": {
                    "items": [
                        public_artifact_payload(item)
                        for item in repository.list_draft_artifacts(output.draft.id)
                    ]
                },
            }

        status, payload = repository.use_idempotency(
            idempotency_key,
            "POST",
            request.url.path,
            body.model_dump(mode="json"),
            create,
        )
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/v1/patent-drafts")
    def list_patent_drafts(
        candidate_id: str | None = None,
        repository: Repository = Depends(repo),
    ) -> dict[str, Any]:
        return {
            "items": [
                item.model_dump(mode="json")
                for item in repository.list_patent_drafts(candidate_id)
            ]
        }

    @app.get("/api/v1/patent-drafts/{draft_id}")
    def get_patent_draft(draft_id: str, repository: Repository = Depends(repo)) -> dict[str, Any]:
        return repository.get_patent_draft(draft_id).model_dump(mode="json")

    @app.get("/api/v1/patent-drafts/{draft_id}/versions")
    def patent_draft_versions(
        draft_id: str, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        return {
            "items": [
                item.model_dump(mode="json") for item in repository.draft_versions(draft_id)
            ]
        }

    @app.get("/api/v1/patent-drafts/{draft_id}/export")
    def patent_draft_export(
        draft_id: str,
        format: str = Query(default="markdown"),
        repository: Repository = Depends(repo),
    ) -> Response:
        export = repository.draft_export(draft_id, format)
        if export.get("path"):
            path = Path(export["path"]).expanduser().resolve()
            if not (path == export_root or export_root in path.parents):
                raise HTTPException(status_code=403, detail="Patent export is outside approved storage")
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Patent export file is not available")
            return FileResponse(
                path,
                media_type=export["content_type"],
                filename=export["filename"],
            )
        return Response(
            content=export["content"],
            media_type=export["content_type"],
            headers={"Content-Disposition": "attachment"},
        )

    @app.get("/api/v1/patent-drafts/{draft_id}/artifacts")
    def patent_draft_artifacts(
        draft_id: str, repository: Repository = Depends(repo)
    ) -> dict[str, Any]:
        return {
            "items": [
                public_artifact_payload(item) for item in repository.list_draft_artifacts(draft_id)
            ]
        }

    @app.post("/api/v1/patent-drafts/{draft_id}/revise", status_code=202)
    def revise_draft(
        draft_id: str,
        background_tasks: BackgroundTasks,
        body: PatentDraftReviseRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        repository: Repository = Depends(patent_repo),
    ) -> dict[str, Any]:
        job = repository.revise_draft(draft_id, body, idempotency_key)
        if job.status == "queued" and repository.last_job_created:
            repository.conn.commit()
            background_tasks.add_task(execute_job, job.job_id)
        return public_job_payload(job)

    @app.get("/", include_in_schema=False)
    def web_index() -> Response:
        index = settings.static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Web frontend is not installed")
        return FileResponse(index)

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def web_spa(frontend_path: str) -> Response:
        if frontend_path.startswith("api/") or frontend_path == "health":
            raise HTTPException(status_code=404, detail="Not found")
        index = settings.static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Web frontend is not installed")
        return FileResponse(index)

    return app


def _iso_date(value: Any) -> str | None:
    """Best-effort conversion of an ISO timestamp/date to a YYYY-MM-DD string."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        candidate = str(value)[:10]
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            return None
        return candidate
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat()


app = create_app()
