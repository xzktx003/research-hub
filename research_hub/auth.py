"""Role-based API-key authorization for Research Hub routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


PERMISSION_ADMIN = "admin"
PERMISSION_JOBS_MANAGE = "jobs:manage"
PERMISSION_PATENT_WRITE = "patent:write"
PERMISSION_RESEARCH_WRITE = "research:write"

ROLE_ADMIN = "admin"
ROLE_ANONYMOUS = "anonymous"
ROLE_PATENT_EDITOR = "patent-editor"
ROLE_READ_ONLY = "read-only"
ROLE_RESEARCHER = "researcher"


@dataclass(frozen=True)
class Principal:
    """Authenticated request identity available for audit-aware handlers."""

    id: str
    role: str
    permissions: frozenset[str]
    authenticated: bool

    def can(self, permission: str) -> bool:
        return PERMISSION_ADMIN in self.permissions or permission in self.permissions

    def as_audit_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "role": self.role,
            "permissions": sorted(self.permissions),
            "authenticated": self.authenticated,
        }


ANONYMOUS_PRINCIPAL = Principal(
    id="anonymous",
    role=ROLE_ANONYMOUS,
    permissions=frozenset(),
    authenticated=False,
)

LOCAL_ADMIN_PRINCIPAL = Principal(
    id="local-admin",
    role=ROLE_ADMIN,
    permissions=frozenset(
        {
            PERMISSION_ADMIN,
            PERMISSION_JOBS_MANAGE,
            PERMISSION_PATENT_WRITE,
            PERMISSION_RESEARCH_WRITE,
        }
    ),
    authenticated=True,
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_ADMIN: LOCAL_ADMIN_PRINCIPAL.permissions,
    ROLE_RESEARCHER: frozenset({PERMISSION_JOBS_MANAGE, PERMISSION_RESEARCH_WRITE}),
    ROLE_PATENT_EDITOR: frozenset({PERMISSION_JOBS_MANAGE, PERMISSION_PATENT_WRITE}),
    ROLE_READ_ONLY: frozenset(),
}


@dataclass(frozen=True)
class AuthConfig:
    """Resolved API-key identities for one application instance."""

    key_principals: Mapping[str, Principal]
    auth_required: bool
    write_enabled: bool


def build_auth_config(
    *,
    legacy_api_key: str | None = None,
    admin_api_key: str | None = None,
    researcher_api_key: str | None = None,
    patent_editor_api_key: str | None = None,
    read_only_api_key: str | None = None,
) -> AuthConfig:
    """Build the accepted API-key map.

    The legacy ``RESEARCH_HUB_API_KEY`` remains an admin key so existing
    deployments keep their current write access behavior.
    """

    key_principals: dict[str, Principal] = {}
    for role, keys in (
        (ROLE_ADMIN, (legacy_api_key, admin_api_key)),
        (ROLE_RESEARCHER, (researcher_api_key,)),
        (ROLE_PATENT_EDITOR, (patent_editor_api_key,)),
        (ROLE_READ_ONLY, (read_only_api_key,)),
    ):
        for key in _split_keys(keys):
            if key in key_principals:
                raise RuntimeError("Research Hub API keys must be unique across RBAC roles")
            key_principals[key] = Principal(
                id=role,
                role=role,
                permissions=ROLE_PERMISSIONS[role],
                authenticated=True,
            )
    return AuthConfig(
        key_principals=key_principals,
        auth_required=bool(key_principals),
        write_enabled=any(
            principal.can(PERMISSION_RESEARCH_WRITE)
            for principal in key_principals.values()
        ),
    )


def authenticate_api_key(
    auth_config: AuthConfig,
    *,
    x_api_key: str | None = None,
    authorization: str | None = None,
) -> Principal | None:
    """Return the matching principal, anonymous, or ``None`` for bad credentials."""

    if not auth_config.auth_required:
        return LOCAL_ADMIN_PRINCIPAL
    token = _request_token(x_api_key=x_api_key, authorization=authorization)
    if token is None:
        return ANONYMOUS_PRINCIPAL
    return auth_config.key_principals.get(token)


def _request_token(*, x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
        return bearer or None
    return None


def _split_keys(values: tuple[str | None, ...]) -> tuple[str, ...]:
    keys: list[str] = []
    for value in values:
        if not value:
            continue
        keys.extend(key.strip() for key in value.split(",") if key.strip())
    return tuple(keys)
