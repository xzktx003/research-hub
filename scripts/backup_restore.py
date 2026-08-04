#!/usr/bin/env python3
"""Create or restore verified Research Hub SQLite backups."""

# Direct execution bootstraps the project root before local-package imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from research_hub.operations import backup_sqlite_database, restore_sqlite_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Hub SQLite backup/restore")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="Create a consistent SQLite backup")
    backup.add_argument("--source", type=Path, default=None, help="SQLite database path; defaults to configured DB")
    backup.add_argument("backup_path", type=Path, help="Destination .sqlite3 backup path")

    restore = sub.add_parser("restore", help="Restore a SQLite backup into a target database")
    restore.add_argument("backup_path", type=Path, help="Source backup .sqlite3 path")
    restore.add_argument("--target", type=Path, default=None, help="Restore target; defaults to configured DB")
    restore.add_argument("--checksum", default=None, help="Expected backup SHA-256 checksum")

    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "backup":
        source = args.source or settings.database_path
        payload = backup_sqlite_database(source, args.backup_path).as_dict()
    elif args.command == "restore":
        target = args.target or settings.database_path
        payload = restore_sqlite_database(args.backup_path, target, expected_checksum=args.checksum)
    else:
        raise AssertionError(args.command)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status", "ok") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
