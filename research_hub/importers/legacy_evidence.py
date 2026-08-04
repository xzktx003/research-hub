"""Dry-run planning and reconciliation for historical Research Hub imports.

This module intentionally does not write to the production database.  It
normalizes legacy Dify, MinerU, and patent-disclosure sources into a stable
bundle contract that can be reviewed or consumed by a later repository-backed
import step.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "legacy-migration-plan/v1"
HASH_PREFIX = "sha256:"
PAPER_PATH_FIELDS = (
    "pdf_path",
    "local_pdf_path",
    "markdown_path",
    "mineru_markdown_path",
    "summary_path",
    "review_path",
)
PATENT_SUFFIXES = {".md", ".markdown", ".docx"}


@dataclass(frozen=True)
class PlannedArtifact:
    kind: str
    source_field: str
    path: str
    resolved_path: str | None
    exists: bool
    within_allowed_roots: bool
    checksum: str | None
    size_bytes: int | None
    media_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_field": self.source_field,
            "path": self.path,
            "resolved_path": self.resolved_path,
            "exists": self.exists,
            "within_allowed_roots": self.within_allowed_roots,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class PlannedRecord:
    source: str
    record_type: str
    natural_key: str
    proposed_canonical_id: str
    payload: dict[str, Any]
    artifacts: list[PlannedArtifact] = field(default_factory=list)
    source_path: str | None = None

    @property
    def import_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.source}:{self.record_type}:{self.natural_key}"))

    @property
    def payload_checksum(self) -> str:
        payload = {
            "source": self.source,
            "record_type": self.record_type,
            "natural_key": self.natural_key,
            "proposed_canonical_id": self.proposed_canonical_id,
            "payload": self.payload,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
        }
        return _json_checksum(payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "source": self.source,
            "record_type": self.record_type,
            "natural_key": self.natural_key,
            "proposed_canonical_id": self.proposed_canonical_id,
            "source_path": self.source_path,
            "payload_checksum": self.payload_checksum,
            "payload": self.payload,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "proposed_actions": _actions_for_record(self),
        }


def plan_legacy_sources(
    *,
    dify_sqlite: str | Path | None = None,
    mineru_root: str | Path | None = None,
    patent_drafts_root: str | Path | None = None,
    allowed_roots: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Build a deterministic dry-run import plan for available legacy sources."""

    roots = _allowed_roots(dify_sqlite, mineru_root, patent_drafts_root, allowed_roots)
    records: list[PlannedRecord] = []
    source_checksums: list[dict[str, Any]] = []
    source_reports: list[dict[str, Any]] = []

    if dify_sqlite is not None:
        dify_path = Path(dify_sqlite).expanduser().resolve()
        dify_records, dify_report = _plan_dify_sqlite(dify_path, roots)
        records.extend(dify_records)
        source_reports.append(dify_report)
        source_checksums.extend(_source_checksum(dify_path, "dify_sqlite"))

    if mineru_root is not None:
        mineru_path = Path(mineru_root).expanduser().resolve()
        mineru_records, mineru_report, mineru_checksums = _plan_mineru_tree(mineru_path, roots)
        records.extend(mineru_records)
        source_reports.append(mineru_report)
        source_checksums.extend(mineru_checksums)

    if patent_drafts_root is not None:
        patent_path = Path(patent_drafts_root).expanduser().resolve()
        patent_records, patent_report, patent_checksums = _plan_patent_drafts(patent_path, roots)
        records.extend(patent_records)
        source_reports.append(patent_report)
        source_checksums.extend(patent_checksums)

    records = sorted(records, key=lambda item: (item.proposed_canonical_id, item.source, item.natural_key))
    conflicts = _conflicts(records)
    artifact_count = sum(len(record.artifacts) for record in records)
    invalid_paths = [
        artifact.as_dict()
        for record in records
        for artifact in record.artifacts
        if not artifact.exists or not artifact.within_allowed_roots
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "dry_run_plan",
        "summary": {
            "sources_seen": len(source_reports),
            "records_seen": len(records),
            "artifacts_seen": artifact_count,
            "conflicts": len(conflicts),
            "invalid_paths": len(invalid_paths),
            "proposed_canonical_ids": len({record.proposed_canonical_id for record in records}),
        },
        "sources": source_reports,
        "source_checksums": sorted(source_checksums, key=lambda item: (item["kind"], item["path"])),
        "path_validation": {
            "allowed_roots": [str(root) for root in roots],
            "invalid": invalid_paths,
        },
        "records": [record.as_dict() for record in records],
        "conflicts": conflicts,
        "post_import_diff": {
            "status": "not_applied",
            "expected_records": len(records),
            "expected_artifacts": artifact_count,
            "bundle_contract": "Write records as sorted JSONL; consumers match import_id and payload_checksum.",
        },
    }


