# Research Hub Observability

Research Hub observability is implemented in `research_hub.observability` and
uses only the Python standard library. It is safe for the API process, scheduler,
cron jobs, and tests.

## Capabilities

- Trace context: `trace_context`, `trace_from_headers`, `current_trace_context`,
  `X-Trace-Id`, and `X-Request-Id`.
- Structured logs: `configure_json_logging` and `log_event` emit one JSON object
  per line with trace fields.
- In-process metrics: `MetricsRegistry`, `METRICS`, `collect_job_metrics`, and
  `render_json` or `render_text`.
- Database-backed business gauges: discovery source success ratio, download
  success ratio, average parse duration, Dify model token usage, report success
  ratio, and patent candidate pass ratio.
- Dead-letter jobs: `list_dead_letter_jobs`, `dead_letter_payload`, and
  `replay_dead_letter_job` operate on existing `job` and `job_attempt` rows.
- Alert hooks: `AlertSink`, `InMemoryAlertSink`, `LoggingAlertSink`,
  `WebhookAlertSink`, `emit_alert`, and `alert_on_dead_letters`.

## Scheduler Usage

The scheduler now attaches a trace to every one-shot command and worker loop
iteration. Add `--json-logs` when the process manager expects structured stderr:

```bash
python scripts/scheduler.py --json-logs run-queued --limit 10
python scripts/scheduler.py --json-logs worker --interval 30
```

The command response includes `trace` and `metrics` keys. Worker iterations print
the same shape on each loop so a process manager can scrape the output without an
extra dependency.

## Manual Dead-Letter Replay

The helper below lists failed jobs and requeues one after an operator verifies
the upstream issue is fixed:

```python
from research_hub.database import Database
from research_hub.observability import dead_letter_payload, replay_dead_letter_job

database = Database("config/research_hub.sqlite3")
with database.connect() as conn:
    print(dead_letter_payload(conn, limit=50))
    replay_dead_letter_job(conn, "job_...", reason="MinerU endpoint restored")
```

`replay_dead_letter_job` delegates to the existing repository retry contract, so
it preserves idempotent request payloads and records `_retry.retry_reason`.

## App Endpoints

The API app wires trace middleware and exposes operational read endpoints:

- `GET /api/v1/metrics`: returns the in-process metrics snapshot after
  collecting database-backed job and business gauges. Business metric names are
  `research_hub_source_success_ratio`, `research_hub_download_success_ratio`,
  `research_hub_parse_duration_ms_avg`, `research_hub_model_tokens_total`,
  `research_hub_report_success_ratio`, and
  `research_hub_patent_candidate_pass_ratio`.
- `GET /api/v1/jobs/dead-letter?limit=100`: lists failed jobs requiring operator
  attention.
- `POST /api/v1/jobs/dead-letter/{job_id}/replay`: requeues a failed job through
  the existing retry contract. This route requires the `admin` role because it
  can restart any failed job.

Every response includes `X-Trace-Id`. If the caller sends `X-Request-Id`, the
response echoes it as `X-Request-Id`.

## Alert Hook

Alerts are intentionally sink-based. Production can start with structured logs:

```python
from research_hub.observability import LoggingAlertSink, alert_on_dead_letters

alert_on_dead_letters(conn, [LoggingAlertSink()])
```

For a webhook receiver:

```python
from research_hub.observability import WebhookAlertSink, alert_on_dead_letters

alert_on_dead_letters(conn, [WebhookAlertSink("https://alerts.example.internal/hooks/research-hub")])
```
