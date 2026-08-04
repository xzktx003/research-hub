# Research Hub PostgreSQL Migrations

PostgreSQL support includes an executable schema contract plus an optional
runtime adapter. The application still defaults to SQLite unless
`RESEARCH_HUB_POSTGRES_DSN` is set.

## Dry Run And Diff

Dry-run mode has no PostgreSQL driver dependency. It lists pending migrations,
statement counts, migration checksums, and a combined plan checksum:

```bash
python -m research_hub.postgres --dry-run --json
```

To compare the current SQLite schema against the PostgreSQL DDL and record the
source database checksum:

```bash
python -m research_hub.postgres \
  --dry-run \
  --sqlite config/research_hub.sqlite3 \
  --json
```

The `sqlite_diff.ok` field must be `true` before treating the PostgreSQL DDL as
schema-compatible with the SQLite control-plane database.

## Local PostgreSQL Service

The compose PostgreSQL service is disabled by default so existing SQLite
development remains unchanged. Start it explicitly with the `postgres` profile:

```bash
docker compose --profile postgres up -d postgres
```

Use a secret-manager value for the local DSN. `.env.example` intentionally
stores only a redacted placeholder:

```text
RESEARCH_HUB_POSTGRES_DSN=<set-in-secret-manager>
```

For host-side commands against the published port, use:

```bash
RESEARCH_HUB_POSTGRES_DSN="$RESEARCH_HUB_POSTGRES_DSN" \
  python -m research_hub.postgres --json
```

Live migration mode requires `psycopg` or `psycopg2`. The application image
installs `psycopg[binary]`; dry-run and SQLite diff mode still use only the
Python standard library.

## Runtime Adapter

`research_hub.postgres_runtime.PostgresRuntimeDatabase` exposes the same
`initialize()` and `connect()` shape as `research_hub.database.Database`.
Connections translate existing qmark SQL placeholders to PostgreSQL DB-API
`%s` placeholders, return dict/index-addressable rows for the repository, and
commit or roll back at the context-manager boundary.

App startup is already wired through `create_database_from_env()`. A non-empty
`RESEARCH_HUB_POSTGRES_DSN` selects PostgreSQL and applies pending migrations;
an empty or unset value selects SQLite. If a custom host environment omits both
supported drivers, startup raises an actionable `PostgresRuntimeDependencyError`.

Known SQLite-specific query paths that still need live PostgreSQL QA or small
query rewrites before broad production use:

- Direct service and observability SQL uses the same qmark placeholder style as
  the repository; it is supported by the wrapper, but these paths have not been
  exercised against a live PostgreSQL server in this slice.
- PostgreSQL drivers decode JSONB to Python dictionaries/lists; the shared JSON
  loader accepts both decoded values and SQLite JSON text.

## Compose Runtime

Set a real password and matching service-network DSN, then start the profile:

```bash
POSTGRES_PASSWORD='replace-with-secret' \
RESEARCH_HUB_POSTGRES_DSN='postgresql://research_hub:replace-with-secret@postgres:5432/research_hub' \
docker compose --profile postgres up --build -d
```

Do not leave placeholder DSNs in `.env`; an empty DSN intentionally keeps the
default SQLite runtime.

## Smoke Contract

Successful live migration output includes:

- `plan.pending_count`: number of migrations applied in that run.
- `plan.plan_checksum`: checksum of ordered migration versions and contents.
- `smoke.ok`: `true` when core tables exist and `schema_meta.schema_version` is
  `5`.

The `schema_migrations` table records each migration version, name, checksum,
and application timestamp.
