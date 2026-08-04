from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.schemas import CONTRACT_MODELS, contract_json_schema
from research_hub.models import EvidenceAnchor, Job


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"
SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "json_schemas"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_schema_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_schema_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_schema_nodes(value)


@pytest.mark.parametrize("model_name", sorted(CONTRACT_MODELS))
def test_committed_schema_matches_pydantic_contract(model_name: str) -> None:
    committed = load_json(SCHEMA_ROOT / f"{model_name}.schema.json")

    assert committed == contract_json_schema(model_name)


@pytest.mark.parametrize("model_name,model", sorted(CONTRACT_MODELS.items()))
def test_contract_fixtures_have_valid_positive_and_invalid_negative_examples(model_name: str, model) -> None:
    fixture_dir = FIXTURE_ROOT / "entities" / model_name
    valid = load_json(fixture_dir / "valid.json")
    invalid = load_json(fixture_dir / "invalid.json")

    parsed = model.model_validate(valid)
    assert parsed.model_dump(mode="json")

    with pytest.raises(ValidationError):
        model.model_validate(invalid)


@pytest.mark.parametrize("model_name", sorted(CONTRACT_MODELS))
def test_required_fields_are_declared_for_contract_models(model_name: str) -> None:
    schema = load_json(SCHEMA_ROOT / f"{model_name}.schema.json")

    assert schema["type"] == "object"
    assert schema["required"]


def test_enums_are_declared_for_status_and_evidence_kind() -> None:
    job_schema = load_json(SCHEMA_ROOT / "Job.schema.json")
    evidence_schema = load_json(SCHEMA_ROOT / "EvidenceAnchor.schema.json")

    assert job_schema["properties"]["status"]["enum"] == [
        "queued",
        "running",
        "succeeded",
        "partial_succeeded",
        "retryable_failed",
        "terminal_failed",
        "cancelled",
    ]
    assert evidence_schema["properties"]["kind"]["enum"] == ["fact", "analysis", "hypothesis"]
    with pytest.raises(ValidationError):
        Job.model_validate(load_json(FIXTURE_ROOT / "entities" / "Job" / "invalid_enum.json"))
    with pytest.raises(ValidationError):
        EvidenceAnchor.model_validate(
            load_json(FIXTURE_ROOT / "entities" / "EvidenceAnchor" / "invalid_enum.json")
        )


@pytest.mark.parametrize("model_name,model", sorted(CONTRACT_MODELS.items()))
def test_unknown_fields_are_forward_compatible_for_contract_readers(model_name: str, model) -> None:
    schema = load_json(SCHEMA_ROOT / f"{model_name}.schema.json")
    valid = load_json(FIXTURE_ROOT / "entities" / model_name / "valid.json")
    valid["future_extension_field"] = {"producer": "future phase"}

    parsed = model.model_validate(valid)

    assert parsed.model_dump(mode="json")
    assert schema["x-forward-compatibility"]["unknown_fields"] == "accepted_by_readers"
    assert not any(node.get("additionalProperties") is False for node in iter_schema_nodes(schema))


@pytest.mark.parametrize(
    "fixture_name,expected",
    [
        (
            "ordinary_pdf.json",
            {
                "papers": 1,
                "paper_versions": 1,
                "artifacts": {"raw_pdf", "mineru_markdown"},
                "reports": 1,
                "candidates": 0,
                "drafts": 0,
            },
        ),
        (
            "formula_table_dense_pdf.json",
            {
                "papers": 1,
                "paper_versions": 1,
                "artifacts": {"raw_pdf", "mineru_markdown", "mineru_tables", "mineru_equations"},
                "reports": 1,
                "candidates": 0,
                "drafts": 0,
            },
        ),
        (
            "two_paper_patent_candidate.json",
            {
                "papers": 2,
                "paper_versions": 2,
                "artifacts": {"raw_pdf", "paper_report_markdown", "patent_disclosure_markdown"},
                "reports": 2,
                "candidates": 1,
                "drafts": 1,
            },
        ),
    ],
)
def test_golden_baselines_match_structural_contracts(fixture_name: str, expected: dict[str, Any]) -> None:
    baseline = load_json(FIXTURE_ROOT / "golden" / fixture_name)

    assert len(baseline["papers"]) == expected["papers"]
    assert len(baseline["paper_versions"]) == expected["paper_versions"]
    assert {item["artifact_type"] for item in baseline["artifacts"]} == expected["artifacts"]
    assert len(baseline["paper_reports"]) == expected["reports"]
    assert len(baseline["invention_candidates"]) == expected["candidates"]
    assert len(baseline["patent_drafts"]) == expected["drafts"]

    for collection_name, model_name in [
        ("papers", "Paper"),
        ("paper_versions", "PaperVersion"),
        ("artifacts", "Artifact"),
        ("paper_reports", "PaperReport"),
        ("invention_candidates", "InventionCandidate"),
        ("patent_drafts", "PatentDraft"),
    ]:
        model = CONTRACT_MODELS[model_name]
        for item in baseline[collection_name]:
            model.model_validate(item)
