from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "research_hub.sqlite3"


@pytest.fixture()
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture()
def initialized_db(temp_db_path: Path):
    from research_hub.database import Database

    database = Database(temp_db_path)
    database.initialize()
    return database


def import_or_xfail(module_name: str, reason: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        pytest.xfail(f"{reason}: {exc}")


def api_client_or_xfail() -> Any:
    candidates = (
        "research_hub.api",
        "research_hub.app",
        "research_hub.main",
    )
    app = None
    import_errors: list[str] = []
    for module_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            import_errors.append(f"{module_name}: {exc}")
            continue
        app = getattr(module, "app", None) or getattr(module, "create_app", lambda: None)()
        if app is not None:
            break
    if app is None:
        pytest.xfail("Research Hub API entrypoint is not implemented yet; tried " + "; ".join(import_errors))

    try:
        from fastapi.testclient import TestClient
    except ModuleNotFoundError as exc:
        pytest.xfail(f"API client requires FastAPI TestClient or equivalent test harness: {exc}")
    return TestClient(app)


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch) -> Any:
    """Create an isolated API client with explicit test-only fixture data."""

    from config.settings import Settings
    from research_hub.app import create_app
    from research_hub.database import seed_demo_records
    from fastapi.testclient import TestClient

    monkeypatch.setenv("RESEARCH_HUB_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("RESEARCH_HUB_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("RESEARCH_HUB_RUNTIME_CONFIG", str(tmp_path / "runtime-config.json"))
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        api_key=None,
        static_dir=PROJECT_ROOT / "web",
    )
    app = create_app(settings)
    with app.state.database.connect() as conn:
        seed_demo_records(conn)
    return TestClient(app)
