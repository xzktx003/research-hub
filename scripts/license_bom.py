#!/usr/bin/env python3
"""Emit a dependency and repository-license BOM for connected source repos."""

# Direct execution bootstraps the project root before local-package imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_hub.retention import DEFAULT_SOURCE_REPOS, build_license_bom, write_json_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Hub dependency/license BOM")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Override scanned repos; repeatable. Defaults to Dify, MinerU, and patent skill.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write JSON to this path")
    args = parser.parse_args(argv)

    repos = _parse_repos(args.repo) if args.repo else DEFAULT_SOURCE_REPOS
    report = build_license_bom(repos)
    write_json_report(report, args.output)
    return 0 if report["status"] == "ok" else 1


def _parse_repos(values: list[str]) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--repo must be NAME=PATH, got {value!r}")
        name, raw_path = value.split("=", 1)
        if not name.strip() or not raw_path.strip():
            raise SystemExit(f"--repo must be NAME=PATH, got {value!r}")
        repos[name.strip()] = Path(raw_path.strip())
    return repos


if __name__ == "__main__":
    raise SystemExit(main())
