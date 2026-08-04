"""Structural section extraction from parsed Markdown.

A lightweight, dependency-free splitter that turns a parsed paper's Markdown
(body text) into a list of ``{heading, level, content}`` blocks keyed on
Markdown ATX headings (``#``..``######``). The frontend reader can reuse
these anchors for in-document navigation, and downstream summarisers can
operate per-section instead of on one unbounded blob.
"""

from __future__ import annotations

import re
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

# Headings that conventionally open a paper but carry no navigable content
# (front matter / back matter), which we fold into the preamble rather than
# emitting as standalone sections.
_SKIP_HEADINGS = {
    "abstract",
    "acknowledgements",
    "acknowledgment",
    "references",
    "bibliography",
    "appendix references",
    "author contributions",
    "data availability",
    "code availability",
}


def split_markdown_sections(markdown: str, *, max_sections: int = 200) -> list[dict[str, Any]]:
    """Split *markdown* into ATX-heading sections.

    Returns a list of ``{heading, level, content}`` dicts in document order.
    Content before the first heading is attached to a synthetic ``"Preamble"``
    section so nothing is dropped. Sections are capped at ``max_sections`` to
    bound memory for pathological inputs.
    """
    text = markdown or ""
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    preamble: list[str] = []

    def push() -> None:
        nonlocal current
        if current is None:
            return
        body = "\n".join(current["_raw"]).strip()
        current.pop("_raw", None)
        if body or current["heading"].lower() not in _SKIP_HEADINGS:
            current["content"] = body
            sections.append(current)

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            push()
            level = len(match.group(1))
            heading = match.group(2).strip()
            # Normalize the common "3. Title" numbering prefixes from PDF
            # converters so the same section is stable across runs.
            heading = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", heading)
            current = {"heading": heading, "level": level, "_raw": []}
            if not sections and preamble:
                sections.append(
                    {"heading": "Preamble", "level": 0, "content": "\n".join(preamble).strip()}
                )
                preamble = []
        elif current is not None:
            current["_raw"].append(line)
        else:
            preamble.append(line)
    push()

    if preamble:
        joined = "\n".join(preamble).strip()
        if joined:
            sections.insert(0, {"heading": "Preamble", "level": 0, "content": joined})

    if not sections:
        stripped = text.strip()
        if stripped:
            sections.append({"heading": "Preamble", "level": 0, "content": stripped})

    for section in sections:
        section.pop("_raw", None)
    return sections[:max_sections]


def section_anchors(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return a lightweight table-of-contents from *sections*.

    Each entry is ``{heading, anchor}`` where ``anchor`` is a URL-safe slug
    usable for in-page jump links.
    """
    seen: set[str] = set()
    toc: list[dict[str, str]] = []
    for section in sections:
        heading = section.get("heading") or "Preamble"
        base = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
        anchor = f"sec-{base}" if base else "sec-preamble"
        if anchor in seen:
            i = 2
            while f"{anchor}-{i}" in seen:
                i += 1
            anchor = f"{anchor}-{i}"
        seen.add(anchor)
        toc.append({"heading": heading, "anchor": anchor})
    return toc
