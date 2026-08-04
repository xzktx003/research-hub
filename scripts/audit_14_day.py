#!/usr/bin/env python3
"""Emit a structured 14-day Research Hub operations audit report."""

# Direct execution bootstraps the project root before local-package imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from research_hub.operations import (
    DEFAULT_EXPECTED_VERSION_JOBS,
    audit_recent_operations,
    write_json_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Hub 14-day operations audit")
    parser.add_argument("--database", type=Path, default=None, help="SQLite database path; defaults to configured DB")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None, help="Inclusive YYYY-MM-DD audit end date")
    parser.add_argument("--expected-job", action="append", default=[], help="Expected paper_version job kind; repeatable")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON report to this path instead of stdout")
    args = parser.parse_args(argv)

    expected_jobs = tuple(args.expected_job) or DEFAULT_EXPECTED_VERSION_JOBS
    report = audit_recent_operations(
        args.database or get_settings().database_path,
        days=args.days,
        end_date=args.end_date,
        expected_version_jobs=expected_jobs,
    )
    write_json_report(report, args.output)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
