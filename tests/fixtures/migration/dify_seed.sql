CREATE TABLE papers (
    version_id TEXT PRIMARY KEY,
    arxiv_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    run_date TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT,
    metadata_json TEXT,
    relevance_json TEXT,
    score_json TEXT,
    status TEXT,
    pdf_path TEXT,
    review_path TEXT,
    score REAL,
    updated_at TEXT
);

INSERT INTO papers (
    version_id, arxiv_id, version, run_id, run_date, title, abstract,
    metadata_json, relevance_json, score_json, status, pdf_path, review_path,
    score, updated_at
) VALUES (
    '2608.10001v1',
    '2608.10001',
    1,
    'run-2026-08-01',
    '2026-08-01',
    'Speculative Decode Scheduling for AI Infrastructure',
    'A scheduling paper for speculative decoding.',
    '{"authors":["Ada Chen"],"categories":["cs.DC"]}',
    '{"decision":"keep"}',
    '{"overall":8.5}',
    'scored',
    'papers/speculative-decode.pdf',
    'reviews/speculative-decode.md',
    8.5,
    '2026-08-01T09:10:00Z'
);
