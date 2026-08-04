"""Patent disclosure output service built on approved invention candidates."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters import FileArtifactStore, PatentEngineAdapter, TechnicalCard
from .adapters.patent import ensure_path_within_root, safe_patent_filename
from .database import dumps, loads
from .models import (
    InventionCandidate,
    PatentDraft,
    PatentStageRunCreate,
    PatentStageRunUpdate,
)
from .repository import ConflictError, Repository, new_id, stable_hash, utcnow


@dataclass(frozen=True)
class PatentArtifactBundle:
    markdown_artifact: dict[str, Any]
    docx_artifact: dict[str, Any]


@dataclass(frozen=True)
class PatentDisclosureOutput:
    candidate: InventionCandidate
    draft: PatentDraft
    artifacts: PatentArtifactBundle
    request_hash: str
    version_label: str


class PatentOutputService:
    """Generate patent disclosure Markdown + DOCX for approved candidates.

    The service is intentionally database-backed but API-agnostic. It can be
    called from FastAPI routes, CLI commands, or background jobs without
    requiring a specific presentation layer.
    """

    def __init__(
        self,
        conn,
        *,
        output_root: Path | str | None = None,
        patent_adapter: PatentEngineAdapter | None = None,
    ) -> None:
        self.conn = conn
        self.repo = Repository(conn)
        self.output_root = Path(output_root or Path.cwd() / "exports" / "patent_drafts").expanduser()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.artifact_store = FileArtifactStore(self.output_root / "artifacts")
        self.patent_adapter = patent_adapter or PatentEngineAdapter(
            allowed_output_root=str(self.output_root)
        )

    def generate_outputs(
        self,
        candidate_id: str,
        *,
        case_name: str | None = None,
        protection_focus: str = "",
        notes: str = "",
        version_label: str | None = None,
    ) -> PatentDisclosureOutput:
        candidate = self.repo.get_invention_candidate(candidate_id)
        if candidate.status != "approved":
            raise ConflictError("Patent output generation requires human approval first")
        self._ensure_prior_art_gate(candidate)
        self._ensure_fact_provenance_gate(candidate)

        request = {
            "candidate_id": candidate_id,
            "case_name": case_name or candidate.title,
            "protection_focus": protection_focus,
            "notes": notes,
        }
        request_hash = stable_hash(request)
        existing_draft = self._find_existing_request_draft(candidate.id, request_hash)
        if existing_draft and version_label is None:
            existing_bundle = self._existing_bundle(existing_draft.id)
            if existing_bundle:
                self._attach_artifacts(existing_draft.id, existing_bundle)
                self._complete_output_stages(candidate.id, existing_draft, existing_bundle, request)
                return PatentDisclosureOutput(
                    candidate=candidate,
                    draft=existing_draft,
                    artifacts=existing_bundle,
                    request_hash=request_hash,
                    version_label=existing_draft.version_label,
                )
        resolved_version_label = version_label or self._timestamped_version_label(request["case_name"])
        # All DB writes (draft, stage runs, artifact registry rows) happen on the
        # same connection and are committed/rolled-back as one unit. Using the
        # sqlite3 connection as a context manager makes the transaction boundary
        # explicit: any failure below rolls back the draft+stage+artifact rows
        # together, so a "draft exists but files never landed" half-state cannot
        # survive. Artifact files are checksum-addressed and idempotent, so any
        # file already persisted is safe to keep on a retry.
        with self.conn:
            draft = self._upsert_draft(candidate, request, request_hash, resolved_version_label)
            builder_stage = self._start_builder_stage(candidate.id, draft, request)
            existing_bundle = self._existing_bundle(draft.id)
            if existing_bundle:
                self._attach_artifacts(draft.id, existing_bundle)
                self._complete_output_stages(candidate.id, draft, existing_bundle, request)
                return PatentDisclosureOutput(
                    candidate=candidate,
                    draft=draft,
                    artifacts=existing_bundle,
                    request_hash=request_hash,
                    version_label=resolved_version_label,
                )
            try:
                bundle = self._render_and_register(candidate, draft, request)
            except Exception as exc:
                if builder_stage.status == "running":
                    self.repo.update_patent_stage_run(
                        builder_stage.id,
                        PatentStageRunUpdate(
                            status="failed",
                            output={"error": str(exc), "draft_id": draft.id},
                        ),
                    )
                raise
            self._attach_artifacts(draft.id, bundle)
            self._complete_output_stages(candidate.id, draft, bundle, request)
            return PatentDisclosureOutput(
                candidate=candidate,
                draft=draft,
                artifacts=bundle,
                request_hash=request_hash,
                version_label=resolved_version_label,
            )

    def _start_builder_stage(
        self,
        candidate_id: str,
        draft: PatentDraft,
        request: dict[str, Any],
    ):
        existing = {item.stage: item for item in self.repo.list_patent_stage_runs(candidate_id)}.get(
            "builder"
        )
        if existing is None:
            return self.repo.record_patent_stage_run(
                candidate_id,
                PatentStageRunCreate(
                    stage="builder",
                    status="running",
                    patent_draft_id=draft.id,
                    idempotency_key=f"draft:{draft.id}:builder",
                    input=request,
                ),
            )
        if existing.status == "failed":
            return self.repo.update_patent_stage_run(
                existing.id,
                PatentStageRunUpdate(status="running", output={}),
            )
        return existing

    def _complete_output_stages(
        self,
        candidate_id: str,
        draft: PatentDraft,
        bundle: PatentArtifactBundle,
        request: dict[str, Any],
    ) -> None:
        stages = {item.stage: item for item in self.repo.list_patent_stage_runs(candidate_id)}
        builder = stages.get("builder")
        builder_output = {
            "draft_id": draft.id,
            "version_label": draft.version_label,
            "markdown_artifact_id": bundle.markdown_artifact["artifact_id"],
            "docx_artifact_id": bundle.docx_artifact["artifact_id"],
        }
        if builder is None:
            builder = self.repo.record_patent_stage_run(
                candidate_id,
                PatentStageRunCreate(
                    stage="builder",
                    status="succeeded",
                    patent_draft_id=draft.id,
                    artifact_id=bundle.markdown_artifact["artifact_id"],
                    idempotency_key=f"draft:{draft.id}:builder",
                    input=request,
                    output=builder_output,
                ),
            )
        elif builder.status != "succeeded":
            self.repo.update_patent_stage_run(
                builder.id,
                PatentStageRunUpdate(
                    status="succeeded",
                    artifact_id=bundle.markdown_artifact["artifact_id"],
                    output=builder_output,
                ),
            )

        stages = {item.stage: item for item in self.repo.list_patent_stage_runs(candidate_id)}
        if "self_check" not in stages:
            checked_draft = self.repo.get_patent_draft(draft.id)
            self.repo.record_patent_stage_run(
                candidate_id,
                PatentStageRunCreate(
                    stage="self_check",
                    status="succeeded",
                    patent_draft_id=draft.id,
                    artifact_id=bundle.markdown_artifact["artifact_id"],
                    idempotency_key=f"draft:{draft.id}:self-check",
                    input={"draft_id": draft.id},
                    output={"self_check": checked_draft.self_check, **builder_output},
                ),
            )

    def _upsert_draft(
        self,
        candidate: InventionCandidate,
        request: dict[str, Any],
        request_hash: str,
        version_label: str,
    ) -> PatentDraft:
        row = self.conn.execute(
            """
            SELECT * FROM patent_draft
            WHERE invention_candidate_id = ? AND version_label = ?
            """,
            (candidate.id, version_label),
        ).fetchone()
        now = utcnow()
        markdown = self._build_markdown(candidate, request)
        fact_coverage = self._fact_provenance_coverage(candidate)
        self_check = {
            "legal_notice": "技术交底书草稿，不构成法律意义上的新颖性、创造性或授权结论。",
            "request_hash": request_hash,
            "candidate_id": candidate.id,
            "fact_provenance_coverage": fact_coverage,
            "disclosure_structure": self._disclosure_structure_check(markdown),
        }
        if row:
            existing_check = loads(row["self_check_json"], {})
            if existing_check.get("request_hash") and existing_check.get("request_hash") != request_hash:
                raise ConflictError("Patent draft version_label already exists for a different request")
            return self.repo.get_patent_draft(row["id"])

        draft_id = new_id("draft")
        self.conn.execute(
            """
            INSERT INTO patent_draft (
                id, invention_candidate_id, case_name, version_label,
                status, markdown, self_check_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'generated', ?, ?, ?, ?)
            """,
            (
                draft_id,
                candidate.id,
                request["case_name"],
                version_label,
                markdown,
                dumps(self_check),
                now,
                now,
            ),
        )
        return self.repo.get_patent_draft(draft_id)

    def _build_markdown(self, candidate: InventionCandidate, request: dict[str, Any]) -> str:
        cards = self._technical_cards(candidate)
        paired_candidate = self.patent_adapter.build_candidate(cards, title=request["case_name"])
        if paired_candidate.gate_status != "ok":
            reasons = "; ".join(paired_candidate.gate_reasons)
            raise ConflictError(f"Patent adapter gate rejected candidate: {reasons}")
        markdown = self.patent_adapter.render_disclosure_markdown(paired_candidate)
        markdown = markdown.replace("<!-- PRIOR_ART_RECORDS -->", self._prior_art_markdown(candidate.id))
        if request.get("protection_focus"):
            markdown += f"\n\n## 额外保护重点\n\n{request['protection_focus']}\n"
        if request.get("notes"):
            markdown += f"\n\n## 修订备注\n\n{request['notes']}\n"
        markdown += self._provenance_markdown(candidate)
        return markdown

    def _prior_art_markdown(self, candidate_id: str) -> str:
        rows = self.conn.execute(
            """
            SELECT source_type, source, title, publication_number, url,
                   abstract, analysis_basis, limitations
            FROM prior_art_record
            WHERE invention_candidate_id = ?
            ORDER BY source_type DESC, created_at ASC
            """,
            (candidate_id,),
        ).fetchall()
        if not rows:
            return "- 暂无可审计查新记录；如通过人工覆盖继续生成，必须在定稿前补充。"
        lines = []
        for row in rows:
            title = _markdown_text(row["title"] or "未命名公开文献")
            publication = _markdown_text(row["publication_number"] or "编号待核验")
            url = str(row["url"] or "").strip()
            abstract = _markdown_text(str(row["abstract"] or "")[:500])
            source = _markdown_text(row["source"] or row["source_type"] or "unknown")
            lines.append(
                f"- [{title}]({url})（{publication}，来源：{source}）\n"
                f"  - 分析依据：{_markdown_text(row['analysis_basis'] or 'abstract')}\n"
                f"  - 摘要要点：{abstract}\n"
                f"  - 局限：{_markdown_text(row['limitations'] or '需人工核验全文与权利要求')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _disclosure_structure_check(markdown: str) -> dict[str, Any]:
        required_sections = [
            "## 一、现有技术及其问题",
            "## 二、发明目的",
            "## 三、技术方案",
            "## 四、有益效果",
            "## 五、建议保护点",
            "## 六、组合门禁、证据与风险",
            "## 七、实验验证与可选实施例",
            "## 八、附图清单",
        ]
        missing = [section for section in required_sections if section not in markdown]
        mermaid_count = markdown.count("```mermaid")
        protection_block = markdown.split("## 五、建议保护点", 1)[-1].split("## 六、", 1)[0]
        protection_points = sum(
            1 for line in protection_block.splitlines() if line.strip()[:1].isdigit()
        )
        return {
            "passed": not missing and mermaid_count >= 2 and protection_points >= 4,
            "missing_sections": missing,
            "mermaid_diagrams": mermaid_count,
            "protection_points": protection_points,
            "prior_art_embedded": "<!-- PRIOR_ART_RECORDS -->" not in markdown,
            "provenance_appendix": "## 事实级来源与假设标注" in markdown,
        }

    def _technical_cards(self, candidate: InventionCandidate) -> list[TechnicalCard]:
        cards: list[TechnicalCard] = []
        for index, source in enumerate(candidate.sources, start=1):
            paper_id = source.paper_id
            paper_title = source.paper_id or source.paper_version_id or f"source-{index}"
            if source.paper_version_id:
                version = self.repo.get_paper_version(source.paper_version_id)
                paper = self.repo.get_paper(version.paper_id)
                paper_id = paper.id
                paper_title = paper.canonical_title
            elif paper_id:
                paper = self.repo.get_paper(paper_id)
                paper_title = paper.canonical_title
            method = "；".join(
                item
                for item in [
                    candidate.integration_mechanism.strip(),
                    source.contribution.strip(),
                    source.paper_version_id or source.paper_id or "",
                ]
                if item
            )
            problem = candidate.problem_statement.strip() or candidate.technical_effects.strip() or candidate.title
            evidence = [f"candidate:{candidate.id}", f"source:{index}"]
            if source.paper_id:
                evidence.append(f"paper:{source.paper_id}")
            if source.paper_version_id:
                evidence.append(f"paper_version:{source.paper_version_id}")
            evidence.extend(
                _evidence_text(item)
                for item in candidate.evidence
            )
            cards.append(
                TechnicalCard(
                    card_id=source.technical_card_id or f"{candidate.id}:{index}",
                    paper_id=paper_id or "",
                    title=paper_title,
                    technical_problem=problem,
                    method=method,
                    evidence=tuple(dict.fromkeys(evidence)),
                    risks=tuple(filter(None, [candidate.risk_notes.strip(), source.contribution.strip()])),
                )
            )
        if len(cards) < 2:
            raise ConflictError("Patent output generation requires at least two source papers")
        return cards

    def _render_and_register(
        self,
        candidate: InventionCandidate,
        draft: PatentDraft,
        request: dict[str, Any],
    ) -> PatentArtifactBundle:
        with tempfile.TemporaryDirectory(
            prefix="research-hub-patent-",
            dir=str(self.output_root),
        ) as tmp_dir:
            tmp_root = Path(tmp_dir)
            file_stem = draft.version_label
            md_path = ensure_path_within_root(
                tmp_root / safe_patent_filename(file_stem, extension="md"),
                tmp_root,
            )
            docx_path = ensure_path_within_root(
                tmp_root / safe_patent_filename(file_stem, extension="docx"),
                tmp_root,
            )
            md_path.write_text(draft.markdown, encoding="utf-8")
            export_result = self.patent_adapter.export_docx(md_path, docx_path)
            if export_result.status != "ok":
                raise ConflictError(
                    f"Patent DOCX export failed: {export_result.message}"
                )

            markdown_artifact = self._register_file_artifact(
                draft.id,
                kind="patent_disclosure_markdown",
                source_path=md_path,
                metadata={
                    "candidate_id": candidate.id,
                    "version_label": draft.version_label,
                    "request": request,
                },
            )
            docx_artifact = self._register_file_artifact(
                draft.id,
                kind="patent_disclosure_docx",
                source_path=docx_path,
                metadata={
                    "candidate_id": candidate.id,
                    "version_label": draft.version_label,
                    "request": request,
                },
            )
        return PatentArtifactBundle(markdown_artifact=markdown_artifact, docx_artifact=docx_artifact)

    def _register_file_artifact(
        self,
        draft_id: str,
        *,
        kind: str,
        source_path: Path,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        record = self.artifact_store.register_existing(source_path, kind=kind, metadata=metadata, copy=True)
        if record.status != "ok":
            raise ConflictError(record.message)
        artifact = record.data["artifact"]
        stored_metadata = {
            **metadata,
            "store_artifact_id": artifact["artifact_id"],
            "path": artifact["path"],
            "size_bytes": artifact["size_bytes"],
            "sha256": artifact["sha256"],
        }
        registry_artifact_id = self._upsert_artifact_row(
            draft_id=draft_id,
            artifact_type=kind,
            uri=f"file://{artifact['path']}",
            media_type=str(artifact["content_type"]),
            checksum=str(artifact["sha256"]),
            metadata=stored_metadata,
        )
        return {
            **artifact,
            "artifact_id": registry_artifact_id,
            "store_artifact_id": artifact["artifact_id"],
        }

    def _existing_bundle(self, draft_id: str) -> PatentArtifactBundle | None:
        rows = self.conn.execute(
            """
            SELECT * FROM artifact
            WHERE patent_draft_id = ?
              AND artifact_type IN ('patent_disclosure_markdown', 'patent_disclosure_docx')
            """,
            (draft_id,),
        ).fetchall()
        by_type = {row["artifact_type"]: row for row in rows}
        if "patent_disclosure_markdown" not in by_type or "patent_disclosure_docx" not in by_type:
            return None
        return PatentArtifactBundle(
            markdown_artifact=_artifact_dict(by_type["patent_disclosure_markdown"]),
            docx_artifact=_artifact_dict(by_type["patent_disclosure_docx"]),
        )

    def _upsert_artifact_row(
        self,
        *,
        draft_id: str,
        artifact_type: str,
        uri: str,
        media_type: str,
        checksum: str,
        metadata: dict[str, Any],
    ) -> str:
        existing = self.conn.execute(
            """
            SELECT id FROM artifact
            WHERE patent_draft_id = ? AND artifact_type = ? AND uri = ?
            """,
            (draft_id, artifact_type, uri),
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE artifact
                SET media_type = ?, checksum = ?, metadata_json = ?
                WHERE id = ?
                """,
                (media_type, checksum, dumps(metadata), existing["id"]),
            )
            return str(existing["id"])
        artifact_id = new_id("art")
        self.conn.execute(
            """
            INSERT INTO artifact (
                id, patent_draft_id, artifact_type, uri, media_type, checksum, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                draft_id,
                artifact_type,
                uri,
                media_type,
                checksum,
                dumps(metadata),
            ),
        )
        return artifact_id

    def _attach_artifacts(self, draft_id: str, bundle: PatentArtifactBundle) -> None:
        draft = self.repo.get_patent_draft(draft_id)
        self_check = dict(draft.self_check)
        self_check["artifacts"] = {
            "markdown": {
                "artifact_id": bundle.markdown_artifact["artifact_id"],
                "sha256": bundle.markdown_artifact["sha256"],
                "path": bundle.markdown_artifact["path"],
            },
            "docx": {
                "artifact_id": bundle.docx_artifact["artifact_id"],
                "sha256": bundle.docx_artifact["sha256"],
                "path": bundle.docx_artifact["path"],
            },
        }
        self.conn.execute(
            """
            UPDATE patent_draft
            SET self_check_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (dumps(self_check), utcnow(), draft_id),
        )

    @staticmethod
    def _ensure_prior_art_gate(candidate: InventionCandidate) -> None:
        prior_art = candidate.gate.get("prior_art") if isinstance(candidate.gate, dict) else None
        if not isinstance(prior_art, dict) or prior_art.get("status") not in {"succeeded", "overridden"}:
            raise ConflictError(
                "Patent output generation requires succeeded prior-art check or recorded prior-art override"
            )

    def _find_existing_request_draft(
        self, candidate_id: str, request_hash: str
    ) -> PatentDraft | None:
        rows = self.conn.execute(
            """
            SELECT * FROM patent_draft
            WHERE invention_candidate_id = ?
            ORDER BY created_at ASC
            """,
            (candidate_id,),
        ).fetchall()
        for row in rows:
            self_check = loads(row["self_check_json"], {})
            if self_check.get("request_hash") == request_hash:
                return self.repo.get_patent_draft(row["id"])
        return None

    @staticmethod
    def _timestamped_version_label(case_name: str) -> str:
        return f"{case_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    @staticmethod
    def _fact_provenance_coverage(candidate: InventionCandidate) -> dict[str, Any]:
        required_fields = {
            "problem_statement",
            "integration_mechanism",
            "coupling_interface",
            "data_or_control_flow",
            "why_not_juxtaposition",
            "expected_joint_effect",
            "technical_effects",
        }
        covered: set[str] = set()
        hypotheses: set[str] = set()
        for anchor in candidate.evidence:
            fields = {
                str(getattr(anchor, "report_field", "") or "").strip(),
                str(getattr(anchor, "section", "") or "").strip(),
            }
            fields = {field for field in fields if field}
            if anchor.kind in {"fact", "hypothesis"}:
                covered.update(fields)
            if anchor.kind == "hypothesis":
                hypotheses.update(fields)
        missing = sorted(required_fields - covered)
        return {
            "required_fields": sorted(required_fields),
            "covered_fields": sorted(covered & required_fields),
            "missing_fields": missing,
            "coverage_percent": 100 if not missing else round(
                100 * (len(required_fields) - len(missing)) / len(required_fields), 2
            ),
            "hypothesis_fields": sorted(hypotheses & required_fields),
        }

    def _ensure_fact_provenance_gate(self, candidate: InventionCandidate) -> None:
        coverage = self._fact_provenance_coverage(candidate)
        if coverage["missing_fields"]:
            raise ConflictError(
                "Patent output generation requires 100% fact-level provenance coverage; missing: "
                + ", ".join(coverage["missing_fields"])
            )
        hypotheses = set(coverage["hypothesis_fields"])
        if not {"expected_joint_effect", "technical_effects"} & hypotheses:
            raise ConflictError("Unverified technical effects must be marked as hypothesis")

    @staticmethod
    def _provenance_markdown(candidate: InventionCandidate) -> str:
        rows = []
        for anchor in candidate.evidence:
            field = anchor.report_field or anchor.section or "general"
            rows.append(f"- `{field}` [{anchor.kind}] {anchor.source}: {anchor.note}")
        if not rows:
            return ""
        return "\n\n## 事实级来源与假设标注\n\n" + "\n".join(rows) + "\n"


