from __future__ import annotations

import pytest

from config.settings import Settings
from fastapi.testclient import TestClient
from research_hub.app import create_app
from research_hub.database import dumps, seed_demo_records
from research_hub.models import PaperCreate
from research_hub.repository import Repository


PATENT_CANDIDATE_BODY = {
    "title": "长上下文推理中的预测预取与低精度协同控制",
    "sources": [
        {
            "paper_id": "paper-1",
            "paper_version_id": "pv-1",
            "contribution": "提供 prefill/decode 分离和 KV cache 管理约束",
        },
        {
            "paper_id": "paper-2",
            "paper_version_id": "pv-2",
            "contribution": "提供投机解码调度和验证约束",
        },
    ],
    "problem_statement": "长上下文推理的等待开销和解码误差需要联合控制。",
    "integration_mechanism": "按请求阶段选择缓存调度、草稿验证和回退策略。",
    "coupling_interface": "缓存控制器通过置信度接口触发草稿验证器并接收回退反馈。",
    "data_or_control_flow": "阶段信号和缓存状态进入统一控制流，再反馈验证批次与迁移决策。",
    "why_not_juxtaposition": "验证结果会改变缓存迁移阈值，缓存状态也会反向改变验证批次。",
    "expected_joint_effect": "预期联合降低等待与带宽占用，但幅度仍需实验验证。",
    "technical_effects": "潜在降低尾延迟和带宽占用，具体效果需实验验证。",
    "risk_notes": "需要人工查新并验证联合控制效果。",
    "evidence": [
        {
            "kind": "fact",
            "source": "paper:paper-1",
            "report_field": "problem_statement",
            "note": "论文一支持问题定义。",
        },
        {
            "kind": "fact",
            "source": "paper:paper-1",
            "report_field": "integration_mechanism",
            "note": "论文一支持缓存运行时控制。",
        },
        {
            "kind": "fact",
            "source": "paper:paper-2",
            "report_field": "coupling_interface",
            "note": "论文二支持验证控制接口。",
        },
        {
            "kind": "fact",
            "source": "paper:paper-1",
            "report_field": "data_or_control_flow",
            "note": "阶段信号驱动缓存控制流。",
        },
        {
            "kind": "fact",
            "source": "paper:paper-2",
            "report_field": "why_not_juxtaposition",
            "note": "验证与缓存决策存在双向反馈。",
        },
        {
            "kind": "hypothesis",
            "source": "user",
            "report_field": "expected_joint_effect",
            "note": "联合效果仍需实验验证。",
        },
        {
            "kind": "hypothesis",
            "source": "user",
            "report_field": "technical_effects",
            "note": "效果是待验证工程假设。",
        },
    ],
}


def test_public_mode_requires_api_key_at_startup(tmp_path, project_root) -> None:
    with pytest.raises(RuntimeError, match="write-capable API key"):
        create_app(
            Settings(
                database_path=tmp_path / "public-no-key.sqlite3",
                api_key=None,
                static_dir=project_root / "web",
                public_mode=True,
            )
        )


def test_public_mode_with_api_key_starts_and_keeps_reads_public(tmp_path, project_root) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "public-with-key.sqlite3",
            api_key="secret-write-key",
            static_dir=project_root / "web",
            public_mode=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/v1/topics").status_code == 200
    assert client.get("/api/v1/stats").status_code == 200
    assert client.patch("/api/v1/topics/aif-01", json={"enabled": False}).status_code == 401


def test_legacy_api_key_allows_existing_write_paths_but_keeps_public_reads(tmp_path, project_root) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "auth.sqlite3",
            api_key="secret-write-key",
            static_dir=project_root / "web",
        )
    )
    client = TestClient(app)

    assert client.get("/api/v1/topics").status_code == 200
    assert client.get("/api/v1/stats").status_code == 200
    assert client.patch("/api/v1/topics/aif-01", json={"enabled": False}).status_code == 401
    assert (
        client.patch(
            "/api/v1/topics/aif-01",
            headers={"X-API-Key": "secret-write-key"},
            json={"enabled": False},
        ).status_code
        == 200
    )


