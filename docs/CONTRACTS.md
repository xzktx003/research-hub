# Research Hub Contracts

This directory documents the Phase 0 public JSON contracts used by tests,
import/export fixtures, and future integration work. The contracts are derived
from the Pydantic models in `research_hub.models` and are committed as JSON
Schema files under `contracts/json_schemas/`.

## Contract Set

The Phase 0 contract set covers these persisted or cross-boundary entities:

- `Paper`
- `PaperVersion`
- `Artifact`
- `Job`
- `PaperReport`
- `EvidenceAnchor`
- `InventionCandidate`
- `PatentDraft`
- `PatentStageRun`

The generator lives in `contracts/schemas.py`. Run it from the
`research-platform` directory after intentional model contract changes:

```bash
python contracts/schemas.py
```

Schema freshness is enforced by `tests/contract/test_json_schemas.py`, which
compares every committed schema file against `model_json_schema()` plus the
contract metadata added by `contracts.schemas.contract_json_schema`.

## Forward Compatibility

Contract readers must accept payloads that contain unknown future fields. This
allows a future producer to add fields without breaking old consumers.

The committed schemas therefore do not set `additionalProperties: false`.
Tests also validate that every contract model can read a positive fixture after
an extra `future_extension_field` is added.

Unknown-field retention is model-specific runtime behavior. Some Pydantic models
ignore unknown fields during `model_dump()`, while `EvidenceAnchor` currently
retains them. The formal Phase 0 compatibility guarantee is only that readers
accept the field and schemas do not prohibit it.

## Required Fields And Enums

Every schema is expected to declare required fields for its object contract.
Tests validate this structurally and validate each model against:

- `tests/fixtures/contracts/entities/<Model>/valid.json`
- `tests/fixtures/contracts/entities/<Model>/invalid.json`

Enum-bearing fields are tested explicitly:

- `Job.status`: `queued`, `running`, `succeeded`, `partial_succeeded`,
  `retryable_failed`, `terminal_failed`, `cancelled`
- `EvidenceAnchor.kind`: `fact`, `analysis`, `hypothesis`
- `PatentStageRun.stage`: `intake`, `candidate_analysis`, `prior_art`,
  `preview`, `builder`, `self_check`
- `PatentStageRun.status`: `pending`, `running`, `succeeded`, `failed`,
  `skipped`, `cancelled`

The enum negative fixtures are:

- `tests/fixtures/contracts/entities/Job/invalid_enum.json`
- `tests/fixtures/contracts/entities/EvidenceAnchor/invalid_enum.json`

## Golden Baselines

Golden baselines are structural contract fixtures, not content snapshots. Tests
assert collection counts, required artifact types, and model validity while
avoiding brittle checks on generated prose.

Current baselines:

- `ordinary_pdf.json`: one normal PDF ingestion path with `raw_pdf`,
  `mineru_markdown`, and one `PaperReport`.
- `formula_table_dense_pdf.json`: one dense PDF path with `raw_pdf`,
  `mineru_markdown`, `mineru_tables`, `mineru_equations`, and one
  `PaperReport`.
- `two_paper_patent_candidate.json`: two analyzed papers, two reports, one
  approved `InventionCandidate`, and one generated `PatentDraft` with a
  `patent_disclosure_markdown` artifact.

These baselines should be extended only when a downstream workflow needs a new
stable structural expectation. They should not include volatile job IDs,
timestamps from live execution, local filesystem paths, or full generated
document text unless those details are the contract under test.

## Verification

Run the contract suite from `research-platform`:

```bash
pytest tests/contract/test_json_schemas.py
```

For broader confidence after changing the underlying Pydantic models, also run:

```bash
pytest tests/unit/test_model_contracts.py tests/contract/test_json_schemas.py
```
