"""Adapters for the official MinerU async API/router and legacy WebApp."""

from __future__ import annotations

import os
import mimetypes
import re
from pathlib import Path
from pathlib import PurePosixPath
import zipfile
from typing import Any

import httpx

from .types import AdapterResult, MinerUJobRequest

# Transient connection failures (network down / service not listening yet) are
# worth retrying against an auto-discovered endpoint before giving up.
_CONNECTION_ERROR_TYPES = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
)

# Healthy MinerU workers expose the same async contract as the router on any port.
_COMMON_MINERU_PORTS = (
    8000,
    8100,
    8200,
    8300,
    8400,
    8500,
    8600,
    8700,
    8800,
    8900,
    9000,
    8001,
)


def _discover_mineru_ports() -> list[int]:
    """Scan ``/proc`` for live ``mineru.cli.fast_api --port <N>`` processes.

    The deployed MinerU service often binds to an ephemeral/random port (e.g. a
    worker spawned by ``mineru-router``), so the configured ``MINERU_BASE_URL``
    can go stale after a restart. Discovering real listening ports keeps the
    product resilient to those restarts without manual reconfiguration.
    """
    ports: list[int] = []
    try:
        proc_root = Path("/proc")
        for proc in proc_root.iterdir():
            if not proc.name.isdigit():
                continue
            cmdline = proc / "cmdline"
            try:
                args = cmdline.read_bytes().split(b"\x00")
            except OSError:
                continue
            text = b" ".join(arg for arg in args if arg).decode(errors="ignore")
            if "mineru" not in text and "fast_api" not in text and "/mineru" not in text:
                continue
            match = re.search(r"--port\s+(\d+)|--port=(\d+)", text)
            if match:
                ports.append(int(match.group(1) or match.group(2)))
    except OSError:
        # /proc not available (e.g. non-Linux) - fall through to common ports.
        pass
    return sorted(set(ports))


def _is_localhost_url(url: str) -> bool:
    """Whether ``url`` points at this machine (127.0.0.1 / localhost).

    Port auto-discovery only makes sense for local deployments where the MinerU
    router/workers bind to ephemeral ports. Remote URLs are used as-is with no
    local-port probing (probing would be both wrong and slow for remote hosts).
    """
    host = (url or "").split("://")[-1].split("/")[0].split(":")[0].lower()
    return host in {"127.0.0.1", "localhost", "::1", ""}


