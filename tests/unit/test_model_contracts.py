from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_hub.models import EvidenceAnchor, InventionCandidateCreate


def source_ref(index: int) -> dict[str, str]:
    return {
        "paper_id": f"paper-{index}",
        "paper_version_id": f"paper-version-{index}",
        "contribution": "提供可组合技术机制",
    }


def test_candidate_requires_at_least_two_source_papers() -> None:
    with pytest.raises(ValidationError):
        InventionCandidateCreate(sources=[source_ref(1)])


def test_candidate_accepts_two_to_five_source_papers() -> None:
    candidate = InventionCandidateCreate(sources=[source_ref(1), source_ref(2)])

    assert len(candidate.sources) == 2


def test_candidate_rejects_more_than_five_source_papers() -> None:
    with pytest.raises(ValidationError):
        InventionCandidateCreate(sources=[source_ref(index) for index in range(6)])


def test_candidate_evidence_requires_fact_analysis_or_hypothesis() -> None:
    with pytest.raises(ValidationError):
        InventionCandidateCreate(
            sources=[source_ref(1), source_ref(2)],
            integration_mechanism="combine controllers across runtime stages",
            evidence=[{"kind": "opinion", "source": "model", "note": "unsupported"}],
        )


def test_evidence_anchor_preserves_report_field_and_extra_metadata() -> None:
    anchor = EvidenceAnchor(
        kind="fact",
        source="paper:p1",
        report_field="method",
        note="quoted method",
        claim_id="claim-1",
    )

    dumped = anchor.model_dump()
    assert dumped["report_field"] == "method"
    assert dumped["claim_id"] == "claim-1"