def generate_patent_outputs(
    conn,
    candidate_id: str,
    *,
    case_name: str | None = None,
    protection_focus: str = "",
    notes: str = "",
    version_label: str | None = None,
    output_root: Path | str | None = None,
    patent_adapter: PatentEngineAdapter | None = None,
) -> PatentDisclosureOutput:
    return PatentOutputService(
        conn,
        output_root=output_root,
        patent_adapter=patent_adapter,
    ).generate_outputs(
        candidate_id,
        case_name=case_name,
        protection_focus=protection_focus,
        notes=notes,
        version_label=version_label,
    )


def _evidence_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if hasattr(item, "model_dump"):
        return dumps(item.model_dump(mode="json"))
    return dumps(item)


def _markdown_text(value: Any) -> str:
    return str(value).replace("[", "［").replace("]", "］").replace("\n", " ").strip()


def _artifact_dict(row: Any) -> dict[str, Any]:
    metadata = loads(row["metadata_json"], {})
    path = str(row["uri"]).removeprefix("file://")
    size_bytes = metadata.get("size_bytes")
    if size_bytes is None:
        artifact_path = Path(path)
        size_bytes = artifact_path.stat().st_size if artifact_path.is_file() else 0
    return {
        "artifact_id": row["id"],
        "store_artifact_id": metadata.get("store_artifact_id"),
        "kind": row["artifact_type"],
        "path": path,
        "size_bytes": size_bytes,
        "sha256": row["checksum"],
        "content_type": row["media_type"],
        "metadata": metadata,
    }
