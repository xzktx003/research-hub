# Research Hub Governance

This document defines the current governance baseline for the Research Hub and
the adjacent source repositories it orchestrates: Dify, MinerU, and the patent
disclosure skill.

## Dependency and License BOM

Run the BOM command from `research-platform`:

```bash
python scripts/license_bom.py --output reports/license-bom.json
```

The default scan covers:

- `../dify`
- `../mineru_service/project/MinerU`
- `../patent-disclosure-skill`

The JSON report records repository presence, root license files with SHA-256
checksums, dependency manifests with SHA-256 checksums, and dependency entries
from Python and npm manifests. CI can override the default repository set with
repeated `--repo NAME=PATH` arguments.

## Sensitive Configuration

No deployment template or committed doc should contain literal API keys,
passwords, access tokens, secrets, or credentialed DSNs. Examples must be blank,
environment-expanded, or explicitly redacted:

```text
RESEARCH_HUB_API_KEY=<set-in-secret-manager>
DIFY_API_KEY=
POSTGRES_PASSWORD=<redacted>
RESEARCH_HUB_POSTGRES_DSN="$RESEARCH_HUB_POSTGRES_DSN"
```

Run the redaction check:

```bash
python scripts/retention.py scan-config --output reports/config-redaction.json
```

Findings include path, line, key, and a stable fingerprint. The value itself is
not written to the report.

## Model and Proxy Policy

External model and workflow providers are optional adapters. Missing Dify or
MinerU configuration must be reported as degraded adapter health, not as fake
success.

Deployment rules:

- Keep `DIFY_API_KEY`, `MINERU_API_KEY`, and any future model-provider keys in a
  secret manager or runtime environment only.
- Keep `DIFY_BASE_URL` and `MINERU_BASE_URL` pointed at private network
  endpoints when possible.
- Route public traffic through a TLS-terminating reverse proxy.
- Forward `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto` headers.
- Do not log request authorization headers or adapter API keys.
- Do not commit proxy credentials, bearer tokens, or credentialed upstream URLs.

## Deployment Policy

Production and shared deployments must set a long random admin key before any
public exposure:

```bash
python - <<'PY'
import secrets
print(f"RESEARCH_HUB_API_KEY={secrets.token_urlsafe(32)}")
PY
```

RBAC keys should be separate per role when more than one operator uses the
system: admin, researcher, patent editor, and read-only. PostgreSQL DSNs and
passwords belong in host secrets, not in `.env.example` or checked-in compose
files.

Run these checks before a release:

- `python scripts/license_bom.py --output reports/license-bom.json`
- `python scripts/retention.py scan-config --output reports/config-redaction.json`
- `python scripts/audit_14_day.py --output reports/operations-audit.json`
- `scripts/public_verify.sh https://YOUR_DOMAIN YYYY-MM-DD`

## Data Retention Policy

SQLite or PostgreSQL data is the durable system of record and must be backed up
before destructive maintenance. Generated artifacts and exports are operational
data and can be retained on a shorter window when reproducible from source
records and upstream services.

Default classes:

- Database backups: retain according to the deployment owner policy.
- Parser and MinerU artifacts: plan deletion after 30 days unless needed for an
  active audit or reproduction.
- Patent draft exports: plan deletion after 30 days unless delivered to the
  reviewer or attached to an active patent workflow.
- Governance reports: retain with deployment evidence.

Generate a dry-run plan:

```bash
python scripts/retention.py plan \
  --older-than-days "${RESEARCH_HUB_RETENTION_DAYS:-30}" \
  --output reports/retention-plan.json
```

Actual deletion requires `--delete`. The implementation only operates under the
configured artifact/export roots, records SHA-256 for every candidate, and
rechecks checksums immediately before unlinking files.
