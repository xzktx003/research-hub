# Quality evaluation

Research Hub quality acceptance is evaluated with deterministic metrics instead
of a single subjective pass/fail label. Run the committed baseline with:

```bash
python scripts/evaluate_quality.py tests/fixtures/quality/acceptance_case.json
```

The command exits non-zero if any group fails its threshold:

- parsing structure recall: at least 95% across sections, formulas, tables, and images;
- translation: 100% formula, citation, glossary, and bilingual-block preservation;
- reports: all required sections, at least 90% field evidence support and traceability,
  and complete model/dataset/hardware/metric/comparison conditions for numeric claims;
- relations: at least 90% positive recall and at most 5% known-unrelated pair rate;
- patent conversion: 100% verifiable prior-art records, fact provenance coverage,
  and draft version uniqueness.

Production evaluation cases should replace the committed synthetic identifiers
with sampled page/block IDs and reviewer-labelled relation pairs. The fixture is
an executable contract for the metrics and thresholds, not evidence that live
external model output has already achieved them.