def discover_mineru_candidates(base_url: str | None) -> list[str]:
    """Return a de-duplicated list of candidate MinerU endpoints.

    The configured ``base_url`` is always first (it is the explicit source of
    truth). For local deployments it is followed by ports discovered from live
    ``mineru.cli.fast_api`` processes, then a small set of common ports as
    last-resort probes. Remote URLs are returned alone - probing random local
    ports makes no sense when the service lives on another host.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        normalized = url.strip().rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    configured = (base_url or os.getenv("MINERU_BASE_URL") or "").strip().rstrip("/")
    _add(configured)

    if _is_localhost_url(configured):
        for port in _discover_mineru_ports():
            _add(f"http://127.0.0.1:{port}")

        for port in _COMMON_MINERU_PORTS:
            _add(f"http://127.0.0.1:{port}")

    return candidates


def _is_transient_connection_error(exc: Exception) -> bool:
    if isinstance(exc, _CONNECTION_ERROR_TYPES):
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("connection refused", "errno 111", "connect error", "connection aborted")
    )


class MinerUApiAdapter:
    """Use MinerU's official ``POST /tasks`` asynchronous contract.

    Both ``mineru-api`` and ``mineru-router`` expose the same task/status/result
    endpoints. Resource selection stays server-side; product callers never
    choose a physical GPU id.

    Transport resilience: if the configured ``base_url`` is unreachable, the
    adapter automatically discovers live MinerU instances on this host and
    retries against one of them. This keeps PDF parsing working across service
    restarts that use ephemeral ports.
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

    def _resolve_base_url(self) -> tuple[str | None, dict[str, Any]]:
        """Pick the first reachable endpoint among configured + discovered candidates.

        Returns ``(endpoint, meta)`` where ``meta`` records which candidates were
        probed and whether the configured URL itself was used. When nothing is
        reachable ``(None, meta)`` is returned so callers can produce a friendly,
        actionable error message (including the discovered ports).
        """
        candidates = discover_mineru_candidates(self.base_url)
        meta: dict[str, Any] = {
            "configured_url": self.base_url,
            "candidates": candidates,
            "probed": [],
            "discovered": candidates[1:] if candidates else [],
        }
        if not candidates:
            return None, meta
        for url in candidates:
            try:
                self._probe_health(url)
                meta["probed"].append(url)
                if url != self.base_url:
                    meta["resolved_url"] = url
                    meta["used_discovered"] = True
                return url, meta
            except Exception as exc:  # noqa: BLE001 - probe failures are expected
                meta["probed"].append(url)
                meta["last_error"] = f"{url}: {exc}"
        return None, meta

    def _probe_health(self, url: str) -> dict[str, Any]:
        with httpx.Client(timeout=min(self.timeout_seconds, 5.0), follow_redirects=True) as client:
            response = client.get(f"{url}/health", headers=self._headers())
        response.raise_for_status()
        return response.json()

    def health(self) -> AdapterResult:
        url, meta = self._resolve_base_url()
        if url is None:
            return AdapterResult.degraded(
                self._friendly_unreachable_message(meta),
                response={"meta": meta},
            )
        try:
            data = self._get_data(f"{url}/health")
        except Exception as exc:
            return AdapterResult.degraded(f"MinerU health check unavailable: {exc}", response={"meta": meta})
        extra = {"meta": meta} if meta.get("used_discovered") else {}
        return AdapterResult.ok(
            "MinerU API/router health check succeeded",
            response={**extra, **data},
        )

    def submit(self, request: MinerUJobRequest) -> AdapterResult:
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

        candidates = discover_mineru_candidates(self.base_url)
        if not candidates:
            return AdapterResult.degraded(
                "MinerU is not configured; set MINERU_BASE_URL",
                artifact_id=request.artifact_id,
            )

        failures: list[str] = []
        for url in candidates:
            try:
                mime_type = mimetypes.guess_type(pdf_path.name)[0] or "application/pdf"
                with pdf_path.open("rb") as handle, httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = client.post(
                        f"{url}/tasks",
                        headers=self._headers(),
                        data=form,
                        files={"files": (pdf_path.name, handle, mime_type)},
                    )
                response.raise_for_status()
                data = response.json()
                task_id = data.get("task_id") if isinstance(data, dict) else None
                if not isinstance(task_id, str) or not task_id:
                    return AdapterResult.failed(
                        "MinerU returned an invalid task payload",
                        response=data,
                        artifact_id=request.artifact_id,
                    )
                extra = {"used_discovered": True} if url != self.base_url else {}
                return AdapterResult.ok(
                    "MinerU task submitted",
                    response={**extra, **data},
                    task_id=task_id,
                    artifact_id=request.artifact_id,
                )
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                if _is_transient_connection_error(exc) and url != self.base_url:
                    continue
                if isinstance(exc, (httpx.HTTPStatusError,)) and url != self.base_url and exc.response.status_code < 500:
                    continue

        # Every candidate failed - report a friendly, actionable message.
        return AdapterResult.degraded(
            self._friendly_submit_message(failures),
            artifact_id=request.artifact_id,
        )

    def _friendly_unreachable_message(self, meta: dict[str, Any]) -> str:
        discovered = [url for url in meta.get("probed", []) if url]
        configured = meta.get("configured_url") or ""
        if discovered:
            return (
                f"MinerU 服务不可达：已探测 {len(discovered)} 个地址均无响应（含自动发现的端口）。"
                f"请确认 MinerU 服务已启动：{', '.join(discovered)}"
            )
        if configured:
            return (
                f"MinerU 服务不可达（{configured}）。"
                "请确认 MinerU 解析服务已启动并监听配置的端口，然后在设置页检查服务地址。"
            )
        return "MinerU 未配置服务地址，请在设置页填写 MINERU_BASE_URL。"

    def _friendly_submit_message(self, failures: list[str]) -> str:
        last = failures[-1] if failures else ""
        refused = [item for item in failures if "refused" in item.lower() or "errno 111" in item.lower()]
        if refused:
            return (
                f"MinerU 任务提交连接被拒绝（{len(refused)} 个地址无法连接）。"
                "MinerU 解析服务可能未启动或端口已变化，系统已自动尝试发现可用实例。"
                "请检查 MinerU 服务是否运行，并在健康面板确认连接状态。"
            )
        if last:
            return f"MinerU 任务提交失败：{last}。请检查 MinerU 服务状态。"
        return "MinerU 任务提交失败：服务不可达，请稍后重试。"

    def status(self, job_id: str) -> AdapterResult:
        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL", job_id=job_id)
        candidates = discover_mineru_candidates(self.base_url)
        failures: list[str] = []
        for url in candidates:
            try:
                data = self._get_data(f"{url}/tasks/{job_id}")
                return AdapterResult.ok("MinerU task status fetched", response=data)
            except httpx.HTTPStatusError as exc:
                # 404 means the task is unknown on this instance. When multiple
                # worker instances exist the task may live on another worker,
                # so keep trying the remaining candidates before failing.
                if exc.response.status_code == 404 and len(candidates) > 1:
                    failures.append(f"{url}: 404")
                    continue
                return AdapterResult.degraded(
                    f"MinerU task status unavailable: {exc}",
                    job_id=job_id,
                    http_status=exc.response.status_code,
                )
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                continue
        return AdapterResult.degraded(
            f"MinerU task status unavailable: {failures[-1] if failures else 'no endpoint reachable'}",
            job_id=job_id,
        )

    def _get_data(self, url: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def fetch_result(self, job_id: str, output_dir: Path | str) -> AdapterResult:
        """Download the result ZIP, extract safely, and return a file manifest."""

        if not self.base_url:
            return AdapterResult.degraded("MinerU is not configured; set MINERU_BASE_URL", job_id=job_id)
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        archive = destination / "mineru-result.zip"
        candidates = discover_mineru_candidates(self.base_url)
        errors: list[str] = []
        for url in candidates:
            try:
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    response = client.get(
                        f"{url}/tasks/{job_id}/result",
                        headers=self._headers(),
                    )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "zip" not in content_type and not response.content.startswith(b"PK"):
                    errors.append(f"{url}: not a ZIP ({content_type})")
                    continue
                archive.write_bytes(response.content)
                self._safe_extract(archive, destination)
                archive.unlink(missing_ok=True)
                break
            except httpx.HTTPStatusError as exc:
                errors.append(f"{url}: HTTP {exc.response.status_code}")
                continue
            except Exception as exc:
                errors.append(f"{url}: {exc}")
                continue
        else:
            archive.unlink(missing_ok=True)
            detail = errors[-1] if errors else "no endpoint reachable"
            refused = any("refused" in item.lower() or "errno 111" in item.lower() for item in errors)
            if refused:
                return AdapterResult.degraded(
                    "MinerU 结果下载连接被拒绝，解析服务可能已重启；请到健康面板确认 MinerU 服务状态。",
                    job_id=job_id,
                )
            return AdapterResult.degraded(
                f"MinerU result download unavailable: {detail}",
                job_id=job_id,
            )

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
