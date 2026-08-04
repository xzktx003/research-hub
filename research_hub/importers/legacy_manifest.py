"""Persist legacy daily-paper manifests into the Research Hub database."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..database import Database, dumps
from ..models import (
    ArtifactCreate,
    PaperCreate,
    PaperIdentifier,
    PaperSourceHitCreate,
    PaperVersionCreate,
)
from ..repository import Repository


TOPIC_MAP = {
    "quantization": "aif-02",
    "pruning": "aif-02",
    "distillation": "aif-02",
    "moe": "aif-01",
    "speculative_decoding": "aif-03",
    "inference": "aif-04",
    "serving": "aif-04",
    "pd_disaggregation": "aif-04",
    "kernel": "aif-05",
    "compiler": "aif-05",
    "distributed_training": "aif-06",
    "hardware": "aif-07",
}


def import_manifest(
    manifest_path: str | Path,
    *,
    database_path: str | Path,
    dry_run: bool = False,
) -> dict[str, int]:
    """Import one manifest idempotently and return deterministic counters."""

    manifest = Path(manifest_path).expanduser().resolve()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    papers = [item for item in data.get("papers", []) if isinstance(item, dict)]
    result = {
        "papers_seen": len(papers),
        "papers_created": 0,
        "papers_matched": 0,
        "artifacts_created": 0,
        "reports_created": 0,
    }
    if dry_run:
        return result

    database = Database(Path(database_path).expanduser().resolve())
    database.initialize()
    with database.connect() as conn:
        repository = Repository(conn)
        for index, raw in enumerate(papers):
            identifiers = _identifiers(raw, manifest, index)
            existing_id = repository.find_paper_by_identifiers(identifiers)
            publication_date = _parse_date(raw.get("first_public_date") or data.get("run_date"))
            slug = str(raw.get("slug") or "").strip()
            local_files = _resolve_files(manifest.parent, raw, slug)
            topics = _topics(raw)
            version_label = _version_label(raw)
            paper = repository.create_paper(
                PaperCreate(
                    canonical_title=str(raw.get("title") or f"Untitled paper {index + 1}"),
                    abstract=str(raw.get("abstract") or ""),
                    first_publication_date=publication_date,
                    status="analyzed" if local_files.get("summary") else "parsed" if local_files.get("markdown") else "discovered",
                    identifiers=identifiers,
                    topics=topics,
                    metadata={
                        "authors": raw.get("authors") or [],
                        "legacy_manifest": str(manifest),
                        "legacy_record": raw,
                        "real_source_data": True,
                    },
                    version=PaperVersionCreate(
                        version_label=version_label,
                        source=str(raw.get("source") or "mineru_manifest"),
                        source_version_id=str(raw.get("arxiv_id") or raw.get("id") or version_label),
                        publication_date=publication_date,
                        pdf_url=raw.get("pdf_url"),
                        pdf_checksum=_checksum(local_files.get("pdf")),
                        metadata={"manifest_path": str(manifest), "slug": slug},
                    ),
                    source_hit=PaperSourceHitCreate(
                        source="mineru_daily_manifest",
                        query=",".join(raw.get("source_categories") or raw.get("topics") or []),
                        rank=index + 1,
                        hit_date=_parse_date(data.get("run_date")),
                        raw_summary={"manifest": str(manifest), "record_type": raw.get("record_type")},
                    ),
                )
            )
            result["papers_matched" if existing_id else "papers_created"] += 1
            version_id = paper.current_version_id
            if not version_id:
                continue
            for kind, path in local_files.items():
                if kind == "summary":
                    continue
                artifact_type = "pdf" if kind == "pdf" else "markdown_original"
                if _artifact_exists(conn, version_id, artifact_type, path):
                    continue
                repository.create_artifact_for_version(
                    version_id,
                    ArtifactCreate(
                        artifact_type=artifact_type,
                        uri=str(path),
                        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        checksum=_checksum(path),
                        metadata={"legacy_manifest": str(manifest), "real_file": True},
                    ),
                )
                result["artifacts_created"] += 1
            summary = local_files.get("summary")
            if summary and _create_report(conn, version_id, summary):
                result["reports_created"] += 1
    return result


def _identifiers(raw: dict[str, Any], manifest: Path, index: int) -> list[PaperIdentifier]:
    values: list[PaperIdentifier] = []
    if raw.get("arxiv_id"):
        values.append(PaperIdentifier(type="arxiv", value=str(raw["arxiv_id"]).split("v", 1)[0]))
    if raw.get("doi"):
        values.append(PaperIdentifier(type="doi", value=str(raw["doi"]).lower()))
    if raw.get("id"):
        values.append(PaperIdentifier(type="legacy", value=str(raw["id"])))
    if not values:
        title = " ".join(str(raw.get("title") or "").lower().split())
        digest = hashlib.sha256(f"{manifest}:{index}:{title}".encode()).hexdigest()[:24]
        values.append(PaperIdentifier(type="legacy", value=digest))
    return values


def _topics(raw: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    text = " ".join(str(value).lower() for value in raw.get("topics") or [])
    for key, topic_id in TOPIC_MAP.items():
        if key in text and topic_id not in selected:
            selected.append(topic_id)
    if not selected:
        selected.append("aif-08")
    return selected


def _version_label(raw: dict[str, Any]) -> str:
    value = str(raw.get("id") or raw.get("arxiv_id") or "v1")
    if "v" in value.rsplit("/", 1)[-1]:
        suffix = value.rsplit("v", 1)[-1]
        if suffix.isdigit():
            return f"v{suffix}"
    return "v1"


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _resolve_files(root: Path, raw: dict[str, Any], slug: str) -> dict[str, Path]:
    fields = {
        "pdf": raw.get("local_pdf_path") or raw.get("pdf_path"),
        "markdown": raw.get("mineru_markdown_path") or raw.get("markdown_path"),
        "summary": raw.get("summary_path"),
    }
    resolved: dict[str, Path] = {}
    for kind, value in fields.items():
        if value:
            direct = Path(str(value)).expanduser()
            if direct.is_file():
                resolved[kind] = direct.resolve()
                continue
        if kind == "summary" and slug:
            candidate = root / "summaries" / f"{slug}.md"
            if candidate.is_file():
                resolved[kind] = candidate.resolve()
                continue
        pattern = f"{slug}.pdf" if kind == "pdf" and slug else f"{slug}.md" if slug else ""
        if not pattern:
            continue
        candidates = [p for p in root.rglob(pattern) if p.is_file()]
        if kind == "pdf":
            candidates = [p for p in candidates if "mineru_output" not in p.parts] or candidates
        elif kind == "markdown":
            candidates = [p for p in candidates if "summaries" not in p.parts] or candidates
        if candidates:
            resolved[kind] = sorted(candidates, key=lambda path: (len(path.parts), str(path)))[0].resolve()
    return resolved


def _checksum(path: Path | None) -> str | None:
    if not path or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_exists(conn: sqlite3.Connection, version_id: str, kind: str, path: Path) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM artifact WHERE paper_version_id = ? AND artifact_type = ? AND uri = ?",
            (version_id, kind, str(path)),
        ).fetchone()
    )


def _create_report(conn: sqlite3.Connection, version_id: str, summary_path: Path) -> bool:
    if conn.execute("SELECT 1 FROM paper_report WHERE paper_version_id = ?", (version_id,)).fetchone():
        return False
    text = summary_path.read_text(encoding="utf-8", errors="replace")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO paper_report (
            id, paper_version_id, summary, motivation, method, experiments,
            results, innovation, limitations, engineering_value,
            reproduction_plan, score_json, evidence_json, created_at, updated_at
        ) VALUES (?, ?, ?, '', '', '', '', '', '', '', '', ?, ?, ?, ?)
        """,
        (
            f"report-{uuid.uuid4()}",
            version_id,
            text,
            dumps({"source": "legacy_summary"}),
            dumps([
                {
                    "kind": "fact",
                    "source": str(summary_path),
                    "note": "Imported from an existing daily-paper research report.",
                }
            ]),
            now,
            now,
        ),
    )
    return True
