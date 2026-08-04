from __future__ import annotations

import pytest

from research_hub.adapters.prior_art import validate_prior_art_records


def valid_cnipa_record() -> dict:
    return {
        "source": "cnipa",
        "title": "一种推理资源控制方法",
        "publication_number": "CN123456789A",
        "url": "https://pss-system.cponline.cnipa.gov.cn/example/CN123456789A",
        "abstract": "公开一种根据队列压力控制推理资源的方法。",
        "analysis_basis": "CNIPA abstract",
        "bibliographic_match": True,
        "limitations": "未公开跨组件反馈接口。",
    }


def test_prior_art_contract_accepts_public_bibliographic_abstract_record() -> None:
    records = validate_prior_art_records([valid_cnipa_record()])

    assert records[0]["source_type"] == "patent"
    assert records[0]["publication_number"] == "CN123456789A"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("url", "file:///tmp/private", "public HTTP"),
        ("bibliographic_match", False, "bibliography"),
        ("analysis_basis", "title only", "abstract"),
        ("abstract", "", "missing abstract"),
    ],
)
def test_prior_art_contract_rejects_unverifiable_records(
    field: str,
    value,
    message: str,
) -> None:
    record = valid_cnipa_record()
    record[field] = value

    with pytest.raises(ValueError, match=message):
        validate_prior_art_records([record])
