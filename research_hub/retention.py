"""Governance helpers for dependency BOMs, config redaction, and retention.

The functions in this module are intentionally filesystem-only and use the
standard library so they can run in CI, cron, and isolated deployment hosts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent

DEFAULT_SOURCE_REPOS: dict[str, Path] = {
    "dify": WORKSPACE_ROOT / "dify",
    "mineru": WORKSPACE_ROOT / "mineru_service" / "project" / "MinerU",
    "patent-disclosure-skill": WORKSPACE_ROOT / "patent-disclosure-skill",
}

DEFAULT_RETENTION_ROOTS = (
    PROJECT_ROOT / "artifacts",
    PROJECT_ROOT / "exports",
)

SENSITIVE_NAME_RE = re.compile(
    r"(API[_-]?KEY|AUTH[_-]?TOKEN|ACCESS[_-]?TOKEN|SECRET|PASSWORD|PASSWD|DSN)",
    re.IGNORECASE,
)
ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Z0-9_][A-Z0-9_.-]*)\s*[:=]\s*(?P<value>.+?)\s*$"
)
REDACTED_VALUES = {
    "",
    "''",
    '""',
    "changeme",
    "change_me",
    "change-me",
    "redacted",
    "<redacted>",
    "<secret>",
    "<set-in-secret-manager>",
    "${secret}",
    "${set_in_secret_manager}",
}


@dataclass(frozen=True)
class RetentionCandidate:
    root: str
    path: str
    relative_path: str
    size_bytes: int
    mtime: str
    checksum_sha256: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "path": self.path,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "checksum_sha256": self.checksum_sha256,
            "reason": self.reason,
        }


def build_license_bom(
    repos: dict[str, Path] | None = None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a dependency and repository-license BOM for source repositories."""

    selected_repos = repos or DEFAULT_SOURCE_REPOS
    report_repos = []
    total_dependencies = 0
    for name, repo_path in selected_repos.items():
        repo_path = repo_path.expanduser().resolve()
        manifests = _dependency_manifests(repo_path) if repo_path.exists() else []
        dependencies: list[dict[str, str]] = []
        for manifest in manifests:
            dependencies.extend(_dependencies_from_manifest(manifest, repo_path))
        dependencies = sorted(
            _dedupe_dicts(dependencies),
            key=lambda item: (item["ecosystem"], item["name"], item["manifest"]),
        )
        total_dependencies += len(dependencies)
        report_repos.append(
            {
                "name": name,
                "path": str(repo_path),
                "exists": repo_path.exists(),
                "licenses": _license_files(repo_path) if repo_path.exists() else [],
                "manifests": [
                    {
                        "path": str(path),
                        "relative_path": path.relative_to(repo_path).as_posix(),
                        "checksum_sha256": file_sha256(path),
                    }
                    for path in manifests
                ],
                "dependencies": dependencies,
            }
        )
    return {
        "status": "ok" if all(repo["exists"] for repo in report_repos) else "failed",
        "generated_at": _iso(generated_at or datetime.now(timezone.utc)),
        "repos": report_repos,
        "summary": {
            "repo_count": len(report_repos),
            "missing_repos": [repo["name"] for repo in report_repos if not repo["exists"]],
            "dependency_count": total_dependencies,
        },
    }


def scan_sensitive_config(paths: Iterable[Path]) -> dict[str, Any]:
    """Scan config-like files for unredacted literal secrets."""

    findings: list[dict[str, Any]] = []
    scanned: list[str] = []
    for path in _iter_scan_files(paths):
        scanned.append(str(path))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            match = ASSIGNMENT_RE.match(line)
            if not match:
                continue
            key = match.group("key").upper()
            if not SENSITIVE_NAME_RE.search(key):
                continue
            value = _strip_inline_comment(match.group("value")).strip()
            if _is_redacted_value(value):
                continue
            findings.append(
                {
                    "path": str(path),
                    "line": line_number,
                    "key": match.group("key"),
                    "fingerprint": hashlib.sha256(
                        f"{path}:{line_number}:{key}".encode("utf-8")
                    ).hexdigest()[:16],
                    "message": "sensitive config value must be blank, env-expanded, "
                    "or a redacted placeholder",
                }
            )
    return {
        "status": "ok" if not findings else "failed",
        "scanned_files": scanned,
        "finding_count": len(findings),
        "findings": findings,
        "generated_at": _iso(datetime.now(timezone.utc)),
    }


