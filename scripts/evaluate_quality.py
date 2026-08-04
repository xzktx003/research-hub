#!/usr/bin/env python3
"""Evaluate a deterministic Research Hub quality baseline JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_hub.quality import evaluate_acceptance_case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="quality baseline JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = evaluate_acceptance_case(json.loads(args.case.read_text(encoding="utf-8")))
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
