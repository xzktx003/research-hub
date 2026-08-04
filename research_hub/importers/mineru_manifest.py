"""Importer for historical MinerU daily-paper manifest files."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ImportRecord:
    kind: str
    natural_key: str
    payload: dict[str, Any]
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def import_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.kind}:{self.natural_key}"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "kind": self.kind,
            "natural_key": self.natural_key,
            "payload": self.payload,
            "artifacts": self.artifacts,
        }


class MinerUManifestImporter:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    def iter_manifests(self) -> Iterable[Path]:
        if self.root.is_file() and self.root.name == "manifest.json":
            yield self.root
            return
        yield from sorted(self.root.glob("**/manifest.json"))

    def import_records(self) -> list[ImportRecord]:
        records: list[ImportRecord] = []
        seen: set[str] = set()
        for manifest_path in self.iter_manifests():
            for record in self._records_from_manifest(manifest_path):
                if record.natural_key in seen:
                    continue
                seen.add(record.natural_key)
                records.append(record)
        return records

    def _records_from_manifest(self, manifest_path: Path) -> list[ImportRecord]:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        papers = data.get("papers") or []
        if not isinstance(papers, list):
            return []
        records: list[ImportRecord] = []
        for index, paper in enumerate(papers):
            if not isinstance(paper, dict):
                continue
            key = _paper_key(paper, manifest_path, index)
            payload = {
                "source": "mineru_manifest",
                "run_date": data.get("run_date") or manifest_path.parent.name,
                "manifest_path": str(manifest_path),
                "source_categories": paper.get("source_categories") or [],
                "topics": paper.get("topics") or [],
                "title": paper.get("title") or "",
                "authors": paper.get("authors") or [],
                "abstract": paper.get("abstract") or "",
                "arxiv_id": paper.get("arxiv_id"),
                "doi": paper.get("doi"),
                "pdf_url": paper.get("pdf_url"),
                "landing_url": paper.get("arxiv_page") or paper.get("official_page"),
                "first_public_date": paper.get("first_public_date"),
                "updated_date": paper.get("updated_date"),
                "processing_status": paper.get("processing_status") or paper.get("status"),
                "download_status": paper.get("download_status"),
                "mineru_status": paper.get("mineru_status"),
                "error": paper.get("error"),
                "raw": paper,
            }
            records.append(
                ImportRecord(
                    kind="paper",
                    natural_key=key,
                    payload=payload,
                    artifacts=_artifact_refs(paper),
                )
            )
        return records


def _paper_key(paper: dict[str, Any], manifest_path: Path, index: int) -> str:
    if paper.get("doi"):
        return f"doi:{str(paper['doi']).lower()}"
    if paper.get("arxiv_id"):
        return f"arxiv:{str(paper['arxiv_id']).lower()}"
    if paper.get("id"):
        return str(paper["id"]).lower()
    title = " ".join(str(paper.get("title") or "").lower().split())
    return f"manifest:{manifest_path}:{index}:{title}"


def _artifact_refs(paper: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for key, kind in (
        ("local_pdf_path", "pdf"),
        ("mineru_markdown_path", "mineru_markdown"),
        ("summary_path", "summary_markdown"),
    ):
        value = paper.get(key)
        if value:
            path = Path(str(value)).expanduser()
            refs.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "exists": path.is_file(),
                    "source_field": key,
                }
            )
    return refs


def records_to_jsonl(records: Iterable[ImportRecord]) -> str:
    return "\n".join(json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True) for record in records)
