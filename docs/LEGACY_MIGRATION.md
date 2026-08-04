# Legacy Migration Evidence

`scripts/migrate_legacy_sources.py` builds a dry-run import plan for historical
Research Hub sources without mutating the application database. The first
production-safe contract is a deterministic JSON/JSONL bundle that can be
reviewed, checksummed, and reconciled before a later repository-backed apply
step consumes it.

Supported source families:

- Dify `paper_digest` SQLite databases with a `papers` table.
- MinerU daily `manifest.json` files and adjacent directory-tree artifacts.
- Historical patent-disclosure Markdown and DOCX draft exports.

The plan reports:

- Source file checksums.
- Record and artifact counts.
- Proposed canonical IDs.
- Artifact path existence and allowed-root validation.
- Review conflicts where multiple legacy records map to one canonical ID.
- A post-import diff contract based on `import_id` and `payload_checksum`.

Example:

```bash
python scripts/migrate_legacy_sources.py \
  --dify-sqlite ../dify/paper_digest/paper_digest.sqlite3 \
  --mineru-root ../mineru_service/project/daliy_pdf \
  --patent-drafts-root ../exports/patent_drafts \
  --bundle-output /tmp/legacy-import-bundle.jsonl
```

The command defaults to dry-run planning. Passing `--bundle-output` writes the
JSONL bundle and immediately reconciles it against the in-memory plan. A
matched reconciliation means the bundle has the same import IDs and payload
checksums as the plan; it does not mean records have been applied to the
Research Hub database.
