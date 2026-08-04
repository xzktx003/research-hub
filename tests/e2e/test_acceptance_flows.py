from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from research_hub.adapters import AdapterResult
from research_hub.models import (
    ArtifactCreate,
    CandidateApproveRequest,
    DiscoveryRunCreate,
    InventionCandidateCreate,
    InventionSourceRef,
    PaperCreate,
    PaperIdentifier,
    PaperSelectRequest,
    PaperVersionCreate,
    VersionActionRequest,
)
from research_hub.patent_service import PatentOutputService
from research_hub.repository import ConflictError, Repository
from research_hub.services import ResearchJobService


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


class StaticDiscoveryAdapter:
    def __init__(self, papers: list[dict[str, Any]]) -> None:
        self.papers = papers
        self.requests = []

    def discover(self, topic):
        self.requests.append(topic)
        return AdapterResult.ok("offline discovery fixture", papers=self.papers)


class AsyncMinerUPackageAdapter:
    def __init__(self, *, markdown: str, structured: dict[str, Any], resources: dict[str, bytes] | None = None) -> None:
        self.markdown = markdown
        self.structured = structured
        self.resources = resources or {}
        self.submit_requests = []
        self.status_requests = []
        self.fetch_requests = []

    def submit(self, request):
        self.submit_requests.append(request)
        return AdapterResult.ok("submitted to official MinerU async API", response={"task_id": "mineru-acceptance"})

    def status(self, job_id: str):
        self.status_requests.append(job_id)
        return AdapterResult.ok("completed", response={"status": "completed"})

    def fetch_result(self, job_id: str, output_root: Path):
        self.fetch_requests.append((job_id, output_root))
        output_root.mkdir(parents=True, exist_ok=True)
        markdown_path = output_root / "paper.md"
        structured_path = output_root / "paper.json"
        markdown_path.write_text(self.markdown, encoding="utf-8")
        structured_path.write_text(json.dumps(self.structured, ensure_ascii=False), encoding="utf-8")
        resource_paths = []
        for relative_path, content in self.resources.items():
            path = output_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            resource_paths.append(str(path))
        return AdapterResult.ok(
            "downloaded official MinerU async package",
            manifest={
                "task_id": job_id,
                "backend": "pipeline",
                "root": str(output_root),
                "markdown": [str(markdown_path)],
                "structured_json": [str(structured_path)],
                "resources": resource_paths,
                "quality_warnings": [],
            },
        )


class StructuredReportAdapter:
    def __init__(self, *, label: str) -> None:
        self.label = label
        self.requests = []

    def run_report(self, request):
        self.requests.append(request)
        report = {
            "summary": f"{self.label} summary with verifiable AI Infra findings.",
            "motivation": f"{self.label} targets serving latency and resource waste.",
            "method": f"{self.label} introduces runtime control over cache, scheduling, and precision signals.",
            "experiments": f"{self.label} evaluates TTFT, TPOT, throughput, and quality retention.",
            "results": f"{self.label} reports lower tail latency and improved resource utilization.",
            "innovation": f"{self.label} couples runtime feedback with model execution policy.",
            "limitations": f"{self.label} requires validation on broader model families.",
            "engineering_value": f"{self.label} can be implemented in an inference platform scheduler.",
            "reproduction_plan": f"Reproduce {self.label} with fixed request traces and ablation metrics.",
            "score": {"overall": 8.7},
            "evidence": [
                {
                    "kind": "fact",
                    "source": f"paper_version:{request.metadata['paper_version_id']}",
                    "report_field": field,
                    "section": field,
                    "page": index + 1,
                    "quote": f"{self.label} evidence for {field}",
                }
                for index, field in enumerate(REPORT_FIELDS)
            ],
        }
        return AdapterResult.ok("structured report generated", report=report)


class TranslationAdapter:
    def __init__(self) -> None:
        self.requests = []

    def run_report(self, request):
        self.requests.append(request)
        return AdapterResult.ok(
            "translated",
            markdown_zh="# 中文Markdown\n\n该论文提出可追溯的推理系统优化方法。",
            markdown_bilingual=(
                "# Bilingual Markdown\n\n"
                "Original: traceable inference optimization.\n\n"
                "中文：可追溯的推理系统优化。"
            ),
        )


