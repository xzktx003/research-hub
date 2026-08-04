from __future__ import annotations

import json
from pathlib import Path

from research_hub.retention import build_license_bom


def test_license_bom_collects_repo_licenses_and_python_npm_dependencies(tmp_path: Path) -> None:
    repo = tmp_path / "repo-a"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (repo / "requirements.txt").write_text(
        "fastapi==0.133.1\n# ignored\nhttpx>=0.28\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        """
[project]
dependencies = ["pydantic==2.13.4"]
""".strip(),
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"@scope/pkg": "^1.0.0"},
                "devDependencies": {"vite": "7.0.0"},
            }
        ),
        encoding="utf-8",
    )

    report = build_license_bom({"repo-a": repo})

    assert report["status"] == "ok"
    assert report["summary"]["repo_count"] == 1
    assert report["repos"][0]["licenses"][0]["relative_path"] == "LICENSE"
    dependencies = {
        (item["ecosystem"], item["name"], item["specifier"])
        for item in report["repos"][0]["dependencies"]
    }
    assert ("python", "fastapi", "==0.133.1") in dependencies
    assert ("python", "httpx", ">=0.28") in dependencies
    assert ("python", "pydantic", "==2.13.4") in dependencies
    assert ("npm", "@scope/pkg", "^1.0.0") in dependencies
    assert ("npm", "vite", "7.0.0") in dependencies


def test_license_bom_reports_missing_repos(tmp_path: Path) -> None:
    report = build_license_bom({"missing": tmp_path / "missing"})

    assert report["status"] == "failed"
    assert report["summary"]["missing_repos"] == ["missing"]
