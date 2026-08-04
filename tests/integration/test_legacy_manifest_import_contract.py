from __future__ import annotations

import json
from pathlib import Path

from conftest import import_or_xfail


def test_legacy_manifest_fixture_contains_papers_and_artifacts() -> None:
    manifest_path = Path(__file__).parents[1] / "fixtures" / "legacy_manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert len(manifest["papers"]) == 2
    assert {"pdf_path", "markdown_path", "summary_path", "checksum"}.issubset(manifest["papers"][0])


def test_legacy_manifest_importer_imports_manifest_idempotently(tmp_path: Path) -> None:
    module = import_or_xfail(
        "research_hub.importers.legacy_manifest",
        "Legacy MinerU/Dify manifest importer contract is waiting for implementation",
    )
    manifest_path = Path(__file__).parents[1] / "fixtures" / "legacy_manifest.json"

    first = module.import_manifest(manifest_path, database_path=tmp_path / "hub.sqlite3", dry_run=False)
    second = module.import_manifest(manifest_path, database_path=tmp_path / "hub.sqlite3", dry_run=False)

    assert first["papers_created"] == 2
    assert second["papers_created"] == 0
    assert second["papers_matched"] == 2
