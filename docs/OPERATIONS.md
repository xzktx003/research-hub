# Research Hub Operations

This document covers the local operational evidence tools for the Research Hub SQLite database.
They intentionally use only the Python standard library and do not modify application code paths.

## Backup

Create a consistent SQLite backup with row-count, checksum, and `PRAGMA quick_check` evidence:

```bash
python scripts/backup_restore.py backup config/research_hub.$(date +%F).sqlite3
```

Use `--source` to back up a non-default database:

```bash
python scripts/backup_restore.py backup --source /path/to/research_hub.sqlite3 /backups/research_hub.sqlite3
```

The command prints JSON containing:

- `checksum_sha256`: SHA-256 of the backup file.
- `row_counts`: per-table row counts from the backup.
- `integrity_check`: SQLite quick-check result, expected to be `ok`.
- `page_count`: SQLite page count for coarse size evidence.

## Restore

Restore a backup to a target database and verify row counts after restore:

```bash
python scripts/backup_restore.py restore /backups/research_hub.sqlite3 --target /tmp/research_hub.restore.sqlite3 --checksum <sha256>
```

If `--target` is omitted, the configured Research Hub database is replaced. The restore command removes any target WAL/SHM sidecars before writing the restored database. For production use, stop writers before restoring and restore to a staging path first when possible.

## 14-Day Audit

Generate the default 14-day structured audit:

```bash
python scripts/audit_14_day.py --output reports/operations-audit.json
```

Useful options:

```bash
python scripts/audit_14_day.py \
  --database config/research_hub.sqlite3 \
  --end-date 2026-08-02 \
  --expected-job download \
  --expected-job parse \
  --expected-job analyze \
  --expected-job translate
```

The audit checks:

- Daily discovery coverage: at least one `discovery_run` per day in the window.
- Duplicate primary records: duplicate paper titles, identifiers, paper versions, and source versions.
- Unexplained missing tasks: each recent `paper_version` has the expected job kinds unless paper or version metadata explains a skip with `operations.skip_jobs`.
- Failure isolation: failed jobs are allowed when other work in the window still succeeds; failures are flagged as blocking when no independent success evidence exists.

Skip metadata can be attached to `paper.metadata_json` or `paper_version.metadata_json`:

```json
{
  "operations": {
    "skip_jobs": {
      "translate": "translation disabled for this source"
    }
  }
}
```

## Report Contract

Both scripts print or write JSON. Automation should treat top-level `status: "ok"` as pass and any other value as failure. The audit report keeps full evidence under `checks` for incident review.

## Retention Planning

Generate a dry-run retention plan for configured artifact/export roots:

```bash
python scripts/retention.py plan \
  --older-than-days "${RESEARCH_HUB_RETENTION_DAYS:-30}" \
  --output reports/retention-plan.json
```

The plan includes the explicit roots scanned, candidate relative paths, file
sizes, UTC mtimes, reasons, and SHA-256 checksums. The command does not delete
files unless `--delete` is passed.

Deletion is checksum guarded and root confined:

```bash
python scripts/retention.py plan \
  --older-than-days "${RESEARCH_HUB_RETENTION_DAYS:-30}" \
  --delete \
  --output reports/retention-delete.json
```

Only files under `RESEARCH_HUB_ARTIFACT_ROOT`, `RESEARCH_HUB_EXPORT_DIR`, or an
explicit `--root` are eligible. The script revalidates root containment and
file checksums before unlinking any file.

## Governance Checks

Generate the dependency/license BOM for the three connected source repositories:

```bash
python scripts/license_bom.py --output reports/license-bom.json
```

By default this scans `../dify`, `../mineru_service/project/MinerU`, and
`../patent-disclosure-skill`. Use repeated `--repo NAME=PATH` arguments for CI
fixtures or alternate checkouts.

Check deployment templates and docs for literal secrets:

```bash
python scripts/retention.py scan-config --output reports/config-redaction.json
```

The scanner reports file, line, key, and a stable fingerprint only. It does not
echo the suspected secret value into logs.

## Future Integration Points

- Call `backup_sqlite_database` and `restore_sqlite_database` from an admin API or scheduled maintenance job.
- Run `scripts/audit_14_day.py` from cron or CI against a copied production database.
- Store emitted JSON under immutable object storage together with the database backup checksum.
- Store retention and BOM reports with deployment evidence.
- Route audit failures through the alert sink primitives documented in
  `docs/OBSERVABILITY.md`.
- Add alert routing when `checks.daily_runs.missing_dates`,
  `checks.missing_jobs.missing`, or `checks.duplicates.issue_count` is non-empty.

## Background Worker (critical: keep it running)

The FastAPI app itself does **not** consume the job queue in the background. If
you rely solely on `uvicorn research_hub.app:app`, queued jobs (PDF downloads,
parsing, translation) will **not** advance and downloads appear to "stall".
A dedicated scheduler worker must run continuously:

```bash
nohup /data01/home/xuzk/anaconda3/bin/python3.11 \
  scripts/scheduler.py worker --interval 10 --limit 20 \
  > /tmp/research_worker.log 2>&1 &
```

Check it is alive:

```bash
ps -ef | grep "scheduler.py worker" | grep -v grep
```

> Note: `scripts/run_local_stack.sh` starts the API and worker in parallel.
> The worker writes to the DB on startup while the API calls `initialize()`,
> which can contend on the SQLite WAL write lock and abort with
> "database is locked". Prefer starting the API and the the worker in separate
> terminals / processes.

### Queue ordering: fast jobs drain first

`ResearchJobService.run_queued_jobs_once` batches queued jobs with a
kind-priority + FIFO ordering:

- **Fast kinds** (`download`, `render_pdf`, `parse`, `relate`) are picked first,
  oldest-first, so PDF downloads always make progress even when slow LLM jobs
  (`analyze`/`translate`) are stuck waiting on an upstream model response.
- Slow LLM kinds are then drained into the remaining batch slots.

This prevents the symptom where many downloads stay queued forever because the
scheduler kept picking the most recently created `analyze`/`translate` job first
(`created_at DESC`) and blocking on an LLM call. Regression covered by
`tests/test_services.py::test_run_queued_jobs_once_prefers_fast_kinds_fifo`.

### Auto-restart via systemd (user session)

A systemd user unit is provided at
`~/.config/systemd/user/research-worker.service` (Restart=always, proxy env
injected). In a session where `systemctl --user` has a bus it can be enabled
with:

```bash
systemctl --user daemon-reload
systemctl --user enable --now research-worker
```

(The current headless shell may lack a user D-Bus; the unit is still useful in
interactive systemd user sessions.)
