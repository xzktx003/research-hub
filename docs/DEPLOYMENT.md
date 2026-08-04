# Research Hub Deployment

This service is a FastAPI control plane plus a same-origin static SPA. It keeps
runtime state in SQLite and does not require a frontend build step.

## Files

- `Dockerfile` builds the API and static frontend into one Python image.
- `docker-compose.yml` publishes the service on `RESEARCH_HUB_PUBLIC_PORT`.
- `.env.example` documents runtime configuration without secrets.
- `scripts/public_verify.sh` checks a local or public URL end to end.
- `requirements.txt` pins the minimal runtime dependencies to the versions used
  by the verified local environment.
- `docs/POSTGRESQL.md` documents the opt-in PostgreSQL migration runner,
  dry-run checksum evidence, and local compose profile.

## Local Docker Compose

```bash
cd research-platform
cp .env.example .env
# Required before any public exposure:
python - <<'PY'
import secrets
print(f"RESEARCH_HUB_API_KEY={secrets.token_urlsafe(32)}")
PY
docker compose up --build -d
docker compose ps
scripts/public_verify.sh http://127.0.0.1:8310 2026-07-30
```

The container stores SQLite data at `/data/research_hub.sqlite3`. Compose maps
that path to the named volume `research-hub-data`, so data survives container
rebuilds and restarts.

## Configuration

Core variables:

- `RESEARCH_HUB_PUBLIC_PORT`: host port exposed by Compose. Default: `8310`.
- `RESEARCH_HUB_API_KEY`: legacy admin key for public mode and any public
  tunnel. Mutation endpoints accept `X-API-Key: <key>` or
  `Authorization: Bearer <key>`.
- `RESEARCH_HUB_ADMIN_API_KEY`: explicit admin key.
- `RESEARCH_HUB_RESEARCHER_API_KEY`: research-write key for discovery, paper,
  parse, relation, and job-management routes.
- `RESEARCH_HUB_PATENT_EDITOR_API_KEY`: patent-write key for invention
  candidates and patent draft routes.
- `RESEARCH_HUB_READ_ONLY_API_KEY`: authenticated read-only identity.
- `RESEARCH_HUB_DB`: SQLite path. In containers this should stay under `/data`.
- `RESEARCH_HUB_RUNTIME_CONFIG`: server-side model and scheduler settings. Keep
  it under `/data` so the API and worker share and persist it. Reads mask all
  API keys; writes require the admin role and store the file with mode `0600`.
- `RESEARCH_HUB_POSTGRES_DSN`: optional PostgreSQL runtime and migration DSN.
  A non-empty value automatically selects PostgreSQL at app and worker startup;
  the image includes `psycopg`. Empty or unset keeps the SQLite default.
- `RESEARCH_HUB_STATIC_DIR`: static SPA directory. In the image this is
  `/app/web`.
- `RESEARCH_HUB_EXPORT_DIR`: generated patent Markdown/DOCX artifact root. In
  Compose this is `/app/exports` and is bind-mounted to `./exports`.
- `RESEARCH_HUB_ARTIFACT_ROOT`: parser, MinerU, and intermediate artifact root.
  In Compose this is `/app/artifacts` and is bind-mounted to `./artifacts`.
- `RESEARCH_HUB_RETENTION_DAYS`: default age window for retention planning.
  The current script defaults to 30 days when this is unset.

If RBAC keys are configured, anonymous requests may still read public
endpoints, but protected write routes return `401` or `403` unless the caller
has the matching role. If no RBAC keys are configured, the API preserves its
previous local-only behavior and does not enforce API-key checks.

Role permissions:

- `admin`: all write routes plus dead-letter replay.
- `researcher`: discovery, paper, parse/translate/analyze, relation rebuild, and
  normal job retry/cancel routes.
- `patent-editor`: invention candidate, patent draft, and normal job
  retry/cancel routes.
- `read-only`: authenticated read access only.

Initial adapter defaults (the same values can be changed in the admin Settings page):

