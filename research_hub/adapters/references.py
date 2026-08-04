"""Best-effort reference/bibliography parsing from parsed Markdown.

A lightweight, dependency-free parser that turns a paper's Markdown
(typically the ``References`` / ``Bibliography`` section produced by a
PDF-to-Markdown converter such as MinerU) into normalized citation
records. The goal is *not* perfect bibliographic accuracy — it is to
recognize the common shapes enough to surface standard identifiers
(arXiv / DOI) and clickable links for the reader and future citation
graph work.

Two public helpers are exposed:

* :func:`parse_references` — turn a Markdown string into ``references[]``.
* :func:`extract_reference_links` — flatten the standard identifiers and
  URLs found across the parsed references into a deduplicated link list.

Everything here uses only the Python standard library and tolerates
imperfect input: unrecognized entries still yield ``{index, raw}``.
"""

from __future__ import annotations

import re
from typing import Any

# A reference item is introduced by a leading bracket group:
#   [1]   -> numeric index
#   [12]  -> numeric index
#   [Author et al., 2020] -> author-year (no index)
_ITEM_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")

# A bare line that looks like a reference continuation (no marker) but
# actually *starts* a new item is rare in converter output, so we only
# start items on markers. Continuation lines are joined onto the current
# item until the next marker or a blank/heading boundary.

# Section headings that conventionally open the bibliography.
_REFERENCES_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:references|bibliography|reference)\s*$", re.IGNORECASE
)
# Heading-like line immediately following cannot belong to references.
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")

_ARXIV_ID_RE = re.compile(
    r"(?:arXiv:|\barXiv\s*)\s*([0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)", re.IGNORECASE
)
_ARXIV_URL_ID_RE = re.compile(
    r"(?:arxiv\.org|export\.arxiv\.org)/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"doi:\s*(10\.\d{4,9}/[^\s,;\)\]]+)", re.IGNORECASE)
_DOI_URL_RE = re.compile(r"doi\.org\/(10\.\d{4,9}/[^\s,;\)\]]+)", re.IGNORECASE)

_URL_RE = re.compile(r"https?://[^\s,;\)\]]+")

_YEAR_RE = re.compile(r"\b(?:19|20)[0-9]{2}\b")

_TITLE_SEP_RE = re.compile(r"[,.;:]\s+")

# Suppress navigable/boilerplate fragments from being mistaken for a venue.
_VENUE_STOP = re.compile(
    r"^(available|accessed|retrieved|doi|arxiv|https?|proceedings|in )", re.IGNORECASE
)


def _find_references_block(text: str) -> list[str]:
    """Return the lines belonging to the bibliography block, or ``[]``.

    Locates the first ``References`` / ``Bibliography`` heading and returns
    everything from the first item until the next ATX heading or the end of
    the document. Returns an empty list when no bibliography block is found.
    """
    lines = (text or "").splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if _REFERENCES_HEADING_RE.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return []

    block: list[str] = []
    for line in lines[start:]:
        if _ANY_HEADING_RE.match(line):
            break
        block.append(line)
    return block


