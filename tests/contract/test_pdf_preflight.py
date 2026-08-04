from __future__ import annotations

from pathlib import Path

from research_hub.adapters.pdf_preflight import preflight_markdown_for_pdf
from research_hub.adapters.render_pdf import MarkdownPdfRenderAdapter


def test_pdf_preflight_reports_missing_images_formula_markers_and_table_overflow() -> None:
    header = "|" + "|".join(f"c{index}" for index in range(13)) + "|"
    separator = "|" + "|".join("---" for _ in range(13)) + "|"
    row = "|" + "|".join("value" for _ in range(13)) + "|"
    markdown = "\n".join(
        [
            "# Report",
            "![figure](missing.png)",
            "The equation marker [[FORMULA]] was not rendered.",
            header,
            separator,
            row,
        ]
    )

    issues = preflight_markdown_for_pdf(markdown)

    assert {issue["kind"] for issue in issues} == {
        "missing_image",
        "formula_render_marker",
        "table_overflow",
    }


def test_pdf_renderer_stops_before_external_render_on_preflight_failure(monkeypatch, tmp_path: Path) -> None:
    def fail_run(*_args, **_kwargs):
        raise AssertionError("Pandoc must not be invoked after preflight failure")

    monkeypatch.setattr("research_hub.adapters.render_pdf.subprocess.run", fail_run)

    result = MarkdownPdfRenderAdapter().render("![figure](missing.png)", tmp_path / "out.pdf")

    assert result.status == "failed"
    assert result.message == "PDF renderer preflight failed"
    assert result.data["issues"][0]["kind"] == "missing_image"
