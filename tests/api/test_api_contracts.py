from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings
from fastapi.testclient import TestClient
from research_hub.adapters import AdapterResult
from research_hub.app import create_app
from research_hub.database import seed_demo_records
from research_hub.models import ArtifactCreate
from research_hub.repository import Repository


def isolated_seeded_client(tmp_path: Path, project_root: Path) -> TestClient:
    app = create_app(
        Settings(
            database_path=tmp_path / "api-contract.sqlite3",
            api_key=None,
            static_dir=project_root / "web",
        )
    )
    with app.state.database.connect() as conn:
        seed_demo_records(conn)
    return TestClient(app)


def valid_candidate_payload(**overrides) -> dict:
    payload = {
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
                "note": "技术效果幅度尚未验证。",
            },
        ],
    }
    payload.update(overrides)
    return payload


def approval_payload(**overrides) -> dict:
    payload = {
        "approved": True,
        "approver": "api-contract-reviewer",
        "contribution_confirmed": True,
        "sanitization_confirmed": True,
        "protection_focus_confirmed": True,
        "unverified_facts_confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_health_endpoint_returns_schema_version(api_client) -> None:
    client = api_client

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["schema_version"] >= 1


def test_papers_api_exposes_translated_abstract(api_client) -> None:
    with api_client.app.state.database.connect() as conn:
        conn.execute(
            "UPDATE paper SET translated_abstract = ? WHERE id = 'paper-1'",
            ("这是通过 LLM 生成的中文摘要。",),
        )

    response = api_client.get("/api/v1/papers")
    paper = next(item for item in response.json() if item["id"] == "paper-1")

    assert paper["translated_abstract"] == "这是通过 LLM 生成的中文摘要。"


def test_selected_papers_api_persists_notebook_membership(api_client) -> None:
    papers = api_client.get("/api/v1/papers").json()
    for paper in papers:
        response = api_client.post(
            f"/api/v1/papers/{paper['id']}/select",
            json={"selected": paper["id"] == "paper-2"},
        )
        assert response.status_code == 200

    selected = api_client.get("/api/v1/papers?selected=true")

    assert selected.status_code == 200
    assert [paper["id"] for paper in selected.json()] == ["paper-2"]


def test_runtime_config_masks_model_secret_and_exposes_fixed_platform_api(api_client) -> None:
    response = api_client.put(
        "/api/v1/runtime-config",
        headers={"Idempotency-Key": "runtime-config-openai"},
        json={
            "analysis": {
                "provider": "openai",
                "openai": {
                    "base_url": "http://model.internal/v1",
                    "api_key": "server-only-secret",
                    "model": "research-model",
                },
            },
            "schedule": {
                "enabled": True,
                "daily_hour": 8,
                "auto_process": True,
                "after_parse": ["analyze"],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["abstract_translation_jobs_queued"] == 2
    assert "api_key" not in response.json()["analysis"]["openai"]
    assert response.json()["analysis"]["openai"]["api_key_configured"] is True
    public = api_client.get("/api/v1/runtime-config").json()
    assert public["platform"]["api_base"] == "/api/v1"
    assert public["analysis"]["openai"]["api_key_configured"] is True
    assert "server-only-secret" not in json.dumps(public)


def test_saving_same_llm_config_does_not_duplicate_abstract_translation_jobs(api_client) -> None:
    payload = {
        "analysis": {
            "provider": "openai",
            "openai": {
                "base_url": "http://model.internal/v1",
                "api_key": "server-only-secret",
                "model": "research-model",
            },
        },
    }

    first = api_client.put(
        "/api/v1/runtime-config",
        headers={"Idempotency-Key": "runtime-config-backfill-first"},
        json=payload,
    )
    second = api_client.put(
        "/api/v1/runtime-config",
        headers={"Idempotency-Key": "runtime-config-backfill-second"},
        json=payload,
    )

    assert first.status_code == 200
    assert first.json()["abstract_translation_jobs_queued"] == 2
    assert second.status_code == 200
    assert second.json()["abstract_translation_jobs_queued"] == 0
    jobs = api_client.get("/api/v1/jobs?kind=translate").json()["items"]
    assert len(jobs) == 2


def test_schedule_or_incomplete_llm_config_does_not_queue_abstract_translation(api_client) -> None:
    schedule = api_client.put(
        "/api/v1/runtime-config",
        headers={"Idempotency-Key": "runtime-config-schedule-only"},
        json={"schedule": {"enabled": True, "daily_hour": 10}},
    )
    incomplete_analysis = api_client.put(
        "/api/v1/runtime-config",
        headers={"Idempotency-Key": "runtime-config-incomplete-analysis"},
        json={"analysis": {"provider": "openai", "openai": {"base_url": "", "model": ""}}},
    )

    assert schedule.status_code == 200
    assert schedule.json()["abstract_translation_jobs_queued"] == 0
    assert incomplete_analysis.status_code == 200
    assert incomplete_analysis.json()["abstract_translation_jobs_queued"] == 0
    assert api_client.get("/api/v1/jobs?kind=translate").json()["items"] == []


def test_workflow_api_exposes_daily_and_patent_dags(api_client) -> None:
    response = api_client.get("/api/v1/workflows")

    assert response.status_code == 200
    payload = response.json()
    workflows = {item["id"]: item for item in payload["items"]}
    assert {"daily-paper-intelligence", "patent-disclosure"} <= workflows.keys()
    assert [node["kind"] for node in workflows["daily-paper-intelligence"]["nodes"]][:4] == [
        "discover",
        "download",
        "parse",
        "analyze",
    ]


def test_topics_api_creates_and_lists_custom_topic(api_client) -> None:
    created = api_client.post(
        "/api/v1/topics",
        headers={"Idempotency-Key": "custom-multimodal-topic"},
        json={
            "name_zh": "多模态推理",
            "name_en": "Multimodal Reasoning",
            "daily_quota": 7,
            "aliases": ["VLM", "multimodal", "reasoning"],
        },
    )

    assert created.status_code == 201
    topic = created.json()
    assert topic["name_zh"] == "多模态推理"
    assert topic["daily_quota"] == 7
    listed = api_client.get("/api/v1/topics").json()["items"]
    assert any(item["id"] == topic["id"] for item in listed)


def test_deleting_topic_hides_its_papers_but_keeps_papers_with_other_topics(api_client) -> None:
    """Deleting a topic is a soft delete: papers that belong only to that
    topic disappear from the paper library and daily digest, while papers
    that also belong to another active topic remain visible."""
    # paper-1 belongs only to aif-04; paper-2 belongs only to aif-03.
    def paper_ids(payload):
        return [item["id"] for item in payload]

    before_lib = api_client.get("/api/v1/papers?date=2026-08-02").json()
    assert "paper-1" in paper_ids(before_lib)
    assert "paper-2" in paper_ids(before_lib)

    # topic list currently shows both topics.
    topics_before = api_client.get("/api/v1/topics").json()["items"]
    assert any(t["id"] == "aif-03" for t in topics_before)
    assert any(t["id"] == "aif-04" for t in topics_before)

    # Soft-delete topic aif-04 (paper-1's only topic).
    deleted = api_client.delete("/api/v1/topics/aif-04")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == "aif-04"

    # aif-04 no longer listed; soft delete keeps the row but marks it.
    topics_after = api_client.get("/api/v1/topics").json()["items"]
    assert not any(t["id"] == "aif-04" for t in topics_after)

    # paper-1 (only aif-04) is hidden from library & digest.
    after_lib = api_client.get("/api/v1/papers?date=2026-08-02").json()
    assert "paper-1" not in paper_ids(after_lib)
    assert "paper-2" in paper_ids(after_lib)
    digest = api_client.get("/api/v1/daily-digests/2026-08-02").json()
    assert "paper-1" not in paper_ids(digest["papers"])
    assert "paper-2" in paper_ids(digest["papers"])
    assert "aif-04" not in digest["topic_distribution"]
    assert "aif-03" in digest["topic_distribution"]

    # A forwarded query for the specific topic now 404s (deleted).
    assert api_client.get("/api/v1/topics/aif-04/digest?date=2026-08-02").status_code == 404


def test_relations_list_exposes_all_relations_and_rebuild(api_client) -> None:
    """The relationship view must be able to list relations across all papers
    (with endpoint titles) and trigger a rebuild via the API."""
    response = api_client.get("/api/v1/relations")
    assert response.status_code == 200
    payload = response.json()
    items = payload.get("items", [])
    assert "total" in payload

    # Every returned relation must carry endpoint paper titles so the view can
    # render a full graph without opening each paper's workspace.
    first_ids = {item["id"] for item in items}
    for item in items:
        assert item["from_title"]
        assert item["to_title"]
        assert item["relation_type"]
        assert "confidence" in item

    rebuild = api_client.post(
        "/api/v1/relations/rebuild",
        headers={"Idempotency-Key": "relations-rebuild-test"},
    )
    assert rebuild.status_code == 200
    assert rebuild.json()["scope"] == "all"
    assert {"created", "updated"} <= rebuild.json().keys()

    # The all-relations listing returns the same set after an idempotent-ish
    # rebuild (relations are upserted, not duplicated).
    after = api_client.get("/api/v1/relations").json()["items"]
    assert {item["id"] for item in after} >= first_ids


def test_papers_all_param_returns_full_corpus_across_dates(api_client) -> None:
    """?all=1 must return papers regardless of the selected discovery date, so
    historical papers are never lost when browsing by date."""
    # Seed one more paper on a different discovery date.
    with api_client.app.state.database.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper (
                id, canonical_title, abstract, language, first_publication_date,
                current_version_id, status, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'en', '2026-08-01', NULL, 'discovered', '{}', ?, ?)
            """,
            ("paper-historical", "Historical KV Cache Paper", "Old demo paper on KV cache eviction.", "2026-08-03T00:00:00+00:00", "2026-08-03T00:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO paper_source_hit (id, paper_id, paper_version_id, source, query, rank, hit_date, raw_summary_json)
            VALUES (?, ?, NULL, 'demo', 'kv cache', 1, '2026-08-03', '{}')
            """,
            ("hit-historical", "paper-historical"),
        )

    by_today = api_client.get("/api/v1/papers?date=2026-08-02").json()
    assert "paper-historical" not in {p["id"] for p in by_today}

    all_papers = api_client.get("/api/v1/papers?all=1").json()
    ids = {p["id"] for p in all_papers}
    assert "paper-historical" in ids
    assert "paper-1" in ids
    assert "paper-2" in ids


def test_search_papers_scopes_across_all_dates_and_identifies_remote_only(api_client) -> None:
    """Search must find papers across all dates (not just the selected day) and
    return a structured payload the UI can use to render remote results."""
    # paper-1 is discovered on 2026-08-02 with 'KV cache' in the title.
    local = api_client.get("/api/v1/papers/search?q=KV%20cache")
    assert local.status_code == 200
    payload = local.json()
    assert payload["remote"] is False
    assert any(item["id"] == "paper-1" for item in payload["items"])

    # No local match, no online flag -> empty items without hitting the network.
    no_hit = api_client.get("/api/v1/papers/search?q=zzzz_nonexistent_zzzz")
    assert no_hit.status_code == 200
    assert no_hit.json()["items"] == []
    assert no_hit.json()["remote_searched"] is False


def test_workflow_history_hides_nested_job_paths(api_client, monkeypatch, tmp_path: Path) -> None:
    local_pdf = tmp_path / "private" / "paper.pdf"
    monkeypatch.setattr(
        "research_hub.app.workflow_payload",
        lambda *_: {
            "items": [],
            "runs": [
                {
                    "id": "pipeline-test",
                    "jobs": [
                        {
                            "id": "job-test",
                            "request": {"local_pdf_path": str(local_pdf)},
                            "result": {
                                "path": str(local_pdf),
                                "url": "https://arxiv.org/pdf/1234.5678",
                            },
                        }
                    ],
                }
            ],
        },
    )

    payload = api_client.get("/api/v1/workflows").json()
    job = payload["runs"][0]["jobs"][0]

    assert str(tmp_path) not in json.dumps(payload)
    assert "local_pdf_path" not in job["request"]
    assert "path" not in job["result"]
    assert job["result"]["url"] == "https://arxiv.org/pdf/1234.5678"


def test_adapter_health_reports_local_cnipa_dependency_failure(
    api_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "research_hub.app.LocalCnipaPriorArtAdapter.health",
        lambda _: AdapterResult.degraded("Playwright is unavailable"),
    )

    response = api_client.get("/api/v1/adapter-health")

    prior_art = next(item for item in response.json()["items"] if item["name"] == "prior_art")
    assert prior_art["status"] == "degraded"
    assert prior_art["message"] == "Playwright is unavailable"


def test_workspace_hides_artifact_uri_and_pdf_is_inline_by_default(
    api_client,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "artifacts" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.7\nserver copy\n")
    with api_client.app.state.database.connect() as conn:
        artifact = Repository(conn).create_artifact_for_version(
            "pv-1",
            ArtifactCreate(
                artifact_type="pdf",
                uri=str(pdf_path),
                media_type="application/pdf",
                metadata={"local_pdf_path": str(pdf_path)},
            ),
        )

    workspace = api_client.get("/api/v1/papers/paper-1/workspace")
    inline = api_client.get(f"/api/v1/artifacts/{artifact.id}/download")
    attachment = api_client.get(f"/api/v1/artifacts/{artifact.id}/download?download=true")
    document = api_client.get("/api/v1/paper-versions/pv-1/document")

    assert workspace.status_code == 200
    workspace_artifact = next(item for item in workspace.json()["artifacts"] if item["id"] == artifact.id)
    assert "uri" not in workspace_artifact
    assert "local_pdf_path" not in workspace_artifact["metadata"]
    assert workspace_artifact["download_url"].startswith("/api/v1/artifacts/")
    assert inline.headers["content-disposition"].startswith("inline;")
    assert attachment.headers["content-disposition"].startswith("attachment;")
    assert document.headers["content-disposition"].startswith("inline;")


def test_public_job_payloads_hide_server_paths(api_client, tmp_path: Path) -> None:
    local_pdf = tmp_path / "private" / "paper.pdf"
    with api_client.app.state.database.connect() as conn:
        repository = Repository(conn)
        created = repository.create_job(
            "download",
            "paper_version",
            "pv-1",
            {
                "local_pdf_path": str(local_pdf),
                "source_url": "https://arxiv.org/pdf/1234.5678",
            },
        )
        repository.transition_job_status(created.job_id, "running")
        repository.transition_job_status(
            created.job_id,
            "succeeded",
            result={
                "path": str(local_pdf),
                "nested": {"manifest_path": str(tmp_path / "manifest.json")},
                "url": "https://arxiv.org/pdf/1234.5678",
            },
        )

    listed = api_client.get("/api/v1/jobs").json()["items"]
    detail = api_client.get(f"/api/v1/jobs/{created.job_id}").json()
    listed_job = next(item for item in listed if item["id"] == created.job_id)

    for payload in (listed_job, detail):
        serialized = json.dumps(payload)
        assert str(tmp_path) not in serialized
        assert "local_pdf_path" not in payload["request"]
        assert "path" not in payload["result"]
        assert "manifest_path" not in payload["result"]["nested"]
        assert payload["result"]["url"] == "https://arxiv.org/pdf/1234.5678"


def test_every_write_operation_accepts_idempotency_key(api_client) -> None:
    schema = api_client.get("/openapi.json").json()
    missing = []
    for path, operations in schema["paths"].items():
        for method in {"post", "put", "patch", "delete"} & operations.keys():
            parameters = operations[method].get("parameters", [])
            if not any(
                item.get("in") == "header" and item.get("name") == "Idempotency-Key"
                for item in parameters
            ):
                missing.append(f"{method.upper()} {path}")

    assert missing == []


def test_papers_endpoint_lists_papers(api_client) -> None:
    client = api_client

    response = client.get("/api/v1/papers")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_paper_date_filter_uses_discovery_date_not_publication_date(api_client) -> None:
    with api_client.app.state.database.connect() as conn:
        conn.execute(
            "UPDATE paper SET first_publication_date = '2026-07-30' WHERE id = 'paper-1'"
        )

    discovered = api_client.get("/api/v1/papers?date=2026-08-02").json()
    published = api_client.get("/api/v1/papers?publication_date=2026-07-30").json()

    assert "paper-1" in {item["id"] for item in discovered}
    assert "paper-1" in {item["id"] for item in published}


def test_topic_digest_filters_daily_papers_and_exposes_reading_routes(api_client) -> None:
    response = api_client.get("/api/v1/topics/aif-04/digest?date=2026-08-02")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["papers"] == 1
    assert [item["id"] for item in payload["papers"]] == ["paper-1"]
    assert payload["topic_distribution"] == {"aif-04": 1}
    assert payload["reading_routes"]["30_minutes"] == ["paper-1"]


def test_parse_job_replay_uses_idempotency_key(api_client) -> None:
    client = api_client
    headers = {"Idempotency-Key": "parse-pv-1"}

    first = client.post("/api/v1/paper-versions/pv-1/parse", headers=headers, json={"force": False})
    second = client.post("/api/v1/paper-versions/pv-1/parse", headers=headers, json={"force": False})

    assert first.status_code == second.status_code
    assert first.json()["job_id"] == second.json()["job_id"]


def test_parse_job_idempotency_key_rejects_changed_body(api_client) -> None:
    headers = {"Idempotency-Key": "parse-pv-1-conflict"}

    first = api_client.post(
        "/api/v1/paper-versions/pv-1/parse",
        headers=headers,
        json={"force": False, "options": {"gpu_id": 0}},
    )
    second = api_client.post(
        "/api/v1/paper-versions/pv-1/parse",
        headers=headers,
        json={"force": True, "options": {"gpu_id": 1}},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert "different asynchronous request body" in second.json()["error"]["message"]


def test_parse_job_idempotency_replay_does_not_reschedule_background_task(
    tmp_path, project_root, monkeypatch
) -> None:
    from research_hub import app as app_module

    calls: list[str] = []

    def fake_run_job(self, job_id: str) -> None:
        calls.append(job_id)

    monkeypatch.setattr(app_module.ResearchJobService, "run_job", fake_run_job)
    client = isolated_seeded_client(tmp_path, project_root)
    headers = {"Idempotency-Key": "parse-background-once"}

    first = client.post("/api/v1/paper-versions/pv-1/parse", headers=headers, json={"force": False})
    second = client.post("/api/v1/paper-versions/pv-1/parse", headers=headers, json={"force": False})

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert calls == [first.json()["job_id"]]


def test_artifact_list_responses_hide_raw_locations_and_expose_download_url(api_client) -> None:
    response = api_client.get("/api/v1/artifacts")

    assert response.status_code == 200
    artifact = response.json()["items"][0]
    assert "uri" not in artifact
    assert artifact["download_url"] == f"/api/v1/artifacts/{artifact['id']}/download"
    assert "content" in artifact["metadata"]


def test_file_artifact_list_response_hides_paths_and_download_stays_in_roots(
    tmp_path, project_root, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact_file = artifact_root / "safe.md"
    artifact_file.write_text("# safe\n", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(artifact_root))
    app = create_app(
        Settings(
            database_path=tmp_path / "file-artifact.sqlite3",
            api_key=None,
            static_dir=project_root / "web",
        )
    )
    client = TestClient(app)
    with app.state.database.connect() as conn:
        seed_demo_records(conn)
        conn.execute(
            """
            INSERT INTO artifact (
                id, paper_version_id, artifact_type, uri, media_type, metadata_json
            )
            VALUES (
                'artifact-file-safe',
                'pv-1',
                'markdown',
                ?,
                'text/markdown',
                '{"path": "/tmp/leak.md", "content": "visible"}'
            )
            """,
            (f"file://{artifact_file}",),
        )

    listed = client.get("/api/v1/artifacts/artifact-file-safe/download")
    payload = client.get("/api/v1/artifacts").json()["items"]
    file_item = next(item for item in payload if item["id"] == "artifact-file-safe")

    assert listed.status_code == 200
    assert listed.text == "# safe\n"
    assert "uri" not in file_item
    assert file_item["download_url"] == "/api/v1/artifacts/artifact-file-safe/download"
    assert "path" not in file_item["metadata"]
    assert file_item["metadata"] == {"content": "visible"}


def test_remote_artifact_download_does_not_redirect(tmp_path, project_root) -> None:
    client = isolated_seeded_client(tmp_path, project_root)
    with client.app.state.database.connect() as conn:
        conn.execute(
            """
            INSERT INTO artifact (id, paper_version_id, artifact_type, uri, media_type)
            VALUES (
                'artifact-remote',
                'pv-1',
                'pdf',
                'https://example.test/unsafe.pdf',
                'application/pdf'
            )
            """
        )

    response = client.get("/api/v1/artifacts/artifact-remote/download", follow_redirects=False)

    assert response.status_code == 403
    assert "location" not in response.headers


def test_candidate_requires_human_approval_before_draft(api_client) -> None:
    client = api_client
    candidate = client.post(
        "/api/v1/invention-candidates",
        json=valid_candidate_payload(),
    )
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]

    stages = client.get(f"/api/v1/invention-candidates/{candidate_id}/stages")
    missing = client.get("/api/v1/invention-candidates/missing-candidate/stages")

    draft = client.post(f"/api/v1/invention-candidates/{candidate_id}/draft", json={"case_name": "PD-KV"})

    assert stages.status_code == 200
    assert stages.json() == {"items": []}
    assert missing.status_code == 404
    assert draft.status_code in {400, 409, 422}


def test_approved_candidate_draft_generates_markdown_and_docx_artifacts(api_client) -> None:
    client = api_client
    candidate = client.post(
        "/api/v1/invention-candidates",
        json=valid_candidate_payload(),
    )
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]
    approve = client.post(
        f"/api/v1/invention-candidates/{candidate_id}/approve",
        json=approval_payload(
            notes="approved for draft",
            override_prior_art=True,
            override_reason="API contract test uses explicit manual prior-art override.",
        ),
    )

    draft = client.post(
        f"/api/v1/invention-candidates/{candidate_id}/draft",
        headers={"Idempotency-Key": "patent-draft-api"},
        json={"case_name": "PD-KV", "notes": "保留证据标注"},
    )
    repeated = client.post(
        f"/api/v1/invention-candidates/{candidate_id}/draft",
        headers={"Idempotency-Key": "patent-draft-api"},
        json={"case_name": "PD-KV", "notes": "保留证据标注"},
    )

    assert approve.status_code == 200
    assert draft.status_code == 202
    assert repeated.json()["draft"]["id"] == draft.json()["draft"]["id"]
    payload = draft.json()
    assert payload["job"]["status"] == "succeeded"
    assert "不是法律意义上的新颖性" in payload["draft"]["markdown"]
    assert {item["artifact_type"] for item in payload["artifacts"]["items"]} == {
        "patent_disclosure_markdown",
        "patent_disclosure_docx",
    }
    stages = client.get(f"/api/v1/invention-candidates/{candidate_id}/stages")
    assert stages.status_code == 200
    stage_items = stages.json()["items"]
    assert [item["stage"] for item in stage_items] == [
        "intake",
        "candidate_analysis",
        "prior_art",
        "preview",
        "builder",
        "self_check",
    ]
    assert stage_items[2]["status"] == "skipped"
    assert stage_items[-1]["status"] == "succeeded"
    assert stage_items[-1]["artifact_id"] in {
        item["id"] for item in payload["artifacts"]["items"]
    }
    serialized_stages = json.dumps(stage_items, ensure_ascii=False)
    assert "/data01/" not in serialized_stages
    assert '"path"' not in serialized_stages


def test_candidate_rejects_mechanical_aggregation_without_integration(api_client) -> None:
    response = api_client.post(
        "/api/v1/invention-candidates",
        json=valid_candidate_payload(
            integration_mechanism="简单拼接两个独立模块。",
            coupling_interface="两个模块各自执行，彼此独立。",
            data_or_control_flow="各自处理输入后汇总结果。",
            why_not_juxtaposition="两个模块没有相互影响。",
        ),
    )

    assert response.status_code == 409
    assert "mechanical aggregation" in response.json()["error"]["message"]


def test_artifact_download_returns_non_empty_file(api_client) -> None:
    client = api_client

    response = client.get("/api/v1/artifacts/artifact-1/download")

    assert response.status_code == 200
    assert response.content


def test_approved_candidate_generates_idempotent_markdown_and_docx(api_client) -> None:
    candidate_response = api_client.post(
        "/api/v1/invention-candidates",
        headers={"Idempotency-Key": "candidate-patent-chain"},
        json=valid_candidate_payload(
            title="PD 与投机解码协同控制",
            problem_statement="长上下文推理的资源等待和验证开销需要联合控制。",
            integration_mechanism="根据 prefill/decode 阶段负载动态选择草稿验证批次，并联合迁移 KV cache。",
        ),
    )
    assert candidate_response.status_code == 201
    candidate_id = candidate_response.json()["id"]
    approved = api_client.post(
        f"/api/v1/invention-candidates/{candidate_id}/approve",
        json=approval_payload(
            notes="test approval",
            override_prior_art=True,
            override_reason="API contract test uses explicit manual prior-art override.",
        ),
    )
    assert approved.status_code == 200

    headers = {"Idempotency-Key": "draft-patent-chain"}
    first = api_client.post(
        f"/api/v1/invention-candidates/{candidate_id}/draft",
        headers=headers,
        json={"case_name": "PD-Spec"},
    )
    second = api_client.post(
        f"/api/v1/invention-candidates/{candidate_id}/draft",
        headers=headers,
        json={"case_name": "PD-Spec"},
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["draft"]["id"] == second.json()["draft"]["id"]
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    draft_id = first.json()["draft"]["id"]

    markdown = api_client.get(f"/api/v1/patent-drafts/{draft_id}/export?format=markdown")
    docx = api_client.get(f"/api/v1/patent-drafts/{draft_id}/export?format=docx")
    artifacts = api_client.get(f"/api/v1/patent-drafts/{draft_id}/artifacts")

    assert markdown.status_code == 200
    assert "不是法律意义上的新颖性" in markdown.text
    assert docx.status_code == 200
    assert docx.content.startswith(b"PK")
    assert {item["artifact_type"] for item in artifacts.json()["items"]} == {
        "patent_disclosure_markdown",
        "patent_disclosure_docx",
    }

    revise_headers = {"Idempotency-Key": "revise-patent-chain"}
    revised = api_client.post(
        f"/api/v1/patent-drafts/{draft_id}/revise",
        headers=revise_headers,
        json={"section": "技术方案", "instruction": "补充控制流边界"},
    )
    repeated_revision = api_client.post(
        f"/api/v1/patent-drafts/{draft_id}/revise",
        headers=revise_headers,
        json={"section": "技术方案", "instruction": "补充控制流边界"},
    )

    assert revised.status_code == repeated_revision.status_code == 202
    assert revised.json()["job_id"] == repeated_revision.json()["job_id"]
    revision_job = api_client.get(f"/api/v1/jobs/{revised.json()['job_id']}")
    assert revision_job.json()["status"] == "succeeded"
    revised_draft_id = revision_job.json()["result"]["draft_id"]
    assert revised_draft_id != draft_id
    versions = api_client.get(f"/api/v1/patent-drafts/{draft_id}/versions").json()["items"]
    assert {item["id"] for item in versions} == {draft_id, revised_draft_id}
    assert len({item["version_label"] for item in versions}) == 2
    revised_artifacts = api_client.get(
        f"/api/v1/patent-drafts/{revised_draft_id}/artifacts"
    ).json()["items"]
    assert {item["artifact_type"] for item in revised_artifacts} == {
        "patent_disclosure_markdown",
        "patent_disclosure_docx",
    }
