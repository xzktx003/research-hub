from __future__ import annotations

import json
from pathlib import Path

from research_hub.quality import (
    evaluate_acceptance_case,
    evaluate_parsing,
    evaluate_translation,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "quality" / "acceptance_case.json"


def test_acceptance_quality_fixture_passes_all_thresholds() -> None:
    report = evaluate_acceptance_case(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert report["status"] == "ok"
    assert set(report["checks"]) == {"parsing", "translation", "report", "relations", "patent"}


def test_parsing_metric_fails_when_formula_or_table_is_missing() -> None:
    report = evaluate_parsing(
        {"sections": ["s1"], "formulas": ["f1"], "tables": ["t1"], "images": []},
        {"sections": ["s1"], "formulas": [], "tables": [], "images": []},
    )

    assert report["status"] == "failed"
    assert report["details"]["formulas"]["missing"] == ["f1"]
    assert report["details"]["tables"]["missing"] == ["t1"]


def test_translation_metric_fails_on_formula_citation_and_alignment_loss() -> None:
    report = evaluate_translation(
        "Use KV cache $x+y$ [1].",
        "使用键值缓存。",
        bilingual_blocks=[{"source_block_id": "b1", "original": "Use KV cache", "translation": ""}],
        glossary={"KV cache": "键值缓存"},
    )

    assert report["status"] == "failed"
    assert report["formula_preservation_percent"] == 0.0
    assert report["citation_preservation_percent"] == 0.0
    assert report["bilingual_alignment_percent"] == 0.0
