"""Persistent runtime configuration for self-hosted Research Hub integrations."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


_env_fallback_values: dict[str, str] = {}


def _load_env_fallback() -> dict[str, str]:
    """Read root .env as a default-variable fallback without overriding real env.

    Returns only keys this module consumes so accidental secrets in other
    variables are not copied into os.environ. Process environment still wins.
    """

    candidate = Path(os.getenv("RESEARCH_HUB_ENV_FILE", str(PROJECT_ROOT / ".env"))).expanduser()
    if not candidate.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                values[key] = value.strip()
    except OSError:
        return {}
    return values


def _env_value(name: str, fallback: str = "") -> str:
    if name in os.environ:
        return os.environ[name]
    return _env_fallback_values.get(name, fallback)


def _env_bool(name: str, default: bool) -> bool:
    value = _env_value(name, "")
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return _bounded_int(_env_value(name, ""), default, minimum, maximum)


def runtime_config_path() -> Path:
    return Path(
        os.getenv(
            "RESEARCH_HUB_RUNTIME_CONFIG",
            str(PROJECT_ROOT / "config" / "runtime_config.json"),
        )
    ).expanduser()


def default_runtime_config() -> dict[str, Any]:
    """Return environment-backed defaults without exposing platform auth keys."""

    global _env_fallback_values
    _env_fallback_values = _load_env_fallback()
    after_parse = [
        item.strip()
        for item in _env_value("RESEARCH_HUB_AFTER_PARSE", "translate").split(",")
        if item.strip() in {"translate"}
    ]
    return {
        "analysis": {
            "provider": _env_value("RESEARCH_HUB_ANALYSIS_PROVIDER", "openai"),
            "openai": {
                "base_url": _env_value("LLM_BASE_URL", "").rstrip("/"),
                "api_key": _env_value("LLM_API_KEY", ""),
                "model": _env_value("LLM_MODEL", ""),
            },
            "dify": {
                "base_url": _env_value("DIFY_BASE_URL", "").rstrip("/"),
                "api_key": _env_value("DIFY_API_KEY", ""),
                "workflow_id": _env_value("DIFY_WORKFLOW_ID", ""),
            },
        },
        "services": {
            "mineru": {
                "base_url": os.getenv("MINERU_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
                "api_key": os.getenv("MINERU_API_KEY", ""),
                "managed_by_server": True,
            },
            "prior_art": {
                "mode": os.getenv("PATENT_PRIOR_ART_MODE", "local"),
                "base_url": os.getenv("PATENT_PRIOR_ART_API_URL", "").rstrip("/"),
                "api_key": os.getenv("PATENT_PRIOR_ART_API_KEY", ""),
                "managed_by_server": True,
            },
        },
        "schedule": {
            "enabled": _env_bool("RESEARCH_HUB_DAILY_ENABLED", True),
            "timezone": _env_value("RESEARCH_HUB_TIMEZONE", "Asia/Shanghai"),
            "daily_hour": _env_int("RESEARCH_HUB_DAILY_HOUR", 9, minimum=0, maximum=23),
            "lookback_days": _env_int(
                "RESEARCH_HUB_DISCOVERY_LOOKBACK_DAYS", 7, minimum=1, maximum=30
            ),
            "max_results": _env_int(
                "RESEARCH_HUB_DAILY_MAX_RESULTS", 5, minimum=1, maximum=100
            ),
            "auto_process": _env_bool("RESEARCH_HUB_AUTO_PROCESS", True),
            "after_parse": after_parse or ["translate"],
        },
    }


def load_runtime_config(path: Path | None = None) -> dict[str, Any]:
    config = default_runtime_config()
    selected = (path or runtime_config_path()).expanduser()
    if not selected.is_file():
        return config
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if isinstance(payload, dict):
        _deep_merge(config, payload)
    return normalize_runtime_config(config)


def update_runtime_config(
    update: dict[str, Any],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Merge an admin update and atomically persist secrets with mode 0600."""

    selected = (path or runtime_config_path()).expanduser()
    config = load_runtime_config(selected)
    sanitized = deepcopy(update)
    for section, key in (
        (("analysis", "openai"), "api_key"),
        (("analysis", "dify"), "api_key"),
    ):
        target = sanitized.get(section[0], {}).get(section[1], {})
        if isinstance(target, dict) and target.get(key) is None:
            target.pop(key, None)
    _deep_merge(config, sanitized)
    config = normalize_runtime_config(config)
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary = selected.with_suffix(f"{selected.suffix}.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(selected)
    os.chmod(selected, 0o600)
    return config


def public_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a browser-safe view with secret values replaced by booleans."""

    payload = deepcopy(normalize_runtime_config(config))
    for group, name in (
        ("analysis", "openai"),
        ("analysis", "dify"),
        ("services", "mineru"),
        ("services", "prior_art"),
    ):
        item = payload[group][name]
        secret = str(item.pop("api_key", "") or "")
        item["api_key_configured"] = bool(secret)
    payload["env_backfilled"] = bool(_env_fallback_values.get("LLM_BASE_URL") or _env_fallback_values.get("DIFY_BASE_URL"))
    return payload


def normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(config)
    analysis = result.setdefault("analysis", {})
    provider = str(analysis.get("provider") or "openai").lower()
    analysis["provider"] = provider if provider in {"openai", "dify"} else "openai"
    for name in ("openai", "dify"):
        item = analysis.setdefault(name, {})
        item["base_url"] = str(item.get("base_url") or "").strip().rstrip("/")
        item["api_key"] = str(item.get("api_key") or "").strip()
    analysis["openai"]["model"] = str(analysis["openai"].get("model") or "").strip()
    analysis["dify"]["workflow_id"] = str(analysis["dify"].get("workflow_id") or "").strip()

    services = result.setdefault("services", {})
    mineru = services.setdefault("mineru", {})
    mineru["base_url"] = str(mineru.get("base_url") or "http://127.0.0.1:8000").strip().rstrip("/")
    mineru["api_key"] = str(mineru.get("api_key") or "").strip()
    mineru["managed_by_server"] = True
    prior_art = services.setdefault("prior_art", {})
    prior_art["mode"] = "remote" if prior_art.get("mode") == "remote" else "local"
    prior_art["base_url"] = str(prior_art.get("base_url") or "").strip().rstrip("/")
    prior_art["api_key"] = str(prior_art.get("api_key") or "").strip()
    prior_art["managed_by_server"] = True

    schedule = result.setdefault("schedule", {})
    schedule["enabled"] = bool(schedule.get("enabled", True))
    schedule["timezone"] = str(schedule.get("timezone") or "Asia/Shanghai").strip()
    schedule["daily_hour"] = _bounded_int(schedule.get("daily_hour"), 9, 0, 23)
    schedule["lookback_days"] = _bounded_int(schedule.get("lookback_days"), 7, 1, 30)
    schedule["max_results"] = _bounded_int(schedule.get("max_results"), 5, 1, 100)
    schedule["auto_process"] = bool(schedule.get("auto_process", True))
    schedule["after_parse"] = [
        item
        for item in dict.fromkeys(schedule.get("after_parse") or ["analyze"])
        if item in {"analyze", "translate"}
    ] or ["analyze"]
    return {
        "analysis": {
            "provider": analysis["provider"],
            "openai": {
                "base_url": analysis["openai"]["base_url"],
                "api_key": analysis["openai"]["api_key"],
                "model": analysis["openai"]["model"],
            },
            "dify": {
                "base_url": analysis["dify"]["base_url"],
                "api_key": analysis["dify"]["api_key"],
                "workflow_id": analysis["dify"]["workflow_id"],
            },
        },
        "services": {
            "mineru": {
                "base_url": mineru["base_url"],
                "api_key": mineru["api_key"],
                "managed_by_server": True,
            },
            "prior_art": {
                "mode": prior_art["mode"],
                "base_url": prior_art["base_url"],
                "api_key": prior_art["api_key"],
                "managed_by_server": True,
            },
        },
        "schedule": {
            "enabled": schedule["enabled"],
            "timezone": schedule["timezone"],
            "daily_hour": schedule["daily_hour"],
            "lookback_days": schedule["lookback_days"],
            "max_results": schedule["max_results"],
            "auto_process": schedule["auto_process"],
            "after_parse": schedule["after_parse"],
        },
    }


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))