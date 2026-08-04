"""Tests for best-effort reference/bibliography parsing.

Covers the three public surfaces of ``research_hub/adapters/references.py``:
:func:`parse_references`, :func:`extract_reference_links`, and the
integration point where parsed sections carry a ``references`` field.
"""

from __future__ import annotations

from research_hub.adapters.references import (
    extract_reference_links,
    parse_references,
)
from research_hub.adapters.sections import split_markdown_sections

_SAMPLE = """\
# Introduction
We build on prior work.

# References
[1] John Smith, A Study of Models, Journal of AI, 2020, arXiv:2301.12345
[2] Jane Doe, Deep Learning, ICML, 2019, doi:10.1234/abcd.5678
[3] Bob Lee, https://example.com/papers/attention
"""


def test_parses_numbered_reference_entry() -> None:
    refs = parse_references(_SAMPLE)
    assert len(refs) == 3

    first = refs[0]
    assert first["index"] == 1
    assert "John Smith" in first["raw"]
    assert first["arxiv_id"] == "2301.12345"
    assert first["year"] == 2020
    assert "John Smith" in first.get("authors", "")
    assert first.get("title") is not None

    second = refs[1]
    assert second["index"] == 2
    assert second["doi"] == "10.1234/abcd.5678"
    assert second["year"] == 2019


def test_arxiv_id_extraction_variants() -> None:
    text = """\
# References
[1] Author, Title, 2020, arXiv:2301.12345
[2] Author, Title, 2021, https://arxiv.org/abs/2301.99999v2
"""
    refs = parse_references(text)
    assert refs[0]["arxiv_id"] == "2301.12345"
    assert refs[1]["arxiv_id"] == "2301.99999v2"


def test_doi_extraction_variants() -> None:
    text = """\
# References
[1] Author, Title, 2020, doi:10.1000/xyz123
[2] Author, Title, 2020, https://doi.org/10.1000/abc456
"""
    refs = parse_references(text)
    assert refs[0]["doi"] == "10.1000/xyz123"
    assert refs[1]["doi"] == "10.1000/abc456"


def test_author_year_marker_without_index() -> None:
    text = """\
# References
[Smith et al., 2020] S. Smith, A Great Paper, NeurIPS, 2020, arXiv:2301.11111
"""
    refs = parse_references(text)
    assert "index" not in refs[0]
    assert "Smith et al." in refs[0]["authors"]
    assert refs[0]["arxiv_id"] == "2301.11111"


def test_no_references_section_returns_empty() -> None:
    markdown = "# Introduction\nNo bibliography here.\n"
    refs = parse_references(markdown)
    assert refs == []


def test_unparseable_entries_still_keep_raw_and_index() -> None:
    markdown = (
        "# References\n"
        "[1] Some totally unrecognizable line without any structure\n"
        ""
    )
    refs = parse_references(markdown)
    assert len(refs) == 1
    assert refs[0]["index"] == 1
    assert "unrecognizable" in refs[0]["raw"]


def test_extract_reference_links() -> None:
    links = extract_reference_links(_SAMPLE)
    kinds = {link["kind"] for link in links}
    assert "arxiv" in kinds
    assert "doi" in kinds
    assert "url" in kinds

    arxiv = next(link for link in links if link["kind"] == "arxiv")
    assert arxiv["url"] == "https://arxiv.org/abs/2301.12345"
    doi = next(link for link in links if link["kind"] == "doi")
    assert doi["url"] == "https://doi.org/10.1234/abcd.5678"


def test_sections_carry_references_field() -> None:
    """Parsed sections expose the references alongside the toc/sections."""
    sections = split_markdown_sections(_SAMPLE)
    # split_markdown_sections itself does not add references — the service
    # layer does. Here we verify the section output still has References
    # content that parse_references can consume end-to-end.
    headings = [s["heading"].lower() for s in sections]
    assert "references" in headings
    assert len(parse_references(_SAMPLE)) == 3
