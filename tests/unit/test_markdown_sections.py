"""Tests for Markdown section extraction used by the parse pipeline."""

from __future__ import annotations

from research_hub.adapters.sections import section_anchors, split_markdown_sections


def test_splits_atx_headings_in_order() -> None:
    md = (
        "# Introduction\nIntro body\n"
        "## Background\nMore detail\n"
        "# Method\nGreat method\n"
        "## Experiment\nResults here\n"
    )
    sections = split_markdown_sections(md)
    assert [s["heading"] for s in sections] == [
        "Introduction",
        "Background",
        "Method",
        "Experiment",
    ]
    intro = sections[0]
    assert intro["level"] == 1
    assert "Intro body" in intro["content"]


def test_preamble_captures_content_before_first_heading() -> None:
    md = "Some preamble text.\n# Real heading\nbody"
    sections = split_markdown_sections(md)
    assert sections[0]["heading"] == "Preamble"
    assert "Some preamble text" in sections[0]["content"]
    assert sections[1]["heading"] == "Real heading"


def test_heading_number_prefixes_normalized() -> None:
    md = "# 1. Introduction\nbody\n# 2. Method\nbody2"
    sections = split_markdown_sections(md)
    assert [s["heading"] for s in sections] == ["Introduction", "Method"]


def test_references_heading_skipped_when_no_content() -> None:
    md = "# Abstract\nAbstract text\n# References\n"
    sections = split_markdown_sections(md)
    headings = [s["heading"] for s in sections]
    # Abstract carries real content so it is kept; an empty References block is
    # dropped as back matter rather than emitting an empty section.
    assert "Abstract" in headings
    assert "References" not in headings
    assert all(s["content"] for s in sections)


def test_empty_input_yields_no_sections() -> None:
    assert split_markdown_sections("") == []
    assert split_markdown_sections(None) == []


def test_section_anchors_are_unique_and_slugged() -> None:
    sections = split_markdown_sections("# Machine Learning\nbody\n# Machine Learning Again\nbody2")
    toc = section_anchors(sections)
    anchors = [entry["anchor"] for entry in toc]
    assert len(anchors) == len(set(anchors))
    assert all(anchor.startswith("sec-") for anchor in anchors)


def test_max_sections_caps_output() -> None:
    md = "".join(f"# H{i}\nbody\n" for i in range(50))
    sections = split_markdown_sections(md, max_sections=5)
    assert len(sections) <= 5
