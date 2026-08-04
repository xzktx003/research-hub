from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from research_hub.models import (
    CandidateApproveRequest,
    DiscoveryRunCreate,
    DraftCreateRequest,
    EvidenceAnchor,
    InventionCandidateCreate,
    InventionSourceRef,
    PaperCreate,
    PaperVersionCreate,
    PatentStageRunCreate,
    PatentStageRunUpdate,
    PipelineRunCreate,
)
from research_hub.repository import ConflictError, Repository


PATENT_FIELDS = (
    "problem_statement",
    "integration_mechanism",
    "coupling_interface",
    "data_or_control_flow",
    "why_not_juxtaposition",
    "expected_joint_effect",
    "technical_effects",
)


def _seed_paper(repo: Repository, title: str, version_label: str) -> tuple[str, str]:
    paper = repo.create_paper(
        PaperCreate(
            canonical_title=title,
            abstract="runtime scheduling and cache-control paper",
            topics=["aif-04"],
            version=PaperVersionCreate(version_label=version_label, source="manual"),
        )
    )
    assert paper.current_version is not None
    return paper.id, paper.current_version.id


def _candidate_request(version_a: str, version_b: str) -> InventionCandidateCreate:
    evidence = [
        EvidenceAnchor(
            kind="fact",
            source=f"paper_version:{version_a}",
            report_field=field,
            note=f"{field} is grounded in source A",
        )
        for field in PATENT_FIELDS
    ]
    evidence.extend(
        [
            EvidenceAnchor(
                kind="fact",
                source=f"paper_version:{version_b}",
                report_field="integration_mechanism",
                note="source B contributes the cooperating control loop",
            ),
            EvidenceAnchor(
                kind="hypothesis",
                source="analysis:joint-effect",
                report_field="expected_joint_effect",
                note="joint latency effect requires follow-up experiments",
            ),
        ]
    )
    return InventionCandidateCreate(
        title="Coupled cache-aware scheduler",
        sources=[
            InventionSourceRef(
                paper_version_id=version_a,
                contribution="cache residency signal producer",
            ),
            InventionSourceRef(
                paper_version_id=version_b,
                contribution="decode scheduler signal consumer",
            ),
        ],
        problem_statement="Serving systems need latency control across cache and scheduling stages.",
        integration_mechanism="A feedback controller couples cache residency with decode scheduling.",
        coupling_interface="A runtime control interface emits cache residency and pressure signals.",
        data_or_control_flow="Control signals flow from cache manager to scheduler every batch window.",
        why_not_juxtaposition="The scheduler changes admission decisions from cache feedback, so behavior is coupled.",
        expected_joint_effect="Expected lower TTFT and fewer cache evictions under bursty traffic.",
        technical_effects="Lower tail latency and more stable KV cache utilization.",
        risk_notes="Needs validation on multi-tenant serving traces.",
        evidence=evidence,
    )