def records_to_jsonl(plan: dict[str, Any]) -> str:
    """Serialize planned records as deterministic JSONL import bundle rows."""

    return "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in plan["records"])


def reconcile_bundle(plan: dict[str, Any], bundle_jsonl: str) -> dict[str, Any]:
    """Compare an import bundle or post-import export with the current plan."""

    expected = {record["import_id"]: record["payload_checksum"] for record in plan.get("records", [])}
    actual: dict[str, str | None] = {}
    duplicates: list[str] = []
    for line in bundle_jsonl.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        import_id = str(row.get("import_id"))
        if import_id in actual:
            duplicates.append(import_id)
        actual[import_id] = row.get("payload_checksum")
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    changed = sorted(key for key in set(expected) & set(actual) if expected[key] != actual[key])
    return {
        "status": "matched" if not missing and not unexpected and not changed and not duplicates else "diff",
        "expected_records": len(expected),
        "actual_records": len(actual),
        "missing_import_ids": missing,
        "unexpected_import_ids": unexpected,
        "changed_import_ids": changed,
        "duplicate_import_ids": sorted(duplicates),
    }


def write_bundle(plan: dict[str, Any], output: str | Path) -> dict[str, Any]:
    """Write the deterministic JSONL bundle and return a reconciliation report."""

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonl = records_to_jsonl(plan)
    path.write_text(jsonl + ("\n" if jsonl else ""), encoding="utf-8")
    report = reconcile_bundle(plan, path.read_text(encoding="utf-8"))
    report["bundle_path"] = str(path)
    report["bundle_checksum"] = _file_checksum(path) if path.is_file() else None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan legacy Research Hub migrations without applying writes")
    parser.add_argument("--dify-sqlite", help="Dify paper_digest SQLite database path")
    parser.add_argument("--mineru-root", help="MinerU daily manifest file or directory tree")
    parser.add_argument("--patent-drafts-root", help="Historical patent draft export directory")
    parser.add_argument("--allowed-root", action="append", default=[], help="Additional allowed artifact root")
    parser.add_argument("--bundle-output", help="Write deterministic JSONL bundle to this path")
    parser.add_argument("--jsonl", action="store_true", help="Print the JSONL bundle instead of the full plan")
    args = parser.parse_args(argv)

    plan = plan_legacy_sources(
        dify_sqlite=args.dify_sqlite,
        mineru_root=args.mineru_root,
        patent_drafts_root=args.patent_drafts_root,
        allowed_roots=args.allowed_root,
    )
    if args.bundle_output:
        plan["post_import_diff"] = write_bundle(plan, args.bundle_output)
    if args.jsonl:
        print(records_to_jsonl(plan))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _plan_dify_sqlite(database_path: Path, allowed_roots: list[Path]) -> tuple[list[PlannedRecord], dict[str, Any]]:
    if not database_path.is_file():
        return [], {"source": "dify_sqlite", "path": str(database_path), "available": False, "records_seen": 0}
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "papers"):
            return [], {"source": "dify_sqlite", "path": str(database_path), "available": True, "records_seen": 0}
        rows = connection.execute("SELECT * FROM papers ORDER BY version_id").fetchall()
    finally:
        connection.close()

    records: list[PlannedRecord] = []
    for row in rows:
        payload = _row_payload(row)
        natural_key = _dify_natural_key(payload)
        records.append(
            PlannedRecord(
                source="dify_paper_digest_sqlite",
                record_type="paper",
                natural_key=natural_key,
                proposed_canonical_id=_paper_canonical_id(payload, natural_key),
                payload=payload,
                artifacts=_paper_artifacts(payload, database_path.parent, allowed_roots),
                source_path=str(database_path),
            )
        )
    return records, {
        "source": "dify_sqlite",
        "path": str(database_path),
        "available": True,
        "records_seen": len(records),
    }


