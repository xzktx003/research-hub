# Research Hub Plan Acceptance

This document maps the implementation to
`../.omx/plans/ai-infra-paper-research-patent-platform.md`. It distinguishes code
completion from evidence that can only be produced by a real deployment over
time.

## Overall Status

- Phases 0–7 are implemented with deterministic unit, contract, integration,
  end-to-end, quality, migration, governance, and browser evidence.
- Phase 8 production controls are implemented: RBAC, trace/logging, business
  metrics, dead-letter replay, backup/restore, retention, license BOM, migration
  reconciliation, and legacy scheduler shutdown.
- Final operational acceptance remains pending until a production-like
  PostgreSQL deployment completes a real 14-day unattended window. Tests and
  audit tooling exist, but generated fixtures are not represented as elapsed
  operational evidence.

## Phase Matrix

| Phase | Implementation status | Primary executable evidence |
|---|---|---|
| 0. Baselines and contracts | Complete | `tests/contract/test_json_schemas.py`, committed golden fixtures, positive/negative fixtures including `PatentStageRun` |
| 1. Research Hub data plane | Complete | `tests/unit/test_database_contracts.py`, `tests/unit/test_core_state_models.py`, artifact store and migration tests |
| 2. Unified discovery | Complete | multi-source adapter contracts, source degradation/429 tests, topic quota tests, canonical dedup tests |
| 3. MinerU integration | Complete | MinerU submit/poll/recovery tests, PDF preflight, registered Markdown/JSON/resource artifacts |
| 4. Reading, translation, digest | Complete | Dify reference contracts, translation/report evidence tests, daily and topic digest API, browser reading routes |
| 5. Relations and combinations | Complete | relation integration tests, positive coupling cases, mechanical aggregation rejection, provenance gates |
| 6. Patent Engine service | Complete | six persisted stages and public schema, prior-art contract, human gate, revision history, Markdown/DOCX generation |
| 7. Unified web workbench | Complete | same-origin SPA, API security contract, browser checks for dashboard, topic digest, reader, jobs, relations, and patent stage timeline |
| 8. Production and migration | Implemented; operational evidence pending | RBAC tests, business metrics, alerts/dead letters, backup/restore, retention, BOM, legacy reconciliation, scheduler shutdown, 14-day audit tool |

## Testable Acceptance Criteria

1. Scheduled/manual discovery and canonical run dedup are covered by production
   contract and service tests.
2. arXiv process-scoped rate limiting, 429 retry, and partial source success are
   covered by service and multi-source contract tests.
3. DOI/arXiv/OpenReview/title/checksum identity merging and retained source hits
   are covered by discovery and database tests.
4. At least ten editable/configurable AI Infra topics, aliases, excludes, and
   quotas are covered by core-state tests.
5. PDF magic, content type, size, checksum, retry failure, and safe download
   behavior are covered by downloader and PDF preflight tests.
6. MinerU external task persistence, disappeared-task recovery, restart-safe
   polling, and artifact registration are covered by service tests.
7. Structured report sections and numeric-condition completeness are covered by
   report and quality tests.
8. The 90% evidence-anchor requirement and fact/analysis/hypothesis distinction
   are enforced by service, contract, and quality tests.
9. Original, Chinese, and bilingual Markdown plus PDF preflight failure behavior
   are covered by translation and rendering tests.
10. Daily digest counts, source/dedup/failure counts, topic distribution, topic
    digest, and reading routes are covered by API, production, E2E, and browser
    tests.
11. Similar, extends, complements, and conflicts relations with evidence are
    covered by relation integration tests.
12. Candidate source count is restricted to 2–5 and aggregation-only inputs are
    rejected by model, contract, API, and E2E tests.
13. Prior-art records require public matching URLs and abstract-based analysis;
    invalid records fail loudly.
14. Draft generation is blocked until the human contribution, sanitization,
    protection-focus, unverified-fact, and prior-art gates pass or carry an
    explicit audited override.
15. Markdown/DOCX outputs use unique version labels; revision tests prove old
    draft IDs and artifacts remain available.
16. Patent fact provenance requires 100% coverage and keeps hypotheses marked as
    unverified effects.
17. The 14-day audit detects missing runs, duplicate records, unexplained missing
    jobs, and failure-isolation violations. A real 14-day production window is
    still required for final operational acceptance.
18. OpenAPI tests require `Idempotency-Key` on every write operation; replay and
    changed-body conflicts are covered by API/database tests.

## Production Metrics

`GET /api/v1/metrics` exposes database-backed gauges for:

- discovery source success ratio;
- download success ratio;
- average parse duration;
- Dify model token usage;
- report success ratio;
- patent candidate pass ratio;
- job status/kind and dead-letter counts.

## Latest Local Verification

- Research Hub: 202 tests passed.
- Dify paper digest baseline: 39 tests passed.
- Patent disclosure Skill baseline: 59 tests passed.
- Ruff, Python compile checks, JavaScript syntax, and static security contracts
   passed.
- Browser verification confirmed the daily digest, topic digest, reading routes,
   six patent stages, local-path redaction, and bounded stage summaries against an
   isolated seeded database.

## External Evidence Still Required

1. Run the PostgreSQL migration and application against a reachable PostgreSQL
   service with `psycopg` installed. The current environment cannot access a
   Docker daemon or PostgreSQL service, so only migration/runtime contract tests
   are available locally.
2. Operate the Research Hub scheduler and worker for 14 consecutive days, then
   run `scripts/audit_14_day.py` against a copied production database.
3. Preserve the resulting audit JSON, backup checksum/row counts, alert history,
   and artifact reconciliation report as immutable release evidence.
4. Replace synthetic quality fixtures with reviewer-labelled production samples
   before claiming live parsing, translation, relation, or patent quality rates.

## Verification Commands

From `research-platform`:

```bash
python -m pytest -q
python -m ruff check contracts research_hub tests
python -m compileall -q contracts research_hub tests
node --check web/app.js
node tests/static/security_check.js
```

Upstream compatibility baselines:

```bash
cd ../dify && python -m pytest paper_digest/tests -q
cd ../patent-disclosure-skill && python -m pytest -q
```