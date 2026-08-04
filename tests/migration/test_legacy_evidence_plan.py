from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from conftest import PROJECT_ROOT

from research_hub.importers.legacy_evidence import plan_legacy_sources, reconcile_bundle, records_to_jsonl, write_bundle


FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "migration"


def test_legacy_evidence_plan_is_deterministic_across_all_source_families(tmp_path: Path) -> None:
    dify_db = _build_dify_sqlite(tmp_path)
    mineru_root = FIXTURES / "mineru_daily"
    patent_root = FIXTURES / "patent_drafts"

    first = plan_legacy_sources(dify_sqlite=dify_db, mineru_root=mineru_root, patent_drafts_root=patent_root)
    second = plan_legacy_sources(dify_sqlite=dify_db, mineru_root=mineru_root, patent_drafts_root=patent_root)

    assert first == second
    assert first["summary"]["records_seen"] == 5
    assert first["summary"]["artifacts_seen"] == 11
    assert first["summary"]["invalid_paths"] == 1
    assert first["summary"]["conflicts"] == 1
    assert {source["source"] for source in first["sources"]} == {
        "dify_sqlite",
        "mineru_daily_tree",
        "patent_disclosure_drafts",
    }
    assert all(item["checksum"].startswith("sha256:") for item in first["source_checksums"])

    canonical_ids = [record["proposed_canonical_id"] for record in first["records"]]
    assert "paper:arxiv:2608.10001" in canonical_ids
    assert "paper:arxiv:2608.10002" in canonical_ids
    assert "patent-draft:speculative-decode-cache-coordination" in canonical_ids


def test_legacy_evidence_bundle_reconciles_with_plan(tmp_path: Path) -> None:
    dify_db = _build_dify_sqlite(tmp_path)
    plan = plan_legacy_sources(
        dify_sqlite=dify_db,
        mineru_root=FIXTURES / "mineru_daily",
        patent_drafts_root=FIXTURES / "patent_drafts",
    )

    jsonl = records_to_jsonl(plan)
    report = reconcile_bundle(plan, jsonl)
    written = write_bundle(plan, tmp_path / "legacy-import-bundle.jsonl")

    assert report["status"] == "matched"
    assert written["status"] == "matched"
    assert written["actual_records"] == plan["summary"]["records_seen"]
    assert Path(written["bundle_path"]).is_file()
    assert written["bundle_checksum"].startswith("sha256:")


def test_migrate_legacy_sources_cli_outputs_plan_and_bundle(tmp_path: Path) -> None:
    dify_db = _build_dify_sqlite(tmp_path)
    bundle = tmp_path / "bundle.jsonl"
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "migrate_legacy_sources.py"),
        "--dify-sqlite",
        str(dify_db),
        "--mineru-root",
        str(FIXTURES / "mineru_daily"),
        "--patent-drafts-root",
        str(FIXTURES / "patent_drafts"),
        "--bundle-output",
        str(bundle),
    ]

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "legacy-migration-plan/v1"
    assert payload["post_import_diff"]["status"] == "matched"
    assert bundle.read_text(encoding="utf-8").count("\n") == payload["summary"]["records_seen"]


def _build_dify_sqlite(tmp_path: Path) -> Path:
    db_path = tmp_path / "paper_digest.sqlite3"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript((FIXTURES / "dify_seed.sql").read_text(encoding="utf-8"))
        (tmp_path / "papers").mkdir()
        (tmp_path / "papers" / "speculative-decode.pdf").write_bytes(b"%PDF-1.4\nfixture dify pdf\n")
    finally:
        connection.close()
    return db_path
