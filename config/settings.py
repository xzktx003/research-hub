"""Small environment-driven settings layer.

The project intentionally avoids new dependencies in the first Research Hub
slice, so settings are read from environment variables instead of pydantic
settings or a config framework.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the Research Hub API."""

    database_path: Path
    api_key: str | None
    static_dir: Path
    app_name: str = "AI Infra Research Hub"
    public_mode: bool = False
    admin_api_key: str | None = None
    researcher_api_key: str | None = None
    patent_editor_api_key: str | None = None
    read_only_api_key: str | None = None


def get_settings() -> Settings:
    """Return settings resolved from environment variables."""

    database_path = Path(
        os.environ.get(
            "RESEARCH_HUB_DB",
            str(ROOT_DIR / "config" / "research_hub.sqlite3"),
        )
    ).expanduser()
    static_dir = Path(os.environ.get("RESEARCH_HUB_STATIC_DIR", str(ROOT_DIR / "web"))).expanduser()
    api_key = os.environ.get("RESEARCH_HUB_API_KEY") or None
    public_mode = os.environ.get("RESEARCH_HUB_PUBLIC", "").lower() in {"1", "true", "yes", "on"}
    return Settings(
        database_path=database_path,
        api_key=api_key,
        static_dir=static_dir,
        public_mode=public_mode,
        admin_api_key=os.environ.get("RESEARCH_HUB_ADMIN_API_KEY") or None,
        researcher_api_key=os.environ.get("RESEARCH_HUB_RESEARCHER_API_KEY") or None,
        patent_editor_api_key=os.environ.get("RESEARCH_HUB_PATENT_EDITOR_API_KEY") or None,
        read_only_api_key=os.environ.get("RESEARCH_HUB_READ_ONLY_API_KEY") or None,
    )
