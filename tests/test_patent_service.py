from __future__ import annotations

import re
from pathlib import Path

import pytest

from research_hub.models import (
    CandidateApproveRequest,
    InventionCandidateCreate,
    InventionSourceRef,
    PaperCreate,
    PaperIdentifier,
    PaperVersionCreate,
)
from research_hub.adapters.patent import ensure_path_within_root, safe_patent_filename
from research_hub.adapters.types import PatentCandidate
from research_hub.patent_service import PatentOutputService
from research_hub.repository import ConflictError, Repository


def test_patent_output_service_requires_approval_before_generation(initialized_db, tmp_path: Path) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=False)

        with pytest.raises(ConflictError, match="human approval"):
            PatentOutputService(conn, output_root=tmp_path / "exports").generate_outputs(candidate.id)


def test_candidate_approval_requires_prior_art_success_or_override(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=False)

        with pytest.raises(ConflictError, match="prior_art_check"):
            repo.approve_candidate(candidate.id, _approval_request(notes="approve"))

        with pytest.raises(ConflictError, match="override_reason"):
            repo.approve_candidate(
                candidate.id,
                _approval_request(override_prior_art=True),
            )

        approved = repo.approve_candidate(
            candidate.id,
            _approval_request(
                override_prior_art=True,
                override_reason="人工查新系统暂不可用，已由负责人线下确认风险。",
                notes="manual override",
            ),
        )

        assert approved.status == "approved"
        assert approved.gate["prior_art"]["status"] == "overridden"
        assert approved.gate["prior_art"]["override_reason"]
        assert approved.gate["human_confirmations"]["approver"] == "patent-reviewer"
        assert approved.gate["audit"][-1]["event"] == "prior_art_override"


def test_candidate_creation_rejects_juxtaposition_without_structured_coupling(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper_1 = repo.create_paper(PaperCreate(canonical_title="Paper A"))
        paper_2 = repo.create_paper(PaperCreate(canonical_title="Paper B"))

        with pytest.raises(ConflictError, match="structured coupling"):
            repo.create_invention_candidate(
                InventionCandidateCreate(
                    title="仅拼接候选",
                    sources=[
                        InventionSourceRef(paper_id=paper_1.id, contribution="A"),
                        InventionSourceRef(paper_id=paper_2.id, contribution="B"),
                    ],
                    problem_statement="降低长上下文推理等待和低精度误差。",
                    integration_mechanism="简单拼接两个论文方案形成组合。",
                    technical_effects="可能降低等待并改善误差。",
                )
            )


def test_candidate_approval_requires_human_confirmations(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=False)
        _mark_prior_art_succeeded(repo, candidate.id)

        with pytest.raises(ConflictError, match="approver"):
            repo.approve_candidate(candidate.id, CandidateApproveRequest(approved=True))


def test_patent_output_service_generates_markdown_docx_and_persisted_artifacts(
    initialized_db, tmp_path: Path
) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=True)

        service = PatentOutputService(conn, output_root=tmp_path / "exports")
        output = service.generate_outputs(
            candidate.id,
            case_name="长上下文推理的预测预取与低精度协同控制",
            protection_focus="建议覆盖方法、系统、装置和存储介质。",
            notes="保留来源证据，不给出法律授权结论。",
        )

        draft = repo.get_patent_draft(output.draft.id)
        artifacts = repo.list_draft_artifacts(draft.id)

        assert draft.case_name == "长上下文推理的预测预取与低精度协同控制"
        assert draft.version_label == output.version_label
        assert re.fullmatch(r"长上下文推理的预测预取与低精度协同控制_\d{14}", draft.version_label)
        assert "不是法律意义上的新颖性" in draft.markdown
        assert "## 事实级来源与假设标注" in draft.markdown
        assert draft.self_check["fact_provenance_coverage"]["coverage_percent"] == 100
        assert "paper:" in draft.markdown
        assert len(artifacts) == 2
        assert {artifact.artifact_type for artifact in artifacts} == {
            "patent_disclosure_markdown",
            "patent_disclosure_docx",
        }
        assert all(artifact.uri.startswith("file://") for artifact in artifacts)
        assert all(Path(artifact.uri.removeprefix("file://")).is_file() for artifact in artifacts)
        assert repo.get_artifact(output.artifacts.markdown_artifact["artifact_id"]).id == artifacts[0].id
        assert output.artifacts.markdown_artifact["store_artifact_id"]
        assert output.artifacts.markdown_artifact["sha256"] != output.artifacts.docx_artifact["sha256"]