class PdfRendererAdapter:
    def __init__(self) -> None:
        self.requests = []

    def render(self, markdown: str, output_path: Path):
        self.requests.append((markdown, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"%PDF-1.7\nacceptance render\n")
        return AdapterResult.ok("rendered", path=str(output_path), size_bytes=output_path.stat().st_size)


class PriorArtDiscoveryAdapter:
    def __init__(self) -> None:
        self.requests = []

    def discover(self, topic):
        self.requests.append(topic)
        return AdapterResult.ok(
            "academic prior art fixture",
            papers=[
                {
                    "source": "arxiv",
                    "source_id": "2608.09991",
                    "title": "Prior Runtime Feedback for Inference Scheduling",
                    "abstract": "A baseline runtime feedback method.",
                    "landing_url": "https://arxiv.org/abs/2608.09991",
                    "pdf_url": "https://arxiv.org/pdf/2608.09991",
                }
            ],
        )


class PatentPriorArtAdapter:
    def __init__(self) -> None:
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        return AdapterResult.ok(
            "patent prior art fixture",
            records=[
                {
                    "source_type": "patent",
                    "source": "cnipa",
                    "title": "一种推理调度控制方法",
                    "publication_number": "CN260809991A",
                    "url": "https://pss-system.cponline.cnipa.gov.cn/example/CN260809991A",
                    "abstract": "公开基于队列压力的推理调度。",
                    "analysis_basis": "CNIPA abstract fixture",
                    "bibliographic_match": True,
                    "limitations": "未公开与精度回退策略的闭环接口。",
                }
            ],
        )


class LocalPatentExportAdapter:
    def build_candidate(self, cards, *, title=None):
        from research_hub.adapters.patent import PatentEngineAdapter

        return PatentEngineAdapter().build_candidate(cards, title=title)

    def render_disclosure_markdown(self, candidate):
        from research_hub.adapters.patent import PatentEngineAdapter

        return PatentEngineAdapter().render_disclosure_markdown(candidate)

    def export_docx(self, markdown_path: Path, output_path: Path):
        output_path.write_bytes(b"PK\x03\x04 acceptance docx package\n")
        return AdapterResult.ok("docx exported", path=str(output_path))


def test_daily_paper_discovery_to_markdown_translations_report_and_digest(
    initialized_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    discovery = StaticDiscoveryAdapter(
        [
            {
                "source": "arxiv",
                "source_id": "2608.00010",
                "stable_key": "arxiv:2608.00010",
                "title": "Deterministic Runtime Control for LLM Serving",
                "abstract": "A paper about deterministic serving control.",
                "authors": ["A. Engineer"],
                "published_at": "2026-08-02T02:00:00+00:00",
                "updated_at": "2026-08-02T03:00:00+00:00",
                "pdf_url": "https://arxiv.org/pdf/2608.00010",
                "landing_url": "https://arxiv.org/abs/2608.00010",
                "categories": ["cs.DC"],
                "raw": {"fixture": "ordinary-discovery"},
            }
        ]
    )
    parser = AsyncMinerUPackageAdapter(
        markdown="# Abstract\n\nRuntime control evidence.\n\n# Method\n\nScheduler feedback.",
        structured={"pages": [{"page": 1, "blocks": [{"type": "text", "text": "Runtime control evidence."}]}]},
    )
    translator = TranslationAdapter()
    analyzer = StructuredReportAdapter(label="ordinary paper")

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        run = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="arxiv",
                topics=["aif-04"],
                max_results=1,
                window_start=datetime(2026, 8, 2, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 3, tzinfo=timezone.utc),
            ),
            idempotency_key=None,
        )

        discovered = ResearchJobService(conn, discovery_adapter=discovery).run_discovery_run(run.id)
        paper = repo.get_paper(repo.list_papers(topic="aif-04", source="arxiv")[0].id)
        version_id = paper.current_version_id or ""
        source_pdf = tmp_path / "ordinary.pdf"
        source_pdf.write_bytes(b"%PDF-1.7\nordinary fixture\n")
        source_pdf_artifact = repo.create_artifact_for_version(
            version_id,
            _artifact("source_pdf", str(source_pdf), "application/pdf", {"source_url": paper.current_version.pdf_url}),
        )
        parse_job = repo.version_action(
            version_id,
            "parse",
            VersionActionRequest(options={"pdf_path": str(source_pdf), "after_parse": ["translate", "analyze"]}),
            idempotency_key=None,
        )
        service = ResearchJobService(conn, parser_adapter=parser, translator_adapter=translator, analyzer_adapter=analyzer)
        submitted = service.run_parse_job(parse_job.job_id)
        parsed = service.poll_parse_job(parse_job.job_id)
        translate_job_id = _job_id_for_kind(parsed["result"]["chained_jobs"], "translate")
        analyze_job_id = _job_id_for_kind(parsed["result"]["chained_jobs"], "analyze")
        translated = service.run_translate_job(translate_job_id)
        analyzed = service.run_analyze_job(analyze_job_id)
        repo.select_paper(paper.id, PaperSelectRequest(selected=True))
        digest = repo.daily_digest("2026-08-02")
        artifacts = repo.list_version_artifacts(version_id)
        relation_rows = conn.execute("SELECT * FROM artifact_relation").fetchall()
        evidence_rows = conn.execute("SELECT * FROM evidence_anchor").fetchall()

        assert discovered["status"] == "succeeded"
        assert submitted["status"] == "running"
        assert parsed["status"] == "succeeded"
        assert translated["status"] == "succeeded"
        assert analyzed["status"] == "succeeded"
        assert {artifact.artifact_type for artifact in artifacts} >= {
            "source_pdf",
            "markdown_original",
            "mineru_structured_json",
            "markdown_zh",
            "markdown_bilingual",
        }
        assert parsed["result"]["manifest"]["task_id"] == "mineru-acceptance"
        assert translator.requests[0].artifact_refs
        assert analyzer.requests[0].sections[0]["title"] == "Abstract"
        assert len(evidence_rows) == len(REPORT_FIELDS)
        assert digest.counts["papers"] == 1
        assert digest.counts["selected"] == 1
        assert digest.counts["analyzed"] == 1
        assert digest.source_counts == {"arxiv": 1}
        assert digest.topic_distribution == {"aif-04": 1}
        assert paper.id in digest.reading_routes["30_minutes"]
        assert any(row["source_artifact_id"] == source_pdf_artifact.id for row in relation_rows)