def _plan_mineru_tree(root: Path, allowed_roots: list[Path]) -> tuple[list[PlannedRecord], dict[str, Any], list[dict[str, Any]]]:
    manifest_paths = [root] if root.is_file() else sorted(root.glob("**/manifest.json"))
    records: list[PlannedRecord] = []
    checksums: list[dict[str, Any]] = []
    referenced_paths: set[str] = set()
    for manifest_path in manifest_paths:
        if not manifest_path.is_file():
            continue
        checksums.extend(_source_checksum(manifest_path, "mineru_manifest"))
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        papers = data.get("papers") if isinstance(data, dict) else []
        if not isinstance(papers, list):
            continue
        for index, raw in enumerate(papers):
            if not isinstance(raw, dict):
                continue
            payload = {
                "run_date": data.get("run_date") or data.get("date") or manifest_path.parent.name,
                "manifest_path": str(manifest_path),
                **raw,
            }
            natural_key = _paper_natural_key(payload, f"manifest:{manifest_path}:{index}")
            artifacts = _paper_artifacts(payload, manifest_path.parent, allowed_roots)
            referenced_paths.update(artifact.resolved_path or "" for artifact in artifacts)
            records.append(
                PlannedRecord(
                    source="mineru_daily_tree",
                    record_type="paper",
                    natural_key=natural_key,
                    proposed_canonical_id=_paper_canonical_id(payload, natural_key),
                    payload=payload,
                    artifacts=artifacts,
                    source_path=str(manifest_path),
                )
            )

    for artifact_path in _orphan_mineru_artifacts(root, referenced_paths):
        artifact = _artifact(artifact_path, "tree_artifact", artifact_path.parent, allowed_roots)
        records.append(
            PlannedRecord(
                source="mineru_daily_tree",
                record_type="artifact",
                natural_key=f"artifact:{artifact.checksum or artifact.resolved_path}",
                proposed_canonical_id=f"artifact:{_short_hash(artifact.checksum or artifact.path)}",
                payload={"path": str(artifact_path), "reason": "unreferenced MinerU tree artifact"},
                artifacts=[artifact],
                source_path=str(artifact_path),
            )
        )
    return records, {
        "source": "mineru_daily_tree",
        "path": str(root),
        "available": root.exists(),
        "records_seen": len(records),
        "manifests_seen": len(manifest_paths),
    }, checksums


def _plan_patent_drafts(root: Path, allowed_roots: list[Path]) -> tuple[list[PlannedRecord], dict[str, Any], list[dict[str, Any]]]:
    files = sorted(path for path in root.glob("**/*") if path.is_file() and path.suffix.lower() in PATENT_SUFFIXES)
    grouped: dict[str, list[Path]] = {}
    for path in files:
        grouped.setdefault(path.stem, []).append(path)
    records: list[PlannedRecord] = []
    checksums: list[dict[str, Any]] = []
    for stem, paths in sorted(grouped.items()):
        artifacts = [_artifact(path, _patent_kind(path), root, allowed_roots) for path in sorted(paths)]
        checksums.extend({"kind": "patent_draft_artifact", "path": item.path, "checksum": item.checksum} for item in artifacts)
        title = _markdown_title(paths) or stem.replace("_", " ").replace("-", " ").strip()
        payload = {
            "title": title,
            "draft_stem": stem,
            "artifact_count": len(artifacts),
            "artifact_paths": [artifact.path for artifact in artifacts],
        }
        records.append(
            PlannedRecord(
                source="patent_disclosure_drafts",
                record_type="patent_draft",
                natural_key=f"patent-draft:{stem.lower()}",
                proposed_canonical_id=f"patent-draft:{_slug(title) or _short_hash(stem)}",
                payload=payload,
                artifacts=artifacts,
                source_path=str(root),
            )
        )
    return records, {
        "source": "patent_disclosure_drafts",
        "path": str(root),
        "available": root.exists(),
        "records_seen": len(records),
        "artifacts_seen": len(files),
    }, checksums


def _paper_artifacts(payload: dict[str, Any], base: Path, allowed_roots: list[Path]) -> list[PlannedArtifact]:
    artifacts = []
    for path_field in PAPER_PATH_FIELDS:
        value = payload.get(path_field)
        if value:
            artifacts.append(
                _artifact(Path(str(value)), _artifact_kind(path_field, value), base, allowed_roots, source_field=path_field)
            )
    return sorted(artifacts, key=lambda item: (item.kind, item.path))


def _artifact(
    path: Path,
    kind: str,
    base: Path,
    allowed_roots: list[Path],
    *,
    source_field: str = "path",
) -> PlannedArtifact:
    raw_path = str(path)
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=False)
    exists = resolved.is_file()
    checksum = _file_checksum(resolved) if exists else None
    size = resolved.stat().st_size if exists else None
    return PlannedArtifact(
        kind=kind,
        source_field=source_field,
        path=raw_path,
        resolved_path=str(resolved) if exists else None,
        exists=exists,
        within_allowed_roots=_within_roots(resolved, allowed_roots),
        checksum=checksum,
        size_bytes=size,
        media_type=mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
    )


def _actions_for_record(record: PlannedRecord) -> list[dict[str, Any]]:
    if record.record_type == "paper":
        actions = [{"op": "upsert_paper", "canonical_id": record.proposed_canonical_id}]
        actions.extend(
            {
                "op": "attach_artifact",
                "canonical_id": record.proposed_canonical_id,
                "artifact_checksum": artifact.checksum,
                "artifact_kind": artifact.kind,
            }
            for artifact in record.artifacts
            if artifact.exists
        )
        return actions
    if record.record_type == "patent_draft":
        return [{"op": "stage_patent_draft", "canonical_id": record.proposed_canonical_id}]
    return [{"op": "stage_artifact", "canonical_id": record.proposed_canonical_id}]


