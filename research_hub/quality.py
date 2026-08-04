"""Deterministic quality metrics for Research Hub acceptance baselines."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse


REPORT_FIELDS = (
    "summary",
    "motivation",
    "method",
    "experiments",
    "results",
    "innovation",
    "limitations",
    "engineering_value",
    "reproduction_plan",
)


def percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 100.0
    return round(numerator * 100.0 / denominator, 2)


def evaluate_parsing(
    expected: Mapping[str, Iterable[str]],
    observed: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    """Measure structural recall for pages, formulas, tables, and image references."""

    kinds = ("sections", "formulas", "tables", "images")
    details: dict[str, dict[str, Any]] = {}
    expected_total = 0
    matched_total = 0
    for kind in kinds:
        expected_values = {str(value) for value in expected.get(kind, ())}
        observed_values = {str(value) for value in observed.get(kind, ())}
        matched = expected_values & observed_values
        missing = expected_values - observed_values
        expected_total += len(expected_values)
        matched_total += len(matched)
        details[kind] = {
            "expected": len(expected_values),
            "matched": len(matched),
            "recall_percent": percentage(len(matched), len(expected_values)),
            "missing": sorted(missing),
        }
    score = percentage(matched_total, expected_total)
    return {
        "status": "ok" if score >= 95.0 else "failed",
        "structure_recall_percent": score,
        "details": details,
    }


def evaluate_translation(
    source_markdown: str,
    translated_markdown: str,
    *,
    bilingual_blocks: Iterable[Mapping[str, Any]] = (),
    glossary: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Measure formula/citation preservation, glossary use, and bilingual alignment."""

    source_formulas = _formula_tokens(source_markdown)
    translated_formulas = _formula_tokens(translated_markdown)
    source_citations = set(re.findall(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]", source_markdown))
    translated_citations = set(re.findall(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]", translated_markdown))
    required_terms = {
        source: target
        for source, target in (glossary or {}).items()
        if source.lower() in source_markdown.lower()
    }
    matched_terms = {
        source: target
        for source, target in required_terms.items()
        if target.lower() in translated_markdown.lower()
    }
    blocks = list(bilingual_blocks)
    aligned_blocks = sum(
        1
        for block in blocks
        if str(block.get("source_block_id") or "").strip()
        and str(block.get("original") or "").strip()
        and str(block.get("translation") or "").strip()
    )
    formula_score = percentage(len(source_formulas & translated_formulas), len(source_formulas))
    citation_score = percentage(len(source_citations & translated_citations), len(source_citations))
    glossary_score = percentage(len(matched_terms), len(required_terms))
    alignment_score = percentage(aligned_blocks, len(blocks))
    status = "ok" if min(formula_score, citation_score, glossary_score, alignment_score) == 100.0 else "failed"
    return {
        "status": status,
        "formula_preservation_percent": formula_score,
        "citation_preservation_percent": citation_score,
        "glossary_consistency_percent": glossary_score,
        "bilingual_alignment_percent": alignment_score,
        "missing_formulas": sorted(source_formulas - translated_formulas),
        "missing_citations": sorted(source_citations - translated_citations),
        "missing_glossary_terms": sorted(set(required_terms) - set(matched_terms)),
    }


def evaluate_report(
    report: Mapping[str, Any],
    *,
    technology_claims: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Measure required sections, evidence support, traceability, and numeric conditions."""

    populated = {
        field
        for field in REPORT_FIELDS
        if str(report.get(field) or "").strip()
    }
    evidence = [item for item in report.get("evidence", ()) if isinstance(item, Mapping)]
    anchored_fields = {
        str(item.get("report_field") or "").strip()
        for item in evidence
        if str(item.get("report_field") or "").strip()
    }
    traceable = sum(
        1
        for item in evidence
        if item.get("page") is not None
        or str(item.get("section") or "").strip()
        or str(item.get("source") or "").startswith("artifact:")
    )
    claims = [claim for claim in technology_claims if isinstance(claim, Mapping)]
    numeric_claims = [claim for claim in claims if bool(claim.get("contains_number"))]
    conditioned = sum(
        1
        for claim in numeric_claims
        if all(str(claim.get(key) or "").strip() for key in ("model", "dataset", "hardware", "metric", "comparison"))
    )
    required_score = percentage(len(populated), len(REPORT_FIELDS))
    support_score = percentage(len(anchored_fields & populated), len(populated))
    traceability_score = percentage(traceable, len(evidence))
    condition_score = percentage(conditioned, len(numeric_claims))
    return {
        "status": "ok"
        if required_score == 100.0 and support_score >= 90.0 and traceability_score >= 90.0 and condition_score == 100.0
        else "failed",
        "required_section_percent": required_score,
        "fact_support_percent": support_score,
        "evidence_traceability_percent": traceability_score,
        "numeric_condition_completeness_percent": condition_score,
        "unsupported_fields": sorted(populated - anchored_fields),
    }


def evaluate_relations(
    predicted_pairs: Iterable[str],
    expected_positive_pairs: Iterable[str],
    unrelated_pairs: Iterable[str],
) -> dict[str, Any]:
    predicted = {str(value) for value in predicted_pairs}
    positive = {str(value) for value in expected_positive_pairs}
    unrelated = {str(value) for value in unrelated_pairs}
    recalled = predicted & positive
    false_pairs = predicted & unrelated
    recall = percentage(len(recalled), len(positive))
    unrelated_rate = percentage(len(false_pairs), len(unrelated)) if unrelated else 0.0
    return {
        "status": "ok" if recall >= 90.0 and unrelated_rate <= 5.0 else "failed",
        "positive_recall_percent": recall,
        "unrelated_pair_rate_percent": unrelated_rate,
        "missed_positive_pairs": sorted(positive - predicted),
        "false_unrelated_pairs": sorted(false_pairs),
    }


def evaluate_patent_quality(
    prior_art_records: Iterable[Mapping[str, Any]],
    claim_provenance: Iterable[Mapping[str, Any]],
    technical_fact_ids: Iterable[str],
    draft_version_labels: Iterable[str],
) -> dict[str, Any]:
    records = [record for record in prior_art_records if isinstance(record, Mapping)]
    valid_records = sum(
        1
        for record in records
        if _public_url(str(record.get("url") or ""))
        and bool(record.get("bibliographic_match"))
        and str(record.get("analysis_basis") or "").strip()
    )
    facts = {str(value) for value in technical_fact_ids}
    mapped_facts = {
        str(item.get("claim_id") or item.get("technical_fact_id") or "")
        for item in claim_provenance
        if isinstance(item, Mapping) and str(item.get("source") or "").strip()
    }
    versions = [str(value) for value in draft_version_labels]
    source_validity = percentage(valid_records, len(records))
    provenance_coverage = percentage(len(facts & mapped_facts), len(facts))
    version_uniqueness = percentage(len(set(versions)), len(versions))
    return {
        "status": "ok"
        if source_validity == 100.0 and provenance_coverage == 100.0 and version_uniqueness == 100.0
        else "failed",
        "prior_art_source_validity_percent": source_validity,
        "fact_provenance_coverage_percent": provenance_coverage,
        "version_non_overwrite_percent": version_uniqueness,
        "unmapped_fact_ids": sorted(facts - mapped_facts),
    }


def evaluate_acceptance_case(case: Mapping[str, Any]) -> dict[str, Any]:
    parsing = evaluate_parsing(case.get("parsing_expected", {}), case.get("parsing_observed", {}))
    translation = evaluate_translation(
        str(case.get("source_markdown") or ""),
        str(case.get("translated_markdown") or ""),
        bilingual_blocks=case.get("bilingual_blocks", ()),
        glossary=case.get("glossary", {}),
    )
    report = evaluate_report(case.get("report", {}), technology_claims=case.get("technology_claims", ()))
    relations = evaluate_relations(
        case.get("predicted_relations", ()),
        case.get("expected_relations", ()),
        case.get("unrelated_relations", ()),
    )
    patent = evaluate_patent_quality(
        case.get("prior_art_records", ()),
        case.get("claim_provenance", ()),
        case.get("technical_fact_ids", ()),
        case.get("draft_version_labels", ()),
    )
    checks = {
        "parsing": parsing,
        "translation": translation,
        "report": report,
        "relations": relations,
        "patent": patent,
    }
    return {
        "status": "ok" if all(check["status"] == "ok" for check in checks.values()) else "failed",
        "checks": checks,
    }


def _formula_tokens(markdown: str) -> set[str]:
    tokens = set(re.findall(r"\$\$.*?\$\$|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$)", markdown, re.DOTALL))
    tokens.update(re.findall(r"\\\(.*?\\\)|\\\[.*?\\\]", markdown, re.DOTALL))
    return {" ".join(token.split()) for token in tokens}


def _public_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
