"""JSON Schema generation for public Research Hub contracts.

The contracts are intentionally derived from the Pydantic models used at the
API boundary. Unknown future fields must remain readable by current consumers,
so schemas must not set ``additionalProperties: false``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel

from research_hub.models import (
    Artifact,
    EvidenceAnchor,
    InventionCandidate,
    Job,
    Paper,
    PaperReport,
    PaperVersion,
    PatentDraft,
    PatentStageRun,
)


CONTRACT_SCHEMA_VERSION = "2026-08-02.phase0"

ContractModel: TypeAlias = type[BaseModel]

CONTRACT_MODELS: dict[str, ContractModel] = {
    "Paper": Paper,
    "PaperVersion": PaperVersion,
    "Artifact": Artifact,
    "Job": Job,
    "PaperReport": PaperReport,
    "EvidenceAnchor": EvidenceAnchor,
    "InventionCandidate": InventionCandidate,
    "PatentDraft": PatentDraft,
    "PatentStageRun": PatentStageRun,
}


def contract_json_schema(model_name: str) -> dict:
    """Return the committed public JSON Schema for one contract model."""

    model = CONTRACT_MODELS[model_name]
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://research-hub.local/contracts/{CONTRACT_SCHEMA_VERSION}/{model_name}.schema.json"
    schema["x-contract-version"] = CONTRACT_SCHEMA_VERSION
    schema["x-forward-compatibility"] = {
        "unknown_fields": "accepted_by_readers",
        "additional_properties": "schemas_do_not_set_false",
    }
    return schema


def write_contract_json_schemas(output_dir: Path) -> None:
    """Write all public contract schemas with stable formatting."""

    output_dir.mkdir(parents=True, exist_ok=True)
    for model_name in CONTRACT_MODELS:
        path = output_dir / f"{model_name}.schema.json"
        payload = json.dumps(contract_json_schema(model_name), ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(f"{payload}\n", encoding="utf-8")


if __name__ == "__main__":
    write_contract_json_schemas(Path(__file__).resolve().parent / "json_schemas")