def _conflicts(records: list[PlannedRecord]) -> list[dict[str, Any]]:
    by_id: dict[str, list[PlannedRecord]] = {}
    for record in records:
        by_id.setdefault(record.proposed_canonical_id, []).append(record)
    conflicts = []
    for canonical_id, group in sorted(by_id.items()):
        payloads = {record.payload_checksum for record in group}
        sources = sorted({record.source for record in group})
        if len(group) > 1 and (len(payloads) > 1 or len(sources) > 1):
            conflicts.append(
                {
                    "type": "canonical_id_collision",
                    "severity": "review",
                    "proposed_canonical_id": canonical_id,
                    "sources": sources,
                    "import_ids": sorted(record.import_id for record in group),
                    "message": "Multiple legacy records map to the same canonical id; reconcile before applying writes.",
                }
            )
    return conflicts


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = {key: row[key] for key in sorted(row.keys())}
    for key, value in list(payload.items()):
        if key.endswith("_json"):
            payload[key.removesuffix("_json")] = _json_or_value(value)
    return payload


def _dify_natural_key(payload: dict[str, Any]) -> str:
    return _paper_natural_key(payload, str(payload.get("version_id") or payload.get("id") or "unknown"))


def _paper_natural_key(payload: dict[str, Any], fallback: str) -> str:
    doi = payload.get("doi")
    if doi:
        return f"doi:{str(doi).lower()}"
    arxiv_id = payload.get("arxiv_id")
    version_id = payload.get("version_id")
    if arxiv_id:
        version = payload.get("version")
        suffix = f"v{version}" if version and not str(arxiv_id).lower().endswith(f"v{version}") else ""
        return f"arxiv:{str(arxiv_id).lower()}{suffix}"
    if version_id:
        return f"arxiv:{str(version_id).lower()}"
    return str(fallback).lower()


def _paper_canonical_id(payload: dict[str, Any], natural_key: str) -> str:
    doi = payload.get("doi")
    if doi:
        return f"paper:doi:{_slug(str(doi).lower())}"
    arxiv_id = payload.get("arxiv_id") or payload.get("version_id")
    if arxiv_id:
        return f"paper:arxiv:{_strip_arxiv_version(str(arxiv_id).lower())}"
    title = " ".join(str(payload.get("title") or payload.get("canonical_title") or natural_key).lower().split())
    return f"paper:legacy:{_short_hash(title)}"


def _strip_arxiv_version(value: str) -> str:
    tail = value.rsplit("/", 1)[-1]
    if "v" in tail:
        base, suffix = value.rsplit("v", 1)
        if suffix.isdigit():
            return base
    return value


def _artifact_kind(field: str, value: Any) -> str:
    suffix = Path(str(value)).suffix.lower()
    if suffix == ".pdf" or "pdf" in field:
        return "pdf"
    if field == "summary_path":
        return "summary_markdown"
    if field == "review_path":
        return "review"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    return "artifact"


def _orphan_mineru_artifacts(root: Path, referenced_paths: set[str]) -> list[Path]:
    if not root.exists() or root.is_file():
        return []
    candidates = []
    for suffix in ("*.md", "*.json", "*.pdf"):
        candidates.extend(root.glob(f"**/{suffix}"))
    return sorted(path.resolve() for path in candidates if path.is_file() and str(path.resolve()) not in referenced_paths and path.name != "manifest.json")


def _patent_kind(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return "patent_disclosure_docx"
    return "patent_disclosure_markdown"


def _markdown_title(paths: list[Path]) -> str | None:
    for path in sorted(paths):
        if path.suffix.lower() not in {".md", ".markdown"}:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
    return None


def _allowed_roots(*paths: Any) -> list[Path]:
    explicit = paths[-1] if paths else None
    roots: list[Path] = []
    for value in paths[:-1]:
        if value is None:
            continue
        path = Path(value).expanduser().resolve()
        roots.append(path.parent if path.is_file() else path)
    if explicit:
        roots.extend(Path(value).expanduser().resolve() for value in explicit)
    return sorted(set(roots), key=str)


def _within_roots(path: Path, roots: list[Path]) -> bool:
    if not roots:
        return True
    return any(path == root or root in path.parents for root in roots)


def _source_checksum(path: Path, kind: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [{"kind": kind, "path": str(path), "checksum": _file_checksum(path)}]


def _file_checksum(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{HASH_PREFIX}{digest.hexdigest()}"


def _json_checksum(value: Any) -> str:
    import hashlib

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{HASH_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


def _short_hash(value: str) -> str:
    return _json_checksum(value).removeprefix(HASH_PREFIX)[:16]


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "-" for char in value]
    return "-".join(part for part in "".join(chars).split("-") if part)[:96]


def _json_or_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


if __name__ == "__main__":
    raise SystemExit(main())