def _parse_item(marker: str, remainder: str) -> dict[str, Any]:
    """Parse a single already-identified reference item.

    Fills as many fields as possible; the only guarantees are ``raw`` (the
    item's full text) and, when the marker is a plain number, ``index``.
    """
    author_year = None
    if remainder:
        stem = f"{marker} {remainder}"
    else:
        stem = marker

    fields: dict[str, Any] = {"raw": " ".join(stem.split())}

    # Numeric index markers: [1], [12], [23]
    if marker.isdigit():
        fields["index"] = int(marker)
    else:
        # Author-year marker like "Author et al., 2020" — treat as authors.
        author_year = marker.strip()

    # arXiv id: "arXiv:2301.12345" or "arxiv.org/abs/2301.12345".
    arxiv_id = None
    if m := _ARXIV_ID_RE.search(stem) or _ARXIV_URL_ID_RE.search(stem):
        arxiv_id = m.group(1)
    if arxiv_id:
        fields["arxiv_id"] = arxiv_id
        stem = stem.replace(arxiv_id, "")

    # DOI: "doi:10.xxxx/..." or "https://doi.org/10.xxxx/...".
    doi = None
    if m := _DOI_RE.search(stem) or _DOI_URL_RE.search(stem):
        doi = m.group(1)
    if doi:
        fields["doi"] = doi
        stem = stem.replace(doi, "")
    stem = stem.replace("arxiv.org", "").replace("doi.org", "")

    # First external URL (kept even if it was also an arXiv/DOI source).
    urls = _URL_RE.findall(stem)
    if urls:
        fields["url"] = urls[0]

    # Year: first 4-digit year in the remainder.
    years = _YEAR_RE.findall(stem)
    if years:
        fields["year"] = int(years[0])

    # Split the citation into [authors | title] by the first clear separator.
    authors: str | None = author_year
    title: str | None = None
    venue: str | None = None

    remainder_clean = remainder
    if arxiv_id:
        remainder_clean = _ARXIV_ID_RE.sub(" ", remainder_clean)
        remainder_clean = _ARXIV_URL_ID_RE.sub(" ", remainder_clean)
    if doi:
        remainder_clean = _DOI_RE.sub(" ", remainder_clean)
        remainder_clean = _DOI_URL_RE.sub(" ", remainder_clean)
    remainder_clean = re.sub(r"\s+", " ", remainder_clean).strip()

    parts = [p for p in _TITLE_SEP_RE.split(remainder_clean) if p.strip()]
    if parts:
        first = parts[0].strip().strip(".,")
        if not first.lower().startswith(("http", "doi", "arxiv")):
            if authors is None:
                authors = first
            elif title is None:
                title = first
        if len(parts) >= 2 and title is None:
            cand = parts[1].strip().strip(".,")
            if cand and not cand.lower().startswith(("http", "doi", "arxiv")):
                title = cand
        elif len(parts) >= 3 and venue is None:
            cand = parts[2].strip().strip(".,")
            if cand and not _VENUE_STOP.match(cand):
                venue = cand

    if authors:
        fields["authors"] = authors
    if title:
        fields["title"] = title
    if venue:
        fields["venue"] = venue

    return fields


def parse_references(markdown_text: str) -> list[dict[str, Any]]:
    """Parse bibliography entries out of *markdown_text*.

    Returns a list of ``references[]`` dicts in document order. Each entry
    carries at least ``raw`` and, when a numeric marker is present,
    ``index``. Recognized fields are filled best-effort: ``authors``,
    ``title``, ``venue``, ``year``, ``url``, ``arxiv_id``, ``doi``.

    When the text has no recognizable ``References`` / ``Bibliography``
    section this returns ``[]``.
    """
    block = _find_references_block(markdown_text)
    if not block:
        return []

    references: list[dict[str, Any]] = []
    pending_marker: str | None = None
    current_raw: list[str] = []

    def flush() -> None:
        nonlocal pending_marker, current_raw
        if pending_marker is None:
            current_raw = []
            return
        body = " ".join(line.strip() for line in current_raw if line.strip())
        fields = _parse_item(pending_marker, body)
        references.append(fields)
        pending_marker = None
        current_raw = []

    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        m = _ITEM_RE.match(stripped)
        if m:
            flush()
            pending_marker = m.group(1).strip()
            current_raw = [m.group(2).strip()] if m.group(2).strip() else []
        elif pending_marker is not None:
            current_raw.append(stripped)

    flush()
    return references


def extract_reference_links(text: str) -> list[dict[str, str]]:
    """Flatten standard identifiers and URLs from parsed references.

    Returns a deduplicated list of ``{kind, value, url}`` where ``kind`` is
    ``arxiv``, ``doi`` or ``url`` and ``url`` is a clickable HTTP link. Only
    links found inside an actual references block are returned.
    """
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(kind: str, value: str, url: str) -> None:
        if value in seen:
            return
        seen.add(value)
        links.append({"kind": kind, "value": value, "url": url})

    for ref in parse_references(text):
        if arxiv_id := ref.get("arxiv_id"):
            _add("arxiv", arxiv_id, f"https://arxiv.org/abs/{arxiv_id}")
        if doi := ref.get("doi"):
            _add("doi", doi, f"https://doi.org/{doi}")
        if url := ref.get("url"):
            _add("url", url, url)
    return links