def configured_retention_roots(
    *,
    artifact_root: Path | None = None,
    export_root: Path | None = None,
    extra_roots: Iterable[Path] = (),
) -> tuple[Path, ...]:
    """Return explicit artifact/export roots used by the retention planner."""

    roots = [
        artifact_root
        or Path(os.environ.get("RESEARCH_HUB_ARTIFACT_ROOT", DEFAULT_RETENTION_ROOTS[0])),
        export_root or Path(os.environ.get("RESEARCH_HUB_EXPORT_DIR", DEFAULT_RETENTION_ROOTS[1])),
    ]
    roots.extend(extra_roots)
    return tuple(_resolve_path(root) for root in roots)


def plan_retention(
    roots: Iterable[Path],
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a dry-run retention plan with checksums for eligible files."""

    if older_than_days < 1:
        raise ValueError("older_than_days must be >= 1")
    resolved_roots = tuple(dict.fromkeys(_resolve_path(root) for root in roots))
    if not resolved_roots:
        raise ValueError("at least one retention root is required")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    cutoff = reference_time - timedelta(days=older_than_days)

    candidates: list[RetentionCandidate] = []
    for root in resolved_roots:
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            _assert_inside_roots(path, (root,))
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            if mtime > cutoff:
                continue
            candidates.append(
                RetentionCandidate(
                    root=str(root),
                    path=str(path.resolve()),
                    relative_path=path.resolve().relative_to(root).as_posix(),
                    size_bytes=stat.st_size,
                    mtime=_iso(mtime),
                    checksum_sha256=file_sha256(path),
                    reason=f"mtime older than {older_than_days} days",
                )
            )
    return {
        "status": "ok",
        "mode": "dry-run",
        "delete_requires_flag": True,
        "roots": [str(root) for root in resolved_roots],
        "cutoff": _iso(cutoff),
        "candidate_count": len(candidates),
        "total_bytes": sum(candidate.size_bytes for candidate in candidates),
        "candidates": [candidate.as_dict() for candidate in candidates],
        "generated_at": _iso(reference_time),
    }


def apply_retention_plan(plan: dict[str, Any], *, delete: bool = False) -> dict[str, Any]:
    """Apply a retention plan only when delete is explicitly true."""

    roots = tuple(_resolve_path(Path(root)) for root in plan.get("roots", []))
    if not roots:
        raise ValueError("plan does not contain explicit roots")
    if not delete:
        return {
            "status": "ok",
            "mode": "dry-run",
            "deleted": [],
            "skipped": [
                {"path": candidate["path"], "reason": "delete flag not set"}
                for candidate in plan.get("candidates", [])
            ],
        }

    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in plan.get("candidates", []):
        path = _resolve_path(Path(candidate["path"]))
        try:
            _assert_inside_roots(path, roots)
        except ValueError as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
            continue
        if not path.exists():
            skipped.append({"path": str(path), "reason": "missing"})
            continue
        expected_checksum = candidate.get("checksum_sha256")
        actual_checksum = file_sha256(path)
        if actual_checksum != expected_checksum:
            skipped.append(
                {
                    "path": str(path),
                    "reason": "checksum mismatch",
                    "expected_checksum_sha256": expected_checksum,
                    "actual_checksum_sha256": actual_checksum,
                }
            )
            continue
        path.unlink()
        deleted.append({"path": str(path), "checksum_sha256": actual_checksum})
    _remove_empty_directories(roots)
    return {
        "status": "ok" if not skipped else "partial",
        "mode": "delete",
        "deleted": deleted,
        "skipped": skipped,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_report(report: dict[str, Any], output_path: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if output_path is None:
        print(payload)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload + "\n", encoding="utf-8")


def _dependency_manifests(repo_path: Path) -> list[Path]:
    names = {
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
    }
    return [
        path
        for path in sorted(repo_path.rglob("*"))
        if path.is_file()
        and path.name in names
        and "node_modules" not in path.parts
        and "__pycache__" not in path.parts
    ]


def _dependencies_from_manifest(manifest: Path, repo_path: Path) -> list[dict[str, str]]:
    relative = manifest.relative_to(repo_path).as_posix()
    if manifest.name == "requirements.txt":
        return [
            {
                "ecosystem": "python",
                "name": name,
                "specifier": specifier,
                "manifest": relative,
            }
            for name, specifier in _parse_requirements(manifest)
        ]
    if manifest.name == "pyproject.toml":
        return _parse_pyproject(manifest, relative)
    if manifest.name == "package.json":
        return _parse_package_json(manifest, relative)
    if manifest.name == "package-lock.json":
        return _parse_package_lock(manifest, relative)
    if manifest.name == "pnpm-lock.yaml":
        return _parse_pnpm_lock(manifest, relative)
    return []


def _parse_requirements(path: Path) -> list[tuple[str, str]]:
    dependencies = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = _strip_inline_comment(raw_line).strip()
        if not line or line.startswith(("-", "git+", "http://", "https://")):
            continue
        match = re.match(r"(?P<name>[A-Za-z0-9_.-]+)(?P<specifier>.*)", line)
        if match:
            dependencies.append(
                (match.group("name").lower().replace("_", "-"), match.group("specifier").strip())
            )
    return dependencies


def _parse_pyproject(path: Path, relative: str) -> list[dict[str, str]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    dependencies: list[dict[str, str]] = []
    for specifier in data.get("project", {}).get("dependencies", []):
        name, spec = _split_dependency_specifier(specifier)
        dependencies.append(
            {"ecosystem": "python", "name": name, "specifier": spec, "manifest": relative}
        )
    optional = data.get("project", {}).get("optional-dependencies", {})
    for group, values in optional.items():
        for specifier in values:
            name, spec = _split_dependency_specifier(specifier)
            dependencies.append(
                {
                    "ecosystem": "python",
                    "name": name,
                    "specifier": spec,
                    "manifest": relative,
                    "scope": f"optional:{group}",
                }
            )
    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, specifier in poetry_deps.items():
        if name.lower() == "python":
            continue
        dependencies.append(
            {
                "ecosystem": "python",
                "name": name.lower().replace("_", "-"),
                "specifier": str(specifier),
                "manifest": relative,
            }
        )
    return dependencies


def _parse_package_json(path: Path, relative: str) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    dependencies = []
    for scope in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        for name, specifier in data.get(scope, {}).items():
            dependencies.append(
                {
                    "ecosystem": "npm",
                    "name": name,
                    "specifier": str(specifier),
                    "manifest": relative,
                    "scope": scope,
                }
            )
    return dependencies


def _parse_package_lock(path: Path, relative: str) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    dependencies = []
    for package_path, package_data in data.get("packages", {}).items():
        if not package_path.startswith("node_modules/"):
            continue
        name = package_path.removeprefix("node_modules/")
        dependencies.append(
            {
                "ecosystem": "npm",
                "name": name,
                "specifier": str(package_data.get("version", "")),
                "manifest": relative,
            }
        )
    return dependencies


def _parse_pnpm_lock(path: Path, relative: str) -> list[dict[str, str]]:
    dependencies = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line.startswith("/"):
            continue
        package = line.split(":", 1)[0].strip("'\"/")
        if "@" not in package:
            continue
        if package.startswith("@"):
            scope, rest = package.split("/", 1)
            name_part, version = rest.rsplit("@", 1)
            name = f"{scope}/{name_part}"
        else:
            name, version = package.rsplit("@", 1)
        if name:
            dependencies.append(
                {
                    "ecosystem": "npm",
                    "name": name,
                    "specifier": version,
                    "manifest": relative,
                }
            )
    return dependencies


def _split_dependency_specifier(specifier: str) -> tuple[str, str]:
    match = re.match(r"\s*(?P<name>[A-Za-z0-9_.-]+(?:\[[^\]]+\])?)(?P<spec>.*)", specifier)
    if not match:
        return specifier.strip(), ""
    name = match.group("name").split("[", 1)[0].lower().replace("_", "-")
    return name, match.group("spec").strip()


def _license_files(repo_path: Path) -> list[dict[str, str]]:
    return [
        {
            "path": str(path),
            "relative_path": path.relative_to(repo_path).as_posix(),
            "checksum_sha256": file_sha256(path),
        }
        for path in sorted(repo_path.glob("LICENSE*"))
        if path.is_file()
    ]


def _dedupe_dicts(items: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for item in items:
        key = tuple(sorted(item.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _iter_scan_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        path = path.expanduser()
        if path.is_file():
            yield path.resolve()
            continue
        if not path.is_dir():
            continue
        for candidate in sorted(path.rglob("*")):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in {".env", ".example", ".yml", ".yaml", ".toml", ".md"}:
                continue
            yield candidate.resolve()


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None:
            return value[:index]
    return value


def _is_redacted_value(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if normalized in REDACTED_VALUES:
        return True
    if normalized.startswith("${") and normalized.endswith("}"):
        return ":-" not in normalized or normalized.endswith(":-}")
    if normalized.startswith("$"):
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return False


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _assert_inside_roots(path: Path, roots: Iterable[Path]) -> None:
    path = _resolve_path(path)
    for root in roots:
        try:
            path.relative_to(_resolve_path(root))
            return
        except ValueError:
            continue
    raise ValueError(f"{path} is outside configured retention roots")


def _remove_empty_directories(roots: Iterable[Path]) -> None:
    for root in roots:
        if not root.exists():
            continue
        for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
            try:
                path.rmdir()
            except OSError:
                continue


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()