def test_patent_output_service_rechecks_prior_art_gate(initialized_db, tmp_path: Path) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=False)
        conn.execute(
            """
            UPDATE invention_candidate
            SET status = 'approved', gate_json = ?
            WHERE id = ?
            """,
            ('{"status":"approved","notes":"legacy status without prior art"}', candidate.id),
        )

        with pytest.raises(ConflictError, match="prior-art"):
            PatentOutputService(conn, output_root=tmp_path / "exports").generate_outputs(candidate.id)


def test_patent_output_service_rejects_degraded_adapter_gate(initialized_db, tmp_path: Path) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=True)

        with pytest.raises(ConflictError, match="Patent adapter gate rejected"):
            PatentOutputService(
                conn,
                output_root=tmp_path / "exports",
                patent_adapter=DegradedPatentAdapter(),
            ).generate_outputs(candidate.id)


def test_patent_output_service_is_idempotent_for_same_request(initialized_db, tmp_path: Path) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=True)

        service = PatentOutputService(conn, output_root=tmp_path / "exports")
        first = service.generate_outputs(candidate.id, case_name="PD-KV")
        second = service.generate_outputs(candidate.id, case_name="PD-KV")

        assert first.draft.id == second.draft.id
        assert first.version_label == second.version_label
        assert first.artifacts.markdown_artifact["artifact_id"] == second.artifacts.markdown_artifact["artifact_id"]
        assert first.artifacts.docx_artifact["artifact_id"] == second.artifacts.docx_artifact["artifact_id"]


def test_patent_output_service_rejects_incomplete_fact_provenance_for_legacy_candidate(
    initialized_db, tmp_path: Path
) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        candidate = _create_candidate(repo, approved=True)
        conn.execute(
            """
            UPDATE invention_candidate
            SET evidence_json = ?
            WHERE id = ?
            """,
            ('[{"kind":"analysis","note":"legacy summary without field coverage"}]', candidate.id),
        )

        with pytest.raises(ConflictError, match="100% fact-level provenance"):
            PatentOutputService(conn, output_root=tmp_path / "exports").generate_outputs(candidate.id)


def test_patent_filename_safety_rejects_traversal_and_controls_length(tmp_path: Path) -> None:
    filename = safe_patent_filename("../bad/\x00name" * 40, extension="docx")

    assert "/" not in filename
    assert "\\" not in filename
    assert "\x00" not in filename
    assert ".." not in filename
    assert filename.endswith(".docx")
    assert len(filename) <= 85

    root = tmp_path / "root"
    root.mkdir()
    inside = ensure_path_within_root(root / filename, root)
    assert inside.parent == root
    with pytest.raises(ValueError, match="escapes root"):
        ensure_path_within_root(root / ".." / "escape.docx", root)


class DegradedPatentAdapter:
    def build_candidate(self, cards, *, title=None):
        return PatentCandidate(
            candidate_id="degraded",
            title=title or "degraded",
            source_cards=tuple(cards),
            technical_problem="",
            combined_solution="",
            technical_effects=(),
            novelty_risks=(),
            implementation_gaps=("bad aggregation",),
            gate_status="degraded",
            gate_reasons=("bad aggregation",),
        )

    def render_disclosure_markdown(self, candidate):
        raise AssertionError("degraded candidates must not be rendered")

    def export_docx(self, markdown_path, output_path):
        raise AssertionError("degraded candidates must not be exported")


