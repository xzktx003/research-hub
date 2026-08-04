"""SQLite connection and schema management."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 6


def dumps(value: Any) -> str:
    """Serialize structured values consistently for SQLite JSON columns."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: Any, default: Any = None) -> Any:
    """Deserialize JSON text while accepting driver-decoded JSON values."""

    if value is None or value == "":
        return default
    if not isinstance(value, (str, bytes, bytearray)):
        return value
    return json.loads(value)


class Database:
    """Thin sqlite3 wrapper used by repositories and FastAPI dependencies."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            ensure_schema_compatibility(conn)
            ensure_artifact_unique_indexes(conn)
            conn.execute(
                """
                INSERT INTO schema_meta (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            seed_topics(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # FastAPI may enter and exit a synchronous generator dependency on
        # different worker threads.  Each request still owns a dedicated
        # connection, so allowing that connection to cross the dependency
        # lifecycle boundary is safe and avoids sqlite3.ProgrammingError.
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def ensure_artifact_unique_indexes(conn: sqlite3.Connection) -> None:
    """Create artifact uniqueness indexes after checking legacy rows."""

    duplicate_checks = (
        (
            "paper_version artifact",
            """
            SELECT paper_version_id AS owner_id, artifact_type, uri, COUNT(*) AS count
            FROM artifact
            WHERE paper_version_id IS NOT NULL AND patent_draft_id IS NULL
            GROUP BY paper_version_id, artifact_type, uri
            HAVING COUNT(*) > 1
            LIMIT 1
            """,
        ),
        (
            "patent_draft artifact",
            """
            SELECT patent_draft_id AS owner_id, artifact_type, uri, COUNT(*) AS count
            FROM artifact
            WHERE patent_draft_id IS NOT NULL AND paper_version_id IS NULL
            GROUP BY patent_draft_id, artifact_type, uri
            HAVING COUNT(*) > 1
            LIMIT 1
            """,
        ),
    )
    for label, query in duplicate_checks:
        row = conn.execute(query).fetchone()
        if row:
            raise RuntimeError(
                f"Cannot create unique {label} index; duplicate rows exist for "
                f"owner={row['owner_id']} type={row['artifact_type']} uri={row['uri']!r}"
            )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_version_type_uri
        ON artifact(paper_version_id, artifact_type, uri)
        WHERE paper_version_id IS NOT NULL AND patent_draft_id IS NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_draft_type_uri
        ON artifact(patent_draft_id, artifact_type, uri)
        WHERE patent_draft_id IS NOT NULL AND paper_version_id IS NULL
        """
    )


def ensure_schema_compatibility(conn: sqlite3.Connection) -> None:
    """Apply additive schema updates for databases created by older builds."""

    paper_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(paper)").fetchall()
    }
    if "translated_abstract" not in paper_columns:
        conn.execute("ALTER TABLE paper ADD COLUMN translated_abstract TEXT")
    if "method_summary" not in paper_columns:
        conn.execute("ALTER TABLE paper ADD COLUMN method_summary TEXT")

    candidate_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(invention_candidate)").fetchall()
    }
    for name in (
        "coupling_interface",
        "data_or_control_flow",
        "why_not_juxtaposition",
        "expected_joint_effect",
    ):
        if name not in candidate_columns:
            conn.execute(
                f"ALTER TABLE invention_candidate ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
            )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_patent_draft_candidate_version
        ON patent_draft(invention_candidate_id, version_label)
        """
    )

    discovery_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(discovery_run)").fetchall()
    }
    if "run_key" not in discovery_columns:
        conn.execute("ALTER TABLE discovery_run ADD COLUMN run_key TEXT")
        conn.execute("UPDATE discovery_run SET run_key = id WHERE run_key IS NULL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_discovery_run_key ON discovery_run(run_key)"
    )

    topic_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(topic)").fetchall()
    }
    if "config_version_id" not in topic_columns:
        conn.execute("ALTER TABLE topic ADD COLUMN config_version_id TEXT")
    if "daily_quota" not in topic_columns:
        conn.execute("ALTER TABLE topic ADD COLUMN daily_quota INTEGER")
    if "deleted_at" not in topic_columns:
        conn.execute("ALTER TABLE topic ADD COLUMN deleted_at TEXT")

    conn.executescript(CORE_STATE_SCHEMA_SQL)