def test_topic_alias_include_exclude_and_quota_contract(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        config = repo.create_topic_config_version("serving-tuning", active=True)

        repo.add_topic_alias(
            "aif-04",
            "prefill decode disaggregation",
            config_version_id=config.id,
        )
        repo.add_topic_alias(
            "aif-04",
            "biology",
            alias_type="exclude",
            config_version_id=config.id,
        )
        quota = repo.set_topic_quota(
            "aif-04",
            7,
            config_version_id=config.id,
            priority=91,
        )
        contract = repo.topic_search_contract("aif-04", config_version_id=config.id)

    assert quota.max_results == 7
    assert contract["include_terms"] == ["prefill decode disaggregation"]
    assert contract["exclude_terms"] == ["biology"]
    assert contract["daily_quota"] == 7
    assert contract["priority"] == 91


def test_paper_author_organization_and_venue_links_are_first_class(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper_id, _version_id = _seed_paper(repo, "Author Linkage Paper", "v1")
        author = repo.upsert_author("Ada Lovelace", orcid="0000-0001")
        organization = repo.upsert_organization("Example Lab", ror_id="https://ror.org/example")
        venue = repo.upsert_venue("USENIX OSDI", venue_type="conference")

        author_link = repo.link_paper_author(
            paper_id,
            author.id,
            author_order=1,
            is_corresponding=True,
            affiliation_text="Example Lab",
        )
        repo.link_author_organization(author.id, organization.id)
        venue_link = repo.link_paper_venue(paper_id, venue.id)
        organization_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM author_organization
            WHERE author_id = ? AND organization_id = ?
            """,
            (author.id, organization.id),
        ).fetchone()["count"]

    assert author_link.author_order == 1
    assert author_link.is_corresponding is True
    assert venue_link.relation_type == "published_in"
    assert organization_count == 1


def test_pipeline_run_persists_config_version_and_counts(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        discovery = repo.create_discovery_run(
            DiscoveryRunCreate(
                source="scheduled",
                window_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                window_end=datetime(2026, 8, 2, tzinfo=timezone.utc),
                topics=["aif-04"],
                max_results=10,
            ),
            idempotency_key=None,
        )
        run = repo.create_pipeline_run(
            PipelineRunCreate(
                run_type="daily_discovery",
                source="scheduled",
                config_version_id="topic-config-v1",
                discovery_run_id=discovery.id,
                input_counts={"topics": 1},
            )
        )
        completed = repo.update_pipeline_run_counts(
            run.id,
            status="succeeded",
            output_counts={"papers": 8},
            error_counts={"degraded_sources": 1},
        )

    assert completed.config_version_id == "topic-config-v1"
    assert completed.discovery_run_id == discovery.id
    assert completed.input_counts == {"topics": 1}
    assert completed.output_counts == {"papers": 8}
    assert completed.error_counts == {"degraded_sources": 1}


def test_candidate_components_and_integration_mechanism_are_persisted(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        _paper_a, version_a = _seed_paper(repo, "Cache Runtime Paper", "v1")
        _paper_b, version_b = _seed_paper(repo, "Decode Scheduler Paper", "v1")

        candidate = repo.create_invention_candidate(_candidate_request(version_a, version_b))
        components = repo.list_candidate_components(candidate.id)
        mechanisms = repo.list_integration_mechanisms(candidate.id)

    assert [item.contribution for item in components] == [
        "cache residency signal producer",
        "decode scheduler signal consumer",
    ]
    assert len(mechanisms) == 1
    assert mechanisms[0].mechanism_type == "cross_paper_coupling"
    assert "runtime control interface" in mechanisms[0].coupling_interface


def test_explicit_paper_job_and_patent_state_transitions(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        paper_id, _version_id = _seed_paper(repo, "State Machine Paper", "v1")

        repo.transition_paper_status(paper_id, "discovered", reason="source hit merged")
        discovered = repo.transition_paper_status(paper_id, "downloaded", reason="pdf fetched")
        with pytest.raises(ConflictError, match="Illegal paper status transition"):
            repo.transition_paper_status(paper_id, "published")

        job = repo.create_job("parse", "paper", paper_id, {"paper_id": paper_id})
        running = repo.transition_job_status(job.job_id, "running")
        with pytest.raises(ConflictError, match="Illegal job status transition"):
            repo.transition_job_status(job.job_id, "queued")

        _paper_a, version_a = _seed_paper(repo, "Patent State A", "v1")
        _paper_b, version_b = _seed_paper(repo, "Patent State B", "v1")
        candidate = repo.create_invention_candidate(_candidate_request(version_a, version_b))
        conn.execute(
            """
            INSERT INTO job (id, kind, status, target_type, target_id)
            VALUES ('job-prior-art-ok', 'prior_art_check', 'succeeded', 'invention_candidate', ?)
            """,
            (candidate.id,),
        )
        approved = repo.approve_candidate(
            candidate.id,
            CandidateApproveRequest(
                approver="reviewer",
                contribution_confirmed=True,
                sanitization_confirmed=True,
                protection_focus_confirmed=True,
                unverified_facts_confirmed=True,
            ),
        )
        draft, _draft_job = repo.create_patent_draft(
            approved.id,
            DraftCreateRequest(protection_focus="method and system claims"),
            idempotency_key=None,
        )
        under_review = repo.transition_patent_draft_status(draft.id, "under_review")
        with pytest.raises(ConflictError, match="Illegal patent_draft status transition"):
            repo.transition_patent_draft_status(draft.id, "exported")

    assert discovered.status == "downloaded"
    assert running.status == "running"
    assert under_review.status == "under_review"


def test_patent_stage_runs_enforce_order_and_idempotent_updates(initialized_db) -> None:
    with initialized_db.connect() as conn:
        repo = Repository(conn)
        _paper_a, version_a = _seed_paper(repo, "Stage A", "v1")
        _paper_b, version_b = _seed_paper(repo, "Stage B", "v1")
        candidate = repo.create_invention_candidate(_candidate_request(version_a, version_b))

        with pytest.raises(ConflictError, match="requires completed prior stages"):
            repo.record_patent_stage_run(
                candidate.id,
                PatentStageRunCreate(stage="prior_art", status="succeeded"),
            )

        intake = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(
                stage="intake",
                status="succeeded",
                idempotency_key="stage-intake",
                input={"papers": [version_a, version_b]},
                output={"accepted": True},
            ),
        )
        replayed_intake = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(
                stage="intake",
                status="succeeded",
                idempotency_key="stage-intake",
                input={"papers": [version_a, version_b]},
                output={"accepted": True},
            ),
        )
        with pytest.raises(ConflictError, match="different input"):
            repo.record_patent_stage_run(
                candidate.id,
                PatentStageRunCreate(
                    stage="intake",
                    status="succeeded",
                    idempotency_key="stage-intake",
                    input={"papers": [version_b, version_a]},
                ),
            )

        candidate_analysis = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(
                stage="candidate_analysis",
                status="succeeded",
                output={"gate": "ok"},
            ),
        )
        prior_art = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(stage="prior_art", status="running"),
        )
        completed_prior_art = repo.update_patent_stage_run(
            prior_art.id,
            PatentStageRunUpdate(status="succeeded", output={"matches": 0}),
        )
        with pytest.raises(ConflictError, match="Illegal patent_stage_run status transition"):
            repo.update_patent_stage_run(
                prior_art.id,
                PatentStageRunUpdate(status="running"),
            )

        preview = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(stage="preview", status="skipped", output={"reason": "unit test"}),
        )
        builder = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(stage="builder", status="succeeded"),
        )
        self_check = repo.record_patent_stage_run(
            candidate.id,
            PatentStageRunCreate(stage="self_check", status="succeeded"),
        )
        stages = repo.list_patent_stage_runs(candidate.id)
        row_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM patent_stage_run
            WHERE invention_candidate_id = ?
            """,
            (candidate.id,),
        ).fetchone()["count"]

    assert replayed_intake.id == intake.id
    assert candidate_analysis.stage == "candidate_analysis"
    assert completed_prior_art.status == "succeeded"
    assert preview.status == "skipped"
    assert builder.status == "succeeded"
    assert self_check.status == "succeeded"
    assert [item.stage for item in stages] == [
        "intake",
        "candidate_analysis",
        "prior_art",
        "preview",
        "builder",
        "self_check",
    ]
    assert row_count == 6


def test_v3_database_initializes_with_v5_additive_tables(tmp_path) -> None:
    db_path = tmp_path / "legacy-v3.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '3')")
        conn.executescript(
            """
            CREATE TABLE paper (
                id TEXT PRIMARY KEY,
                canonical_title TEXT NOT NULL,
                abstract TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT 'en',
                first_publication_date TEXT,
                current_version_id TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                selected INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE discovery_run (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                topics_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE topic (
                id TEXT PRIMARY KEY,
                name_zh TEXT NOT NULL,
                name_en TEXT NOT NULL,
                parent_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                rules_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE invention_candidate (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                source_refs_json TEXT NOT NULL
            );
            CREATE TABLE patent_draft (
                id TEXT PRIMARY KEY,
                invention_candidate_id TEXT NOT NULL,
                case_name TEXT NOT NULL,
                version_label TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE artifact (
                id TEXT PRIMARY KEY,
                paper_version_id TEXT,
                patent_draft_id TEXT,
                artifact_type TEXT NOT NULL,
                uri TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

    from research_hub.database import Database

    Database(db_path).initialize()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            item[0]
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert row[0] == "6"
    assert {
        "topic_alias",
        "pipeline_run",
        "candidate_component",
        "patent_stage_run",
    }.issubset(tables)
