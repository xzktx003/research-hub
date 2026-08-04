-- Research Hub PostgreSQL schema v5.
--
-- This is the PostgreSQL counterpart for the current SQLite schema v5. JSON
-- payload columns are stored as JSONB, timestamps use timestamptz, and artifact
-- ownership uniqueness is enforced with partial indexes to avoid NULL semantics
-- differences between SQLite and PostgreSQL.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper (
    id TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    first_publication_date DATE,
    current_version_id TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_identifier (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(identifier_type, identifier_value)
);

CREATE TABLE IF NOT EXISTS paper_version (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    source_version_id TEXT,
    publication_date DATE,
    pdf_url TEXT,
    pdf_checksum TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_id, version_label, source)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_paper_current_version'
    ) THEN
        ALTER TABLE paper
            ADD CONSTRAINT fk_paper_current_version
            FOREIGN KEY (current_version_id) REFERENCES paper_version(id) ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS paper_source_hit (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    paper_version_id TEXT REFERENCES paper_version(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    query TEXT,
    rank INTEGER,
    hit_date DATE,
    raw_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_id, source, query, hit_date)
);

CREATE TABLE IF NOT EXISTS discovery_run (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ,
    topics_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    max_results INTEGER,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    job_id TEXT,
    run_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic (
    id TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_en TEXT NOT NULL,
    parent_id TEXT REFERENCES topic(id) ON DELETE SET NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config_version_id TEXT,
    daily_quota INTEGER,
    aliases_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    rules_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS paper_topic (
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_poll_after TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kind, target_type, target_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS artifact (
    id TEXT PRIMARY KEY,
    paper_version_id TEXT REFERENCES paper_version(id) ON DELETE CASCADE,
    patent_draft_id TEXT,
    artifact_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    checksum TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (num_nonnulls(paper_version_id, patent_draft_id) = 1)
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
    score_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_version_id)
);

CREATE TABLE IF NOT EXISTS paper_relation (
    id TEXT PRIMARY KEY,
    from_paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    to_paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(from_paper_id, to_paper_id, relation_type)
);

CREATE TABLE IF NOT EXISTS invention_candidate (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    source_refs_json JSONB NOT NULL,
    problem_statement TEXT NOT NULL DEFAULT '',
    integration_mechanism TEXT NOT NULL DEFAULT '',
    coupling_interface TEXT NOT NULL DEFAULT '',
    data_or_control_flow TEXT NOT NULL DEFAULT '',
    why_not_juxtaposition TEXT NOT NULL DEFAULT '',
    expected_joint_effect TEXT NOT NULL DEFAULT '',
    technical_effects TEXT NOT NULL DEFAULT '',
    risk_notes TEXT NOT NULL DEFAULT '',
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    gate_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patent_draft (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    case_name TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL,
    markdown TEXT NOT NULL DEFAULT '',
    self_check_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invention_candidate_id, version_label)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_artifact_patent_draft'
    ) THEN
        ALTER TABLE artifact
            ADD CONSTRAINT fk_artifact_patent_draft
            FOREIGN KEY (patent_draft_id) REFERENCES patent_draft(id) ON DELETE CASCADE;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS job_attempt (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    log_location TEXT,
    UNIQUE(job_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS artifact_relation (
    id TEXT PRIMARY KEY,
    source_artifact_id TEXT NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
    derived_artifact_id TEXT NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'derived_from',
    generator TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS technology_claim (
    id TEXT PRIMARY KEY,
    paper_report_id TEXT NOT NULL REFERENCES paper_report(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    claim_type TEXT NOT NULL,
    statement TEXT NOT NULL,
    components_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    constraints_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    effects_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_anchor_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    bibliographic_match BOOLEAN NOT NULL DEFAULT FALSE,
    limitations TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invention_candidate_id, source, publication_number)
);

CREATE TABLE IF NOT EXISTS claim_provenance (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    report_field TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    verified_status TEXT NOT NULL DEFAULT 'unverified',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS human_decision (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_config_version (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_alias (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    config_version_id TEXT REFERENCES topic_config_version(id) ON DELETE SET NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'include',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic_id, config_version_id, alias, alias_type)
);

CREATE TABLE IF NOT EXISTS topic_quota (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    config_version_id TEXT REFERENCES topic_config_version(id) ON DELETE SET NULL,
    quota_type TEXT NOT NULL DEFAULT 'daily',
    max_results INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topic_id, config_version_id, quota_type)
);

CREATE TABLE IF NOT EXISTS topic_digest_note (
    topic_id TEXT NOT NULL REFERENCES topic(id) ON DELETE CASCADE,
    date_value TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (topic_id, date_value)
);

CREATE TABLE IF NOT EXISTS author (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    orcid TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name, orcid)
);

CREATE TABLE IF NOT EXISTS organization (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    ror_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name, ror_id)
);

CREATE TABLE IF NOT EXISTS venue (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    venue_type TEXT NOT NULL DEFAULT 'unknown',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name, venue_type)
);

CREATE TABLE IF NOT EXISTS paper_author (
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    author_id TEXT NOT NULL REFERENCES author(id) ON DELETE CASCADE,
    author_order INTEGER NOT NULL DEFAULT 0,
    is_corresponding BOOLEAN NOT NULL DEFAULT FALSE,
    affiliation_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paper_id, author_id)
);

CREATE TABLE IF NOT EXISTS author_organization (
    author_id TEXT NOT NULL REFERENCES author(id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'affiliation',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (author_id, organization_id, role)
);

CREATE TABLE IF NOT EXISTS paper_venue (
    paper_id TEXT NOT NULL REFERENCES paper(id) ON DELETE CASCADE,
    venue_id TEXT NOT NULL REFERENCES venue(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'published_in',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ,
    input_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidate_component (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    source_ref_index INTEGER NOT NULL,
    component_type TEXT NOT NULL,
    name TEXT NOT NULL,
    contribution TEXT NOT NULL DEFAULT '',
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patent_stage_run (
    id TEXT PRIMARY KEY,
    invention_candidate_id TEXT NOT NULL REFERENCES invention_candidate(id) ON DELETE CASCADE,
    patent_draft_id TEXT REFERENCES patent_draft(id) ON DELETE SET NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_id TEXT REFERENCES artifact(id) ON DELETE SET NULL,
    job_id TEXT REFERENCES job(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invention_candidate_id, stage)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_discovery_run_key ON discovery_run(run_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_version_type_uri
    ON artifact(paper_version_id, artifact_type, uri)
    WHERE paper_version_id IS NOT NULL AND patent_draft_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_draft_type_uri
    ON artifact(patent_draft_id, artifact_type, uri)
    WHERE patent_draft_id IS NOT NULL AND paper_version_id IS NULL;

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

INSERT INTO schema_meta (key, value)
VALUES ('schema_version', '5')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