def test_rbac_roles_gate_write_families_and_expose_audit_principal(tmp_path, project_root) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "rbac.sqlite3",
            api_key=None,
            static_dir=project_root / "web",
            admin_api_key="admin-key",
            researcher_api_key="research-key",
            patent_editor_api_key="patent-key",
            read_only_api_key="read-only-key",
        )
    )
    with app.state.database.connect() as conn:
        seed_demo_records(conn)

    @app.middleware("http")
    async def capture_audit_principal(request, call_next):
        response = await call_next(request)
        principal = request.state.audit_principal
        response.headers["X-Test-Audit-Role"] = principal.role
        response.headers["X-Test-Audit-Permissions"] = ",".join(sorted(principal.permissions))
        return response

    client = TestClient(app)

    assert client.get("/api/v1/papers").status_code == 200
    assert (
        client.patch(
            "/api/v1/topics/aif-01",
            headers={"X-API-Key": "research-key"},
            json={"enabled": False},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/topics/aif-01",
            headers={"X-API-Key": "admin-key"},
            json={"enabled": False},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/papers",
            headers={"X-API-Key": "research-key"},
            json={"canonical_title": "Researcher-created paper"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/invention-candidates",
            headers={"X-API-Key": "research-key"},
            json=PATENT_CANDIDATE_BODY,
        ).status_code
        == 403
    )
    patent_response = client.post(
        "/api/v1/invention-candidates",
        headers={"X-API-Key": "patent-key"},
        json=PATENT_CANDIDATE_BODY,
    )
    assert patent_response.status_code == 201
    assert (
        client.patch(
            "/api/v1/topics/aif-01",
            headers={"X-API-Key": "read-only-key"},
            json={"enabled": False},
        ).status_code
        == 403
    )

    patent_audit = client.get(
        "/api/v1/papers",
        headers={"X-API-Key": "patent-key", "X-Trace-Id": "trc-rbac-test"},
    )
    read_only_audit = client.get("/api/v1/papers", headers={"X-API-Key": "read-only-key"})
    assert patent_audit.headers["X-Test-Audit-Role"] == "patent-editor"
    assert "patent:write" in patent_audit.headers["X-Test-Audit-Permissions"]
    assert patent_audit.headers["X-Trace-Id"] == "trc-rbac-test"
    assert read_only_audit.headers["X-Test-Audit-Role"] == "read-only"
    assert read_only_audit.headers["X-Test-Audit-Permissions"] == ""


def test_dead_letter_replay_is_admin_only(tmp_path, project_root) -> None:
    app = create_app(
        Settings(
            database_path=tmp_path / "dead-letter.sqlite3",
            api_key=None,
            static_dir=project_root / "web",
            admin_api_key="admin-key",
            researcher_api_key="research-key",
        )
    )
    with app.state.database.connect() as conn:
        repository = Repository(conn)
        paper = repository.create_paper(PaperCreate(canonical_title="Failed Parse"))
        job = repository.create_job(
            "parse",
            "paper_version",
            paper.current_version_id or "",
            {"source": "auth-boundary"},
        )
        conn.execute(
            "UPDATE job SET status = 'retryable_failed', error_json = ? WHERE id = ?",
            (dumps({"message": "adapter down"}), job.job_id),
        )

    client = TestClient(app)

    listed = client.get("/api/v1/jobs/dead-letter")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["items"][0]["id"] == job.job_id

    replay_without_key = client.post(
        f"/api/v1/jobs/dead-letter/{job.job_id}/replay",
        json={"reason": "fixed"},
    )
    replay_as_researcher = client.post(
        f"/api/v1/jobs/dead-letter/{job.job_id}/replay",
        headers={"X-API-Key": "research-key"},
        json={"reason": "fixed"},
    )
    replay_as_admin = client.post(
        f"/api/v1/jobs/dead-letter/{job.job_id}/replay",
        headers={"Authorization": "Bearer admin-key"},
        json={"reason": "fixed"},
    )

    assert replay_without_key.status_code == 401
    assert replay_as_researcher.status_code == 403
    assert replay_as_admin.status_code == 200
    assert replay_as_admin.json()["status"] == "queued"
    assert client.get("/api/v1/jobs/dead-letter").json()["count"] == 0
