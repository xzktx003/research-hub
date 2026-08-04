#!/usr/bin/env python3
"""Plan safe artifact/export retention and scan config redaction."""

# Direct execution bootstraps the project root before local-package imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_hub.retention import (
    apply_retention_plan,
    configured_retention_roots,
    plan_retention,
    scan_sensitive_config,
    write_json_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Hub governance utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Plan artifact/export retention")
    plan_parser.add_argument(
        "--older-than-days",
        type=int,
        default=int(os.environ.get("RESEARCH_HUB_RETENTION_DAYS", "30")),
    )
    plan_parser.add_argument("--artifact-root", type=Path, default=None)
    plan_parser.add_argument("--export-root", type=Path, default=None)
    plan_parser.add_argument("--root", action="append", type=Path, default=[], help="Extra allowed root")
    plan_parser.add_argument("--delete", action="store_true", help="Actually delete matching files")
    plan_parser.add_argument("--output", type=Path, default=None)

    scan_parser = subparsers.add_parser("scan-config", help="Check redaction in config templates")
    scan_parser.add_argument(
        "path",
        nargs="*",
        type=Path,
        default=[ROOT / ".env.example", ROOT / "docker-compose.yml", ROOT / "docs"],
    )
    scan_parser.add_argument("--output", type=Path, default=None)

    args = parser.parse_args(argv)
    if args.command == "plan":
        roots = configured_retention_roots(
            artifact_root=args.artifact_root,
            export_root=args.export_root,
            extra_roots=args.root,
        )
        report = plan_retention(roots, older_than_days=args.older_than_days)
        if args.delete:
            report["apply"] = apply_retention_plan(report, delete=True)
            report["mode"] = "delete"
        write_json_report(report, args.output)
        return 0 if report.get("apply", {"status": "ok"})["status"] in {"ok", "partial"} else 1

    report = scan_sensitive_config(args.path)
    write_json_report(report, args.output)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