- `RESEARCH_HUB_ANALYSIS_PROVIDER` (`openai` or `dify`)
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
- `DIFY_BASE_URL`, `DIFY_API_KEY`, optional `DIFY_WORKFLOW_ID` (standard Dify
  `/v1/workflows/run` uses the application-scoped API key and needs no ID)
- `MINERU_BASE_URL`, `MINERU_API_KEY`
- `ARXIV_RATE_LIMIT_SECONDS`

If the selected analysis provider or MinerU is not configured, `/api/v1/adapter-health` reports the
adapter as degraded. The service should not report fake success for missing
external systems.

## Public Tunnel

For a temporary public URL without adding project dependencies, keep the API
running locally and open an SSH reverse tunnel. Treat this as an ephemeral demo
surface, not durable production ingress.

Before opening the tunnel, configure `RESEARCH_HUB_API_KEY` and restart the API.
The public verifier intentionally fails if anonymous writes are not rejected.

```bash
ssh -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=30 \
  -R 80:127.0.0.1:8310 \
  nokey@localhost.run
```

Copy the HTTPS URL printed by the tunnel, then verify it from the same host:

```bash
scripts/public_verify.sh https://YOUR-SUBDOMAIN.localhost.run 2026-07-30
```

For an optional authenticated no-op write smoke, pass the key only to the
verification process:

```bash
PUBLIC_VERIFY_API_KEY="$RESEARCH_HUB_API_KEY" \
  scripts/public_verify.sh https://YOUR-SUBDOMAIN.localhost.run 2026-07-30
```

## Production Reverse Proxy

Run the container on a private port and terminate TLS in a reverse proxy such as
Nginx, Caddy, or a managed load balancer.

Minimal Nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8310;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Production checklist:

- Set `RESEARCH_HUB_API_KEY` to a long random value.
- Run both `research-hub` and `research-worker`; the API serves requests, while
  the worker executes queued discovery/parse/analyze/patent jobs.
- Back up the `research-hub-data` volume or the SQLite file.
- Keep Dify and MinerU credentials in the host secret manager or deployment
  environment, not in git.
- Keep PostgreSQL credentials and DSNs in the host secret manager or deployment
  environment. `.env.example` uses redacted placeholders intentionally.
- Run `scripts/public_verify.sh https://YOUR_DOMAIN 2026-07-30` after every
  deploy.
- Run `python scripts/retention.py scan-config` before publishing deployment
  templates.
- Monitor `/health` for liveness and `/api/v1/adapter-health` for external
  adapter readiness.

## Daily Scheduler

Compose starts `research-worker` beside the API. The worker executes queued
jobs, polls MinerU tasks, and creates one idempotent daily multi-source discovery run at
`RESEARCH_HUB_DAILY_HOUR` in `RESEARCH_HUB_TIMEZONE` (defaults: 09:00,
Asia/Shanghai). The publication window looks back seven inclusive days by
default so weekends and source publication gaps do not produce an artificially
empty feed; configure it with `RESEARCH_HUB_DISCOVERY_LOOKBACK_DAYS`. Hits remain
filed under the scheduler run date and canonical identity deduplication prevents
rolling windows from duplicating papers. Every new version with a PDF URL is
automatically enqueued for server-side download, MinerU parsing, and structured
analysis. Optional translation is controlled by the Settings page. Missing model/MinerU credentials are
reported as degraded or retryable failures instead of fake successes.

For a host-only deployment, run the same loop directly:

```bash
python scripts/scheduler.py worker --interval 30
```

Example cron shape:

```cron
15 7 * * * cd /opt/research-platform && ./scripts/public_verify.sh http://127.0.0.1:8310 "$(date +\%F)" >> /var/log/research-hub-verify.log 2>&1
```

Manual or backfill discovery remains available through
`python scripts/scheduler.py daily --date YYYY-MM-DD --lookback-days 7` or
`POST /api/v1/discovery-runs` with a stable `Idempotency-Key`.
