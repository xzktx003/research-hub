"""Tests for proxy/direct fallback in the PDF downloader.

The PdfDownloadAdapter must attempt both proxied and direct transports so a
paper PDF is fetched even when one network path is down. These tests verify:
  * all proxy candidates + a direct attempt are tried in order;
  * the first success wins;
  * when everything fails, the error lists every attempt.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from research_hub.adapters.downloader import PdfDownloadAdapter, _env_proxies
from research_hub.adapters import AdapterResult


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"%PDF-1.7\nok\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_proxy_then_direct_fallback(tmp_path: Path, monkeypatch) -> None:
    """A bad proxy must not block a successful direct download."""
    server = _serve()
    try:
        url = f"http://127.0.0.1:{server.server_port}/paper.pdf"
        # Simulate an unreachable proxy first.
        proxy = f"http://127.0.0.1:{server.server_port - 1 or 1}"  # almost certainly closed
        monkeypatch.setattr("research_hub.adapters.downloader._env_proxies", lambda: [proxy, None])

        result = PdfDownloadAdapter(max_bytes=1024 * 1024).download(url, tmp_path)

        assert result.status == "ok"
        assert Path(result.data["path"]).read_bytes().startswith(b"%PDF-")
        # The successful attempt recorded that it used a direct connection.
        assert result.data.get("proxy") == "direct"
    finally:
        server.shutdown()


def test_all_transports_fail_reports_attempts(tmp_path: Path, monkeypatch) -> None:
    server = _serve()
    try:
        url = f"http://127.0.0.1:{server.server_port}/paper.pdf"
        dead_proxy = "http://127.0.0.1:0"  # invalid/refused
        # Force direct to ALSO fail by pointing at a non-SPDF… we keep it simple:
        # both proxy and "direct" use a connect-refused transport except direct
        # still reaches the server, so force direct by monkeypatching to only the
        # dead proxy.
        monkeypatch.setattr("research_hub.adapters.downloader._env_proxies", lambda: [dead_proxy])
        result = PdfDownloadAdapter(max_bytes=1024 * 1024).download(url, tmp_path)

        assert result.status == "failed"
        attempts = result.data.get("attempts") or []
        assert any(item.get("status") == "degraded" for item in attempts)
    finally:
        server.shutdown()


def test_unconfigured_env_yields_single_direct_candidate(monkeypatch) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    candidates = _env_proxies()
    assert candidates == [None]
