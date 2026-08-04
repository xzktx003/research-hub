"""Constrained HTTP(S) PDF downloader for paper source artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .retry import RetryConfig, run_with_retry
from .types import AdapterResult


class _RetryableDownloadError(Exception):
    """Internal sentinel: a download attempt degraded at the network layer.

    Raised when ``_attempt_download`` returns an ``AdapterResult`` with status
    ``degraded`` so the shared retry helper can back off and try again, while
    deterministic failures (bad scheme / content-type / magic bytes / size) are
    returned directly and never retried.
    """

    def __init__(self, result: AdapterResult) -> None:
        super().__init__(result.message or "download degraded")
        self.result = result


def _env_proxies() -> list[str | None]:
    """Build ordered proxy candidates from the environment.

    Returns a list where ``None`` means "no proxy / direct connection". We try
    the more specific HTTPS proxy first, then HTTP, then SOCKS (ALL_PROXY), and
    finally a direct (proxy-free) connection so both "with proxy" and "without
    proxy" are attempted.
    """
    candidates: list[str | None] = []
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.getenv(key)
        if value:
            candidates.append(value)
    if not candidates:
        return [None]
    # Put a direct attempt first when proxies are present so a working direct
    # connection avoids proxy latency; proxies are still tried as fallbacks.
    candidates.append(None)
    # De-duplicate while preserving order.
    seen: set[str | None] = set()
    unique: list[str | None] = []
    for candidate in candidates:
        key = candidate
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


class PdfDownloadAdapter:
    """Download PDFs with scheme, size, content-type, and magic-byte checks.

    Multiple transport strategies are attempted (proxied and direct) so a PDF
    is fetched even when one network path fails.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 120.0,
        max_bytes: int | None = None,
        user_agent: str = "ResearchHub/0.1 PDF downloader",
        proxies: list[str | None] | None = None,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes or int(os.getenv("RESEARCH_HUB_MAX_PDF_BYTES", str(80 * 1024 * 1024)))
        self.user_agent = user_agent
        self.proxies = proxies if proxies is not None else _env_proxies()
        self.retry_config = retry_config or RetryConfig(max_attempts=3, base_delay=1.0)

    def _client(self, proxy: str | None) -> httpx.Client:
        if proxy:
            return httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, proxy=proxy)
        # trust_env=False ignores any ambient HTTP(S)_PROXY so this is a true
        # direct connection even when the process inherited proxy env vars.
        return httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, trust_env=False)

    def _attempt_download_or_raise(self, url: str, artifact_root: Path, proxy: str | None) -> AdapterResult:
        """Adapt an ``_attempt_download`` result for the retry loop.

        Degraded (network-level) results are raised as
        :class:`_RetryableDownloadError` so the shared backoff can retry them;
        ok/failed results are returned untouched.
        """
        result = self._attempt_download(url, artifact_root, proxy)
        if result.status == "degraded":
            raise _RetryableDownloadError(result)
        return result

    def _attempt_download(self, url: str, artifact_root: Path, proxy: str | None) -> AdapterResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return AdapterResult.failed("Only HTTP(S) PDF downloads are allowed", url=url)

        root = Path(artifact_root).expanduser().resolve()
        temp = root / "tmp" / f"pdf-download-{os.getpid()}-{hashlib.sha256((proxy or 'direct').encode()).hexdigest()[:8]}.part"
        temp.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        content_type = ""
        completed = False
        try:
            with self._client(proxy) as client:
                with client.stream("GET", url, headers={"User-Agent": self.user_agent}) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if "pdf" not in content_type:
                        return AdapterResult.failed(
                            "Downloaded response is not advertised as a PDF",
                            url=url,
                            content_type=content_type,
                        )
                    length = response.headers.get("Content-Length")
                    if length and int(length) > self.max_bytes:
                        return AdapterResult.failed(
                            "PDF exceeds configured size limit",
                            url=url,
                            content_length=int(length),
                            max_bytes=self.max_bytes,
                        )
                    with temp.open("wb") as handle:
                        first = True
                        for chunk in response.iter_bytes(1024 * 1024):
                            if not chunk:
                                continue
                            if first:
                                first = False
                                if not chunk.startswith(b"%PDF-"):
                                    return AdapterResult.failed(
                                        "Downloaded body does not start with %PDF-",
                                        url=url,
                                        content_type=content_type,
                                    )
                            size += len(chunk)
                            if size > self.max_bytes:
                                return AdapterResult.failed(
                                    "PDF exceeds configured size limit",
                                    url=url,
                                    size_bytes=size,
                                    max_bytes=self.max_bytes,
                                )
                            digest.update(chunk)
                            handle.write(chunk)
            if size == 0:
                return AdapterResult.failed("Downloaded PDF body is empty", url=url, content_type=content_type)
            completed = True
        except Exception as exc:
            return AdapterResult.degraded(
                f"PDF download unavailable{(' (proxy: ' + proxy + ')') if proxy else ' (direct)'}: {exc}",
                url=url,
                proxy=proxy or "direct",
            )
        finally:
            if temp.exists() and not completed:
                temp.unlink(missing_ok=True)
        if completed:
            sha256 = digest.hexdigest()
            target = root / "pdf" / sha256[:2] / f"{sha256}.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temp.replace(target)
            else:
                temp.unlink(missing_ok=True)
            return AdapterResult.ok(
                "PDF downloaded",
                url=url,
                path=str(target),
                sha256=sha256,
                size_bytes=size,
                content_type=content_type,
                proxy=proxy or "direct",
            )
        return AdapterResult.failed("PDF download failed", url=url)

    def download(self, url: str, artifact_root: Path | str) -> AdapterResult:
        """Try proxied and direct transports in order until one succeeds.

        Deterministic validation failures (bad scheme, wrong content type,
        non-PDF body, size limits) return immediately since retrying through
        another proxy cannot change them. Only network-level degradation
        triggers the next transport attempt, so both "with proxy" and "without
        proxy" paths are exercised when the network is flaky.
        """
        root = Path(artifact_root).expanduser().resolve()
        failures: list[dict[str, str]] = []
        for proxy in self.proxies:
            result = self._attempt_with_retry(url, root, proxy)
            if result.status == "ok":
                return result
            if result.status == "failed":
                # Deterministic validation failure; another transport won't help.
                return result
            failures.append({"proxy": proxy or "direct", "status": result.status, "message": result.message})
        # All transports degraded: surface the most useful detail.
        return AdapterResult.failed(
            f"PDF download failed across {len(failures)} transport(s)",
            url=url,
            attempts=failures,
        )

    def _attempt_with_retry(self, url: str, root: Path, proxy: str | None) -> AdapterResult:
        """Run a single transport's download with exponential backoff on the
        network-level (degraded) failures. Deterministic failures return
        immediately without retrying.
        """
        try:
            return run_with_retry(
                lambda: self._attempt_download_or_raise(url, root, proxy),
                config=self.retry_config,
            )
        except _RetryableDownloadError as exc:
            return exc.result