def _create_candidate(repo: Repository, *, approved: bool):
    paper_1 = repo.create_paper(
        PaperCreate(
            canonical_title="DualDecoder: Accelerate Long Context LLM Inference by Predictive Prefetch",
            abstract="Long context inference with predictive prefetching.",
            identifiers=[PaperIdentifier(type="doi", value="10.1234/dualdecoder")],
            version=PaperVersionCreate(
                version_label="v1",
                source="test",
                source_version_id="dualdecoder-v1",
                pdf_url="https://example.test/dualdecoder.pdf",
            ),
        )
    )
    paper_2 = repo.create_paper(
        PaperCreate(
            canonical_title="HiFloat4 Format for End-To-End Reinforcement Learning Post-Training of Large Language Models",
            abstract="Low precision format for post-training and deployment.",
            identifiers=[PaperIdentifier(type="doi", value="10.1234/hifloat4")],
            version=PaperVersionCreate(
                version_label="v1",
                source="test",
                source_version_id="hifloat4-v1",
                pdf_url="https://example.test/hifloat4.pdf",
            ),
        )
    )
    candidate = repo.create_invention_candidate(
        InventionCandidateCreate(
            title="长上下文推理中的预测预取与低精度状态协同控制",
            sources=[
                InventionSourceRef(
                    paper_id=paper_1.id,
                    paper_version_id=paper_1.current_version_id,
                    contribution="提供预测预取窗口与长上下文内存访问控制机制",
                ),
                InventionSourceRef(
                    paper_id=paper_2.id,
                    paper_version_id=paper_2.current_version_id,
                    contribution="提供低精度数值格式约束和训练后部署条件",
                ),
            ],
            problem_statement="长上下文推理的数据预取开销与低精度误差需要联合控制。",
            integration_mechanism="根据预测预取窗口、张量动态范围和运行阶段选择预取优先级及数值精度。",
            technical_effects="降低等待和带宽占用，并控制低精度误差。",
            risk_notes="需要查新并验证预测窗口、精度控制器和回退机制是否形成非显而易见的耦合。",
            coupling_interface="预测预取控制器向低精度状态管理器暴露窗口置信度、阶段和回退阈值接口。",
            data_or_control_flow="prefill/decode 阶段产生的窗口置信度和张量动态范围进入统一控制流，反向调节预取优先级和数值精度。",
            why_not_juxtaposition="低精度策略会改变预取窗口的风险阈值，预取命中状态也会改变精度选择，二者存在闭环反馈。",
            expected_joint_effect="预期在长上下文推理中同时降低等待、带宽占用和低精度误差，但具体幅度需实验验证。",
            evidence=_candidate_evidence(paper_1.id, paper_2.id),
        )
    )
    if approved:
        _mark_prior_art_succeeded(repo, candidate.id)
        repo.approve_candidate(candidate.id, _approval_request(notes="approved for output generation"))
    return candidate


def _approval_request(**overrides) -> CandidateApproveRequest:
    data = {
        "approved": True,
        "approver": "patent-reviewer",
        "contribution_confirmed": True,
        "sanitization_confirmed": True,
        "protection_focus_confirmed": True,
        "unverified_facts_confirmed": True,
    }
    data.update(overrides)
    return CandidateApproveRequest(**data)


def _candidate_evidence(paper_1_id: str, paper_2_id: str) -> list[dict[str, str]]:
    return [
        {
            "kind": "fact",
            "source": f"paper:{paper_1_id}",
            "report_field": "problem_statement",
            "note": "长上下文推理存在预取等待和内存访问控制问题。",
        },
        {
            "kind": "fact",
            "source": f"paper:{paper_1_id}",
            "report_field": "integration_mechanism",
            "note": "预测预取窗口可作为运行时控制输入。",
        },
        {
            "kind": "fact",
            "source": f"paper:{paper_2_id}",
            "report_field": "coupling_interface",
            "note": "低精度状态管理器可接收动态范围和部署约束。",
        },
        {
            "kind": "fact",
            "source": f"paper:{paper_1_id}",
            "report_field": "data_or_control_flow",
            "note": "prefill/decode 阶段信号驱动预取优先级。",
        },
        {
            "kind": "fact",
            "source": f"paper:{paper_2_id}",
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


def _mark_prior_art_succeeded(repo: Repository, candidate_id: str) -> None:
    job = repo.candidate_job(candidate_id, "prior_art_check", {"source": "test"}, idempotency_key=None)
    repo.conn.execute(
        """
        UPDATE job
        SET status = 'succeeded', result_json = ?, error_json = '{}', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        ('{"source":"test","status":"clear"}', job.job_id),
    )
