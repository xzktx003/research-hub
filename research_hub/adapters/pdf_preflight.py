"""Local markdown preflight checks before PDF rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PdfPreflightIssue:
    kind: str
    message: str
    detail: dict[str, Any]


_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
_FORMULA_MARKER_PATTERN = re.compile(
    r"(\[\[\s*formula\s*\]\]|\{\{\s*formula\s*\}\}|<\s*formula\s*>|FORMULA_RENDER_(?:FAILED|MISSING)|\[\s*formula\s*\])",
    re.IGNORECASE,
)
_MATH_PLACEHOLDER_PATTERN = re.compile(
    r"(\[\[\s*math\s*\]\]|\{\{\s*math\s*\}\}|<\s*math\s*>|MATH_RENDER_(?:FAILED|MISSING)|\[\s*math\s*\])",
    re.IGNORECASE,
)
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")


def preflight_markdown_for_pdf(
    markdown: str,
    *,
    asset_root: Path | None = None,
    max_table_columns: int = 12,
    max_table_row_chars: int = 1800,
) -> list[dict[str, Any]]:
    issues: list[PdfPreflightIssue] = []
    issues.extend(_missing_image_issues(markdown, asset_root))
    issues.extend(_formula_marker_issues(markdown))
    issues.extend(_table_overflow_issues(markdown, max_table_columns, max_table_row_chars))
    return [asdict(issue) for issue in issues]


def _missing_image_issues(markdown: str, asset_root: Path | None) -> list[PdfPreflightIssue]:
    issues: list[PdfPreflightIssue] = []
    for match in _IMAGE_PATTERN.finditer(markdown):
        alt = match.group("alt").strip()
        src = match.group("src").strip().strip('"\'')
        if not src:
            issues.append(
                PdfPreflightIssue(
                    kind="missing_image",
                    message="Markdown image reference is missing a source path",
                    detail={"alt": alt},
                )
            )
            continue
        if src.startswith(("http://", "https://", "data:", "inline://")):
            continue
        candidate = Path(src)
        if asset_root and not candidate.is_absolute():
            candidate = asset_root / candidate
        if candidate.exists():
            continue
        issues.append(
            PdfPreflightIssue(
                kind="missing_image",
                message="Markdown image reference does not resolve to a readable file",
                detail={"alt": alt, "src": src, "resolved_path": str(candidate)},
            )
        )
    return issues


def _formula_marker_issues(markdown: str) -> list[PdfPreflightIssue]:
    markers = []
    for pattern in (_FORMULA_MARKER_PATTERN, _MATH_PLACEHOLDER_PATTERN):
        markers.extend(pattern.finditer(markdown))
    issues: list[PdfPreflightIssue] = []
    for match in markers:
        snippet = markdown[max(0, match.start() - 40) : min(len(markdown), match.end() + 40)]
        issues.append(
            PdfPreflightIssue(
                kind="formula_render_marker",
                message="Markdown still contains an unresolved formula rendering marker",
                detail={"marker": match.group(0), "snippet": snippet},
            )
        )
    return issues


def _table_overflow_issues(
    markdown: str,
    max_table_columns: int,
    max_table_row_chars: int,
) -> list[PdfPreflightIssue]:
    issues: list[PdfPreflightIssue] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        if "|" not in lines[index]:
            index += 1
            continue
        block_start = index
        block: list[str] = []
        while index < len(lines) and ("|" in lines[index] or not lines[index].strip()):
            if lines[index].strip():
                block.append(lines[index])
            index += 1
        if len(block) < 2 or not any(_TABLE_SEPARATOR_PATTERN.match(line) for line in block):
            continue
        columns = max((_markdown_table_columns(line) for line in block if "|" in line), default=0)
        block_width = max((len(line) for line in block), default=0)
        if columns > max_table_columns:
            issues.append(
                PdfPreflightIssue(
                    kind="table_overflow",
                    message="Markdown table has too many columns for reliable PDF rendering",
                    detail={
                        "columns": columns,
                        "max_columns": max_table_columns,
                        "line": block[0],
                        "block_start_line": block_start + 1,
                    },
                )
            )
        if block_width > max_table_row_chars:
            issues.append(
                PdfPreflightIssue(
                    kind="table_overflow",
                    message="Markdown table row is too wide for reliable PDF rendering",
                    detail={
                        "row_chars": block_width,
                        "max_row_chars": max_table_row_chars,
                        "line": max(block, key=len),
                        "block_start_line": block_start + 1,
                    },
                )
            )
    return issues


def _markdown_table_columns(line: str) -> int:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return len([cell for cell in cells if cell or len(cells) == 1])