def seed_topics(conn: sqlite3.Connection) -> None:
    topics = [
        (
            "aif-01",
            "高效模型与架构",
            "Efficient Models and Architectures",
            None,
            ["MoE", "GQA", "MLA", "Mamba", "linear attention"],
            12,
            {"daily_quota": 12, "priority": 80},
        ),
        (
            "aif-02",
            "模型压缩",
            "Model Compression",
            None,
            ["quantization", "pruning", "distillation", "KV cache quantization"],
            12,
            {"daily_quota": 12, "priority": 85},
        ),
        (
            "aif-03",
            "推理解码算法",
            "Inference and Decoding Algorithms",
            None,
            ["speculative decoding", "Medusa", "multi-token prediction"],
            12,
            {"daily_quota": 12, "priority": 90},
        ),
        (
            "aif-04",
            "推理服务与运行时",
            "Inference Serving and Runtime",
            None,
            ["PD disaggregation", "prefill decode", "continuous batching", "paged attention"],
            16,
            {"daily_quota": 16, "priority": 95},
        ),
        (
            "aif-05",
            "编译器、算子与 Kernel",
            "Compilers, Operators, and Kernels",
            None,
            ["kernel fusion", "Triton", "FlashAttention", "GEMM"],
            14,
            {"daily_quota": 14, "priority": 90},
        ),
        (
            "aif-06",
            "训练基础设施",
            "Training Infrastructure",
            None,
            ["FSDP", "ZeRO", "tensor parallel", "checkpointing"],
            10,
            {"daily_quota": 10, "priority": 75},
        ),
        (
            "aif-07",
            "硬件、存储与网络",
            "Hardware, Storage, and Networking",
            None,
            ["GPU", "NPU", "HBM", "CXL", "RDMA", "NVLink"],
            10,
            {"daily_quota": 10, "priority": 75},
        ),
        (
            "aif-08",
            "评测、可靠性与成本",
            "Evaluation, Reliability, and Cost",
            None,
            ["TTFT", "TPOT", "SLO", "profiling", "cost per token"],
            8,
            {"daily_quota": 8, "priority": 70},
        ),
        (
            "aif-09",
            "数据与存储基础设施",
            "Data and Storage Infrastructure",
            None,
            ["dataset curation", "data deduplication", "checkpoint storage", "vector database"],
            8,
            {"daily_quota": 8, "priority": 65},
        ),
        (
            "aif-10",
            "多模态与端侧基础设施",
            "Multimodal and Edge Infrastructure",
            None,
            ["VLM serving", "edge inference", "on-device AI", "multimodal pipeline"],
            8,
            {"daily_quota": 8, "priority": 65},
        ),
        (
            "aif-11",
            "调度、资源管理与集群运营",
            "Scheduling, Resource Management, and Cluster Operations",
            None,
            ["GPU scheduling", "Kubernetes", "multi-tenant", "autoscaling"],
            10,
            {"daily_quota": 10, "priority": 80},
        ),
        (
            "aif-12",
            "安全、隔离与供应链",
            "Security, Isolation, and Supply Chain",
            None,
            ["sandboxing", "model supply chain", "runtime isolation", "confidential inference"],
            6,
            {"daily_quota": 6, "priority": 60},
        ),
    ]
    conn.execute(
        """
        INSERT INTO topic_config_version (id, label, active, metadata_json)
        VALUES ('topic-config-v1', 'AI Infra MVP topic tree', 1, ?)
        ON CONFLICT(id) DO UPDATE SET
            label = excluded.label,
            active = excluded.active,
            metadata_json = excluded.metadata_json
        """,
        (dumps({"seeded": True, "schema_version": SCHEMA_VERSION}),),
    )
    for topic_id, name_zh, name_en, parent_id, aliases, daily_quota, rules in topics:
        conn.execute(
            """
            INSERT INTO topic (
                id, name_zh, name_en, parent_id, enabled, config_version_id,
                daily_quota, aliases_json, rules_json
            )
            VALUES (?, ?, ?, ?, 1, 'topic-config-v1', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name_zh = excluded.name_zh,
                name_en = excluded.name_en,
                parent_id = excluded.parent_id,
                config_version_id = COALESCE(topic.config_version_id, excluded.config_version_id),
                daily_quota = COALESCE(topic.daily_quota, excluded.daily_quota),
                aliases_json = excluded.aliases_json,
                rules_json = excluded.rules_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                topic_id,
                name_zh,
                name_en,
                parent_id,
                daily_quota,
                dumps(aliases),
                dumps(rules),
            ),
        )
        for alias in aliases:
            conn.execute(
                """
                INSERT INTO topic_alias (
                    id, topic_id, config_version_id, alias, alias_type, weight
                )
                VALUES (?, ?, 'topic-config-v1', ?, 'include', 1.0)
                ON CONFLICT(topic_id, config_version_id, alias, alias_type) DO NOTHING
                """,
                (
                    f"talias_{topic_id}_{hashlib.sha256(alias.encode('utf-8')).hexdigest()[:16]}",
                    topic_id,
                    alias,
                ),
            )
        conn.execute(
            """
            INSERT INTO topic_quota (
                id, topic_id, config_version_id, quota_type, max_results, priority
            )
            VALUES (?, ?, 'topic-config-v1', 'daily', ?, ?)
            ON CONFLICT(topic_id, config_version_id, quota_type) DO UPDATE SET
                max_results = excluded.max_results,
                priority = excluded.priority
            """,
            (
                f"tquota_{topic_id}_daily",
                topic_id,
                daily_quota,
                int(rules.get("priority") or 50),
            ),
        )


def seed_demo_records(conn: sqlite3.Connection) -> None:
    """Seed a tiny live dataset for UI/API smoke tests.

    These records are deliberately small and clearly marked as demo metadata.
    They give the same-origin frontend and API contract tests a non-empty
    backend without depending on external paper services.
    """

    now = "2026-08-02T00:00:00+00:00"
    papers = [
        (
            "paper-1",
            "PD Disaggregation with KV Cache Runtime Control",
            "Demo AI Infra paper about prefill/decode disaggregation and KV cache placement.",
            "aif-04",
            "pv-1",
        ),
        (
            "paper-2",
            "Speculative Decoding Scheduler for Efficient Serving",
            "Demo AI Infra paper about draft verification and serving schedulers.",
            "aif-03",
            "pv-2",
        ),
    ]
    for paper_id, title, abstract, topic_id, version_id in papers:
        conn.execute(
            """
            INSERT INTO paper (
                id, canonical_title, abstract, language, first_publication_date,
                current_version_id, status, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, 'en', '2026-08-02', ?, 'discovered', ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (paper_id, title, abstract, version_id, dumps({"demo": True}), now, now),
        )
        conn.execute(
            """
            INSERT INTO paper_version (
                id, paper_id, version_label, source, source_version_id,
                publication_date, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, 'v1', 'demo', ?, '2026-08-02', ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (version_id, paper_id, version_id, dumps({"demo": True}), now, now),
        )
        conn.execute(
            """
            INSERT INTO paper_topic (paper_id, topic_id, evidence_json)
            VALUES (?, ?, ?)
            ON CONFLICT(paper_id, topic_id) DO NOTHING
            """,
            (paper_id, topic_id, dumps({"demo": True})),
        )
        conn.execute(
            """
            INSERT INTO paper_source_hit (id, paper_id, paper_version_id, source, query, rank, hit_date, raw_summary_json)
            VALUES (?, ?, ?, 'demo', 'ai infra', 1, '2026-08-02', ?)
            ON CONFLICT(paper_id, source, query, hit_date) DO NOTHING
            """,
            (f"hit-{paper_id}", paper_id, version_id, dumps({"demo": True})),
        )
    conn.execute(
        """
        INSERT INTO artifact (
            id, paper_version_id, artifact_type, uri, media_type, checksum, metadata_json
        )
        VALUES ('artifact-1', 'pv-1', 'markdown', 'inline://demo-artifact-1', 'text/markdown', NULL, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (dumps({"demo": True, "content": "# Demo Artifact\n\nResearch Hub artifact browser is live.\n"}),),
    )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_record (
    key TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    translated_abstract TEXT,
    method_summary TEXT,
    language TEXT NOT NULL DEFAULT 'en',
    first_publication_date TEXT,
    current_version_id TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    selected INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_identifier (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(identifier_type, identifier_value)
);

CREATE TABLE IF NOT EXISTS paper_version (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    source_version_id TEXT,
    publication_date TEXT,
    pdf_url TEXT,
    pdf_checksum TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_id, version_label, source)
);

CREATE TABLE IF NOT EXISTS paper_source_hit (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    paper_version_id TEXT REFERENCES paper_version(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    query TEXT,
    rank INTEGER,
    hit_date TEXT,
    raw_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_id, source, query, hit_date)
);

CREATE TABLE IF NOT EXISTS discovery_run (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    topics_json TEXT NOT NULL DEFAULT '[]',
    max_results INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    job_id TEXT,
    run_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic (
    id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_en TEXT NOT NULL,
    parent_id TEXT REFERENCES topic(id) ON DELETE SET NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_version_id TEXT,
    daily_quota INTEGER,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    rules_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_topic (
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paper_id, topic_id)
);

CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    idempotency_key TEXT,
    external_task_id TEXT,
    request_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT NOT NULL DEFAULT '{}',
    next_poll_after TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, target_type, target_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS artifact (
    id TEXT PRIMARY KEY,
    paper_version_id TEXT REFERENCES paper_version(id) ON DELETE CASCADE,
    patent_draft_id TEXT REFERENCES patent_draft(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    checksum TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_version_id, patent_draft_id, artifact_type, uri)
);

CREATE TABLE IF NOT EXISTS paper_report (
    id TEXT PRIMARY KEY,
    paper_version_id TEXT NOT NULL REFERENCES paper_version(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    motivation TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    experiments TEXT NOT NULL DEFAULT '',
    results TEXT NOT NULL DEFAULT '',
    innovation TEXT NOT NULL DEFAULT '',
    limitations TEXT NOT NULL DEFAULT '',
    engineering_value TEXT NOT NULL DEFAULT '',
    reproduction_plan TEXT NOT NULL DEFAULT '',
    score_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_version_id)
);

CREATE TABLE IF NOT EXISTS paper_relation (
    id TEXT PRIMARY KEY,
    from_paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    to_paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(from_paper_id, to_paper_id, relation_type)
);

CREATE TABLE IF NOT EXISTS invention_candidate (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    problem_statement TEXT NOT NULL DEFAULT '',
    integration_mechanism TEXT NOT NULL DEFAULT '',
    coupling_interface TEXT NOT NULL DEFAULT '',
    data_or_control_flow TEXT NOT NULL DEFAULT '',
    why_not_juxtaposition TEXT NOT NULL DEFAULT '',
    expected_joint_effect TEXT NOT NULL DEFAULT '',
    technical_effects TEXT NOT NULL DEFAULT '',
    risk_notes TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    gate_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patent_draft (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    case_name TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL,
    markdown TEXT NOT NULL DEFAULT '',
    self_check_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_attempt (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    log_location TEXT,
    UNIQUE(job_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS artifact_relation (
    id TEXT PRIMARY KEY,
    source_artifact_id TEXT NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
    derived_artifact_id TEXT NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'derived_from',
    generator TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_artifact_id, derived_artifact_id, relation_type)
);

CREATE TABLE IF NOT EXISTS evidence_anchor (
    id TEXT PRIMARY KEY,
    paper_report_id TEXT NOT NULL REFERENCES paper_report(id) ON DELETE CASCADE,
    report_field TEXT,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    section TEXT,
    page TEXT,
    quote TEXT,
    quote_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS technology_claim (
    id TEXT PRIMARY KEY,
    paper_report_id TEXT NOT NULL REFERENCES paper_report(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    claim_type TEXT NOT NULL,
    statement TEXT NOT NULL,
    components_json TEXT NOT NULL DEFAULT '[]',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    effects_json TEXT NOT NULL DEFAULT '[]',
    evidence_anchor_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_report_id, claim_type)
);

CREATE TABLE IF NOT EXISTS prior_art_record (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    url TEXT NOT NULL,
    abstract TEXT NOT NULL,
    analysis_basis TEXT NOT NULL,
    bibliographic_match INTEGER NOT NULL DEFAULT 0,
    limitations TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invention_candidate_id, source, publication_number)
);

CREATE TABLE IF NOT EXISTS claim_provenance (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    report_field TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    verified_status TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS human_decision (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_paper_status ON paper(status);
CREATE INDEX IF NOT EXISTS idx_paper_version_paper ON paper_version(paper_id);
CREATE INDEX IF NOT EXISTS idx_artifact_paper_version ON artifact(paper_version_id);
CREATE INDEX IF NOT EXISTS idx_job_target ON job(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);
CREATE INDEX IF NOT EXISTS idx_job_attempt_job ON job_attempt(job_id, attempt_no);
CREATE INDEX IF NOT EXISTS idx_evidence_anchor_report ON evidence_anchor(paper_report_id, report_field);
CREATE INDEX IF NOT EXISTS idx_technology_claim_paper ON technology_claim(paper_id, claim_type);
CREATE INDEX IF NOT EXISTS idx_prior_art_candidate ON prior_art_record(invention_candidate_id);
CREATE INDEX IF NOT EXISTS idx_claim_provenance_candidate ON claim_provenance(invention_candidate_id, report_field);
"""


CORE_STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS topic_config_version (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_alias (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    config_version_id TEXT REFERENCES topic_config_version(id) ON DELETE SET NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'include',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic_id, config_version_id, alias, alias_type)
);

CREATE TABLE IF NOT EXISTS topic_quota (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    config_version_id TEXT REFERENCES topic_config_version(id) ON DELETE SET NULL,
    quota_type TEXT NOT NULL DEFAULT 'daily',
    max_results INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic_id, config_version_id, quota_type)
);

CREATE TABLE IF NOT EXISTS topic_digest_note (
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    date_value TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (topic_id, date_value)
);

CREATE TABLE IF NOT EXISTS author (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    orcid TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name, orcid)
);

CREATE TABLE IF NOT EXISTS organization (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    ror_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name, ror_id)
);

CREATE TABLE IF NOT EXISTS venue (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    venue_type TEXT NOT NULL DEFAULT 'unknown',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name, venue_type)
);

CREATE TABLE IF NOT EXISTS paper_author (
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    author_id TEXT NOT NULL REFERENCES author(id) ON DELETE CASCADE,
    author_order INTEGER NOT NULL DEFAULT 0,
    is_corresponding INTEGER NOT NULL DEFAULT 0,
    affiliation_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paper_id, author_id)
);

CREATE TABLE IF NOT EXISTS author_organization (
    author_id TEXT NOT NULL REFERENCES author(id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'affiliation',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (author_id, organization_id, role)
);

CREATE TABLE IF NOT EXISTS paper_venue (
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    venue_id TEXT NOT NULL REFERENCES venue(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'published_in',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paper_id, venue_id, relation_type)
);

CREATE TABLE IF NOT EXISTS pipeline_run (
    id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    config_version_id TEXT REFERENCES topic_config_version(id) ON DELETE SET NULL,
    discovery_run_id TEXT REFERENCES discovery_run(id) ON DELETE SET NULL,
    job_id TEXT REFERENCES job(id) ON DELETE SET NULL,
    window_start TEXT,
    window_end TEXT,
    input_counts_json TEXT NOT NULL DEFAULT '{}',
    output_counts_json TEXT NOT NULL DEFAULT '{}',
    error_counts_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidate_component (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    source_ref_index INTEGER NOT NULL,
    component_type TEXT NOT NULL,
    name TEXT NOT NULL,
    contribution TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invention_candidate_id, source_ref_index, component_type, name)
);

CREATE TABLE IF NOT EXISTS integration_mechanism_record (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    mechanism_type TEXT NOT NULL DEFAULT 'cross_paper_coupling',
    coupling_interface TEXT NOT NULL,
    data_or_control_flow TEXT NOT NULL,
    why_not_juxtaposition TEXT NOT NULL,
    expected_joint_effect TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patent_stage_run (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    patent_draft_id TEXT REFERENCES patent_draft(id) ON DELETE SET NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    artifact_id TEXT REFERENCES artifact(id) ON DELETE SET NULL,
    job_id TEXT REFERENCES job(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invention_candidate_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_topic_alias_topic ON topic_alias(topic_id, alias_type);
CREATE INDEX IF NOT EXISTS idx_topic_quota_topic ON topic_quota(topic_id, quota_type);
CREATE INDEX IF NOT EXISTS idx_author_name ON author(normalized_name);
CREATE INDEX IF NOT EXISTS idx_paper_author_author ON paper_author(author_id);
CREATE INDEX IF NOT EXISTS idx_paper_venue_venue ON paper_venue(venue_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_status ON pipeline_run(status, run_type);
CREATE INDEX IF NOT EXISTS idx_candidate_component_candidate ON candidate_component(invention_candidate_id);
CREATE INDEX IF NOT EXISTS idx_integration_mechanism_candidate ON integration_mechanism_record(invention_candidate_id);
CREATE INDEX IF NOT EXISTS idx_patent_stage_run_candidate ON patent_stage_run(invention_candidate_id, stage);
CREATE INDEX IF NOT EXISTS idx_patent_stage_run_status ON patent_stage_run(status, stage);
"""
