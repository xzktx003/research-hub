"""Markdown-to-PDF rendering adapter with explicit degraded states."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .pdf_preflight import preflight_markdown_for_pdf
from .types import AdapterResult


class MarkdownPdfRenderAdapter:
    """Render Markdown to PDF through an existing local lightweight toolchain."""

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds

    def render(self, markdown: str, output_path: Path | str) -> AdapterResult:
        output = Path(output_path).expanduser().resolve()
        issues = preflight_markdown_for_pdf(markdown, asset_root=output.parent)
        if issues:
            return AdapterResult.failed(
                "PDF renderer preflight failed",
                issues=issues,
            )
        pandoc = shutil.which("pandoc")
        pdf_engine = shutil.which("xelatex") or shutil.which("lualatex") or shutil.which("pdflatex")
        if not pandoc:
            return AdapterResult.degraded("PDF renderer is not configured: pandoc is not available")
        if not pdf_engine:
            return AdapterResult.degraded(
                "PDF renderer is not configured: no LaTeX PDF engine is available",
                checked_engines=["xelatex", "lualatex", "pdflatex"],
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        source = output.with_suffix(".md")
        source.write_text(markdown, encoding="utf-8")
        command = [
            pandoc,
            str(source),
            "-o",
            str(output),
            "--pdf-engine",
            pdf_engine,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            return AdapterResult.degraded(f"PDF renderer unavailable: {exc}", output_path=str(output))
        if completed.returncode != 0:
            return AdapterResult.failed(
                "PDF renderer failed",
                output_path=str(output),
                stderr=completed.stderr[-2000:],
            )
        if not output.is_file() or not output.read_bytes().startswith(b"%PDF-"):
            return AdapterResult.failed("PDF renderer did not produce a valid PDF", output_path=str(output))
        return AdapterResult.ok("PDF rendered", path=str(output), size_bytes=output.stat().st_size)