def test_formula_table_dense_pdf_uses_mineru_package_report_and_pdf_render(
    initialized_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    parser = AsyncMinerUPackageAdapter(
        markdown=(
            "# Method\n\n"
            "The dense artifact preserves $QK^T / sqrt(d)$ and Table 1 throughput.\n\n"
            "![Figure](images/fig1.png)\n"
        ),
        structured={
            "pages": [
                {
                    "page": 1,
                    "blocks": [
                        {"type": "formula", "latex": "QK^T / \\sqrt{d}"},
                        {"type": "table", "cells": [["batch", "TPOT"], ["32", "12ms"]]},
                    ],
                }
            ]
        },
        resources={"images/fig1.png": b"\x89PNG\r\n\x1a\n", "tables/table1.csv": b"batch,TPOT\n32,12ms\n"},
    )
    analyzer = StructuredReportAdapter(label="formula table dense paper")
    translator = TranslationAdapter()
    renderer = PdfRendererAdapter()

    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper = _create_versioned_paper(
            repo,
            title="Formula and Table Dense KV Cache Analysis",
            topic="aif-05",
            identifier="10.5555/formula-table",
        )
        version_id = paper.current_version_id or ""
        dense_pdf = tmp_path / "dense.pdf"
        dense_pdf.write_bytes(b"%PDF-1.7\nformula table dense fixture\n")
        pdf_artifact = repo.create_artifact_for_version(
            version_id,
            _artifact("source_pdf", str(dense_pdf), "application/pdf", {"fixture": "formula-table-dense"}),
        )
        parse_job = repo.version_action(
            version_id,
            "parse",
            VersionActionRequest(options={"pdf_path": str(dense_pdf), "after_parse": ["analyze"]}),
            idempotency_key=None,
        )
        service = ResearchJobService(
            conn,
            parser_adapter=parser,
            analyzer_adapter=analyzer,
            translator_adapter=translator,
            renderer_adapter=renderer,
        )

        service.run_parse_job(parse_job.job_id)
        parsed = service.poll_parse_job(parse_job.job_id)
        analyzed = service.run_analyze_job(_job_id_for_kind(parsed["result"]["chained_jobs"], "analyze"))
        translate_job = repo.version_action(
            version_id,
            "translate",
            VersionActionRequest(options={"render_pdf": True}),
            idempotency_key=None,
        )
        translated = service.run_translate_job(translate_job.job_id)
        rendered = service.run_render_pdf_job(translated["result"]["render_pdf_job_id"])
        artifacts = repo.list_version_artifacts(version_id)
        artifact_by_type = {artifact.artifact_type: artifact for artifact in artifacts}
        resource_metadata = artifact_by_type["mineru_resources"].metadata
        pdf_relation = conn.execute(
            """
            SELECT * FROM artifact_relation
            WHERE source_artifact_id = ? AND derived_artifact_id = ?
            """,
            (translated["result"]["markdown_zh_artifact_id"], rendered["result"]["artifact_id"]),
        ).fetchone()

        assert parsed["status"] == "succeeded"
        assert analyzed["status"] == "succeeded"
        assert rendered["status"] == "succeeded"
        assert parser.submit_requests[0].extract == ("markdown", "json", "images", "tables", "formulas")
        assert artifact_by_type["markdown_original"].metadata["source_artifact_id"] == pdf_artifact.id
        assert artifact_by_type["mineru_structured_json"].metadata["source_artifact_id"] == pdf_artifact.id
        assert resource_metadata["source_artifact_id"] == pdf_artifact.id
        assert set(resource_metadata["files"]) == {"images/fig1.png", "tables/table1.csv"}
        assert Path(artifact_by_type["mineru_structured_json"].uri).is_file()
        assert "$QK^T" in analyzer.requests[0].markdown
        assert Path(artifact_by_type["pdf_zh"].uri).read_bytes().startswith(b"%PDF-")
        assert artifact_by_type["pdf_zh"].metadata["source_artifact_id"] == translated["result"]["markdown_zh_artifact_id"]
        assert pdf_relation is not None


def test_complementary_papers_prior_art_approval_and_patent_exports(
    initialized_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_HUB_EXPORT_DIR", str(tmp_path / "exports"))
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        prefetch = _create_versioned_paper(
            repo,
            title="Predictive KV Prefetch for Long Context Serving",
            topic="aif-04",
            identifier="10.5555/predictive-prefetch",
        )
        precision = _create_versioned_paper(
            repo,
            title="Low Precision State Control for Inference Runtime",
            topic="aif-02",
            identifier="10.5555/precision-control",
        )
        _upsert_report(conn, prefetch.id, prefetch.current_version_id or "", "prefetch")
        _upsert_report(conn, precision.id, precision.current_version_id or "", "precision")
        relation_job = repo.create_job("relate", "paper", prefetch.id, {"source": "acceptance"})
        relation_result = ResearchJobService(conn).run_relate_job(relation_job.job_id)
        candidate = repo.create_invention_candidate(
            _candidate_create(prefetch.id, prefetch.current_version_id or "", precision.id, precision.current_version_id or "")
        )
        prior_art_job = repo.candidate_job(candidate.id, "prior_art_check", {"source": "acceptance"}, None)
        prior_art_discovery = PriorArtDiscoveryAdapter()
        patent_prior_art = PatentPriorArtAdapter()
        prior_art = ResearchJobService(
            conn,
            discovery_adapter=prior_art_discovery,
            prior_art_adapter=patent_prior_art,
        ).run_prior_art_job(prior_art_job.job_id)
        approved = repo.approve_candidate(
            candidate.id,
            CandidateApproveRequest(
                approver="acceptance-reviewer",
                contribution_confirmed=True,
                sanitization_confirmed=True,
                protection_focus_confirmed=True,
                unverified_facts_confirmed=True,
                notes="Approve after academic and patent prior-art review.",
            ),
        )
        output = PatentOutputService(
            conn,
            output_root=tmp_path / "exports" / "patent_drafts",
            patent_adapter=LocalPatentExportAdapter(),
        ).generate_outputs(
            candidate.id,
            case_name="闭环预取与精度状态协同控制",
            protection_focus="覆盖调度方法、系统、装置和存储介质。",
            notes="基于人工批准的候选生成。",
        )
        components = repo.list_candidate_components(candidate.id)
        mechanisms = repo.list_integration_mechanisms(candidate.id)
        prior_rows = conn.execute(
            "SELECT source_type, publication_number, bibliographic_match FROM prior_art_record WHERE invention_candidate_id = ?",
            (candidate.id,),
        ).fetchall()
        provenance_fields = {
            row["report_field"]
            for row in conn.execute(
                "SELECT report_field FROM claim_provenance WHERE invention_candidate_id = ?",
                (candidate.id,),
            ).fetchall()
        }
        decisions = conn.execute(
            "SELECT decision, actor FROM human_decision WHERE invention_candidate_id = ?",
            (candidate.id,),
        ).fetchall()
        draft_artifacts = repo.list_draft_artifacts(output.draft.id)

        assert relation_result["status"] == "succeeded"
        assert repo.list_relations(prefetch.id)
        assert prior_art["status"] == "succeeded"
        assert {row["source_type"] for row in prior_rows} == {"academic", "patent"}
        assert all(row["bibliographic_match"] == 1 for row in prior_rows)
        assert prior_art["result"]["legal_notice"].startswith("This automated search is not")
        assert prior_art_discovery.requests[0].topic_id == "prior-art-academic"
        assert patent_prior_art.requests[0]["candidate_id"] == candidate.id
        assert approved.status == "approved"
        assert approved.gate["human_confirmations"]["approver"] == "acceptance-reviewer"
        assert decisions[-1]["decision"] == "approved"
        assert decisions[-1]["actor"] == "acceptance-reviewer"
        assert len(components) == 2
        assert mechanisms[0].mechanism_type == "cross_paper_coupling"
        assert provenance_fields == {
            "problem_statement",
            "integration_mechanism",
            "coupling_interface",
            "data_or_control_flow",
            "why_not_juxtaposition",
            "expected_joint_effect",
            "technical_effects",
        }
        assert output.draft.self_check["fact_provenance_coverage"]["coverage_percent"] == 100
        assert "## 事实级来源与假设标注" in output.draft.markdown
        assert "paper_version:" in output.draft.markdown
        assert {artifact.artifact_type for artifact in draft_artifacts} == {
            "patent_disclosure_markdown",
            "patent_disclosure_docx",
        }
        assert all(Path(artifact.uri.removeprefix("file://")).is_file() for artifact in draft_artifacts)


def test_negative_juxtaposition_candidate_is_rejected(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        first = _create_versioned_paper(repo, title="Paper A", topic="aif-03", identifier="10.5555/a")
        second = _create_versioned_paper(repo, title="Paper B", topic="aif-04", identifier="10.5555/b")

        with pytest.raises(ConflictError, match="mere juxtaposition"):
            repo.create_invention_candidate(
                InventionCandidateCreate(
                    title="Rejected side-by-side candidate",
                    sources=[
                        InventionSourceRef(paper_id=first.id, paper_version_id=first.current_version_id, contribution="A summary"),
                        InventionSourceRef(paper_id=second.id, paper_version_id=second.current_version_id, contribution="B summary"),
                    ],
                    problem_statement="需要降低推理延迟和部署误差。",
                    integration_mechanism="Only juxtaposition of two paper summaries without a shared controller.",
                    coupling_interface="A dashboard lists both methods but exposes no feedback control interface.",
                    data_or_control_flow="No data flow crosses between the two paper methods.",
                    why_not_juxtaposition="only juxtaposition",
                    expected_joint_effect="Expected effect is unverified and not tied to a mechanism.",
                    technical_effects="Potential effect remains unverified.",
                    risk_notes="Reject because the proposal is only a side-by-side summary.",
                    evidence=_candidate_evidence(first.id, first.current_version_id or "", second.id, second.current_version_id or ""),
                )
            )


def _job_id_for_kind(chained_jobs: list[dict[str, str]], kind: str) -> str:
    return next(item["job_id"] for item in chained_jobs if item["kind"] == kind)


def _artifact(kind: str, uri: str, media_type: str, metadata: dict[str, Any]) -> ArtifactCreate:
    return ArtifactCreate(artifact_type=kind, uri=uri, media_type=media_type, metadata=metadata)


def _create_versioned_paper(repo: Repository, *, title: str, topic: str, identifier: str):
    return repo.create_paper(
        PaperCreate(
            canonical_title=title,
            abstract="AI Infra acceptance fixture with cache scheduling precision runtime control.",
            identifiers=[PaperIdentifier(type="doi", value=identifier)],
            topics=[topic],
            version=PaperVersionCreate(
                version_label="v1",
                source="acceptance",
                source_version_id=identifier,
                pdf_url=f"https://example.test/{identifier}.pdf",
            ),
        )
    )


def _upsert_report(conn: sqlite3.Connection, paper_id: str, version_id: str, label: str) -> None:
    service = ResearchJobService(conn, analyzer_adapter=StructuredReportAdapter(label=label))
    job = Repository(conn).version_action(version_id, "analyze", VersionActionRequest(), None)
    result = service.run_analyze_job(job.job_id)
    assert result["status"] == "succeeded"
    assert Repository(conn).get_version_report(version_id).score["quality_status"] == "complete"
    assert paper_id


def _candidate_create(
    first_paper_id: str,
    first_version_id: str,
    second_paper_id: str,
    second_version_id: str,
) -> InventionCandidateCreate:
    return InventionCandidateCreate(
        title="闭环预取与精度状态协同控制候选",
        sources=[
            InventionSourceRef(
                paper_id=first_paper_id,
                paper_version_id=first_version_id,
                technical_card_id=f"card:{first_version_id}",
                contribution="提供预测预取窗口和长上下文 KV 访问控制机制。",
            ),
            InventionSourceRef(
                paper_id=second_paper_id,
                paper_version_id=second_version_id,
                technical_card_id=f"card:{second_version_id}",
                contribution="提供低精度状态选择、动态范围监控和误差回退机制。",
            ),
        ],
        problem_statement="长上下文推理需要同时控制预取等待、带宽占用和低精度误差。",
        integration_mechanism="以运行时控制器把预测预取置信度、队列压力和张量动态范围映射为统一调度策略。",
        coupling_interface="预测预取器向精度状态管理器暴露窗口置信度、阶段和回退阈值控制接口。",
        data_or_control_flow="prefill/decode 阶段产生窗口置信度和张量动态范围，控制流反向调节预取优先级与数值精度。",
        why_not_juxtaposition="精度策略会改变预取窗口风险阈值，预取命中状态也会改变精度选择，形成闭环反馈。",
        expected_joint_effect="预期同时降低等待、带宽占用和低精度误差，但具体幅度需实验验证。",
        technical_effects="可能改善 TTFT、TPOT 和资源利用率，未验证幅度需要后续实验确认。",
        risk_notes="需要查新确认闭环接口和控制策略是否区别于已有调度或量化方案。",
        evidence=_candidate_evidence(first_paper_id, first_version_id, second_paper_id, second_version_id),
    )


def _candidate_evidence(
    first_paper_id: str,
    first_version_id: str,
    second_paper_id: str,
    second_version_id: str,
) -> list[dict[str, str]]:
    return [
        {
            "kind": "fact",
            "source": f"paper:{first_paper_id}",
            "report_field": "problem_statement",
            "note": "长上下文推理存在预取等待和带宽占用问题。",
        },
        {
            "kind": "fact",
            "source": f"paper_version:{first_version_id}",
            "report_field": "integration_mechanism",
            "note": "预测预取窗口可作为运行时控制输入。",
        },
        {
            "kind": "fact",
            "source": f"paper_version:{second_version_id}",
            "report_field": "coupling_interface",
            "note": "低精度状态管理器可接收动态范围和部署约束。",
        },
        {
            "kind": "fact",
            "source": f"paper:{first_paper_id}",
            "report_field": "data_or_control_flow",
            "note": "prefill/decode 阶段信号驱动预取优先级。",
        },
        {
            "kind": "fact",
            "source": f"paper:{second_paper_id}",
            "report_field": "why_not_juxtaposition",
            "note": "精度选择与预取阈值互相影响。",
        },
        {
            "kind": "hypothesis",
            "source": "user",
            "report_field": "expected_joint_effect",
            "note": "联合降低等待和误差的幅度需实验验证。",
        },
        {
            "kind": "hypothesis",
            "source": "user",
            "report_field": "technical_effects",
            "note": "整体技术效果仍需实验确认。",
        },
    ]
