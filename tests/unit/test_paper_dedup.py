"""Tests for the cross-source paper dedup key and title-based dedup lookup."""

from __future__ import annotations

import pytest

from research_hub.models import PaperCreate, PaperIdentifier
from research_hub.repository import (
    Repository,
    normalized_paper_dedup_key,
)


def test_dedup_key_is_deterministic() -> None:
    a = normalized_paper_dedup_key("Attention Is All You Need", "Ashish Vaswani", "2017")
    b = normalized_paper_dedup_key("Attention Is All You Need", "Ashish Vaswani", "2017")
    assert a == b


def test_dedup_key_differs_on_title() -> None:
    a = normalized_paper_dedup_key("Attention Is All You Need", "Ashish Vaswani", "2017")
    b = normalized_paper_dedup_key("BERT: Pre-training", "Jacob Devlin", "2018")
    assert a != b


def test_dedup_key_normalizes_punctuation_and_case() -> None:
    a = normalized_paper_dedup_key("Attention, Is All You Need!", "Ashish Vaswani", "2017")
    b = normalized_paper_dedup_key("attention is all you need", "Ashish Vaswani", "2017")
    assert a == b


def test_dedup_key_includes_year_signal() -> None:
    a = normalized_paper_dedup_key("A Paper", "Alice", "2020")
    b = normalized_paper_dedup_key("A Paper", "Alice", "2021")
    assert a != b


def test_create_paper_dedups_by_normalized_title_when_author_signal(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        first = repo.create_paper(
            PaperCreate(
                canonical_title="Cross-source Duplicate Paper",
                abstract="v1 abstract",
                language="en",
                metadata={"authors": ["Zoe Researcher"]},
            )
        )
        second = repo.create_paper(
            PaperCreate(
                canonical_title="Cross-Source duplicate paper",
                abstract="v2 abstract",
                language="en",
                metadata={"authors": ["Zoe Researcher"]},
            )
        )
        # Both share normalized title + first author => collapsed into one paper.
        assert second.id == first.id


def test_create_paper_keeps_distinct_when_no_extra_signal(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        first = repo.create_paper(PaperCreate(canonical_title="Shared Title", language="en"))
        second = repo.create_paper(PaperCreate(canonical_title="Shared Title", language="en"))
        # No author/year signal => two distinct papers preserved.
        assert first.id != second.id
