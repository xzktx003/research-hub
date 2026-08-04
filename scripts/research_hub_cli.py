#!/usr/bin/env python3
"""CLI entry points for Research Hub adapters and importers."""

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

from research_hub.adapters.arxiv import AI_INFRA_TOPICS, ArxivDiscoveryAdapter
from research_hub.adapters.patent import PatentEngineAdapter, candidate_to_dict, technical_card_from_dict
from research_hub.importers.dify_sqlite import DifySQLiteImporter
from research_hub.importers.mineru_manifest import MinerUManifestImporter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Hub integration CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover-arxiv", help="Run arXiv discovery for configured AI Infra topics")
    discover.add_argument("--topic", default=None, help="Topic id; defaults to all configured topics")
    discover.add_argument("--max-results", type=int, default=5)
    discover.add_argument("--api-url", default="https://export.arxiv.org/api/query")

    mineru = sub.add_parser("import-mineru-manifest", help="Import MinerU daily manifest records as JSONL")
    mineru.add_argument("path", help="Manifest file or daliy_pdf root")
    mineru.add_argument("--dry-run", action="store_true", help="Print normalized JSONL without writing a DB")

    dify = sub.add_parser("import-dify-sqlite", help="Import Dify paper_digest SQLite records as JSONL")
    dify.add_argument("path", help="paper_digest sqlite database path")
    dify.add_argument("--dry-run", action="store_true", help="Print normalized JSONL without writing a DB")

    patent = sub.add_parser("patent-candidate", help="Generate a patent candidate from technical-card JSON")
    patent.add_argument("cards_json", help="JSON file containing a list of technical cards")
    patent.add_argument("--markdown-output", default=None, help="Optional disclosure markdown output path")
    patent.add_argument("--docx-output", default=None, help="Optional disclosure docx output path")

    args = parser.parse_args(argv)
    if args.command == "discover-arxiv":
        return _discover_arxiv(args)
    if args.command == "import-mineru-manifest":
        return _import_mineru(args)
    if args.command == "import-dify-sqlite":
        return _import_dify(args)
    if args.command == "patent-candidate":
        return _patent_candidate(args)
    raise AssertionError(args.command)


def _discover_arxiv(args: argparse.Namespace) -> int:
    topics = [topic for topic in AI_INFRA_TOPICS if args.topic in (None, topic.topic_id)]
    if not topics:
        print(json.dumps({"status": "failed", "message": f"unknown topic: {args.topic}"}, ensure_ascii=False))
        return 2
    adapter = ArxivDiscoveryAdapter(api_url=args.api_url)
    for topic in topics:
        topic = topic.__class__(
            topic.topic_id,
            topic.display_name,
            topic.include_terms,
            topic.categories,
            topic.exclude_terms,
            args.max_results,
        )
        print(json.dumps(adapter.discover(topic).__dict__, ensure_ascii=False, default=str))
    return 0


def _import_mineru(args: argparse.Namespace) -> int:
    records = MinerUManifestImporter(args.path).import_records()
    _print_jsonl(record.as_dict() for record in records)
    return 0


def _import_dify(args: argparse.Namespace) -> int:
    importer = DifySQLiteImporter(args.path)
    if not importer.available():
        print(json.dumps({"status": "degraded", "message": f"sqlite database not found: {args.path}"}, ensure_ascii=False))
        return 0
    _print_jsonl(record.as_dict() for record in importer.import_records())
    return 0


def _patent_candidate(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.cards_json).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print(json.dumps({"status": "failed", "message": "cards_json must contain a list"}, ensure_ascii=False))
        return 2
    adapter = PatentEngineAdapter()
    candidate = adapter.build_candidate([technical_card_from_dict(item) for item in raw])
    print(json.dumps(candidate_to_dict(candidate), ensure_ascii=False, indent=2))
    markdown_path = None
    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        result = adapter.write_disclosure(candidate, markdown_path)
        print(json.dumps(result.__dict__, ensure_ascii=False, default=str), file=sys.stderr)
    if args.docx_output:
        if markdown_path is None:
            markdown_path = Path(args.docx_output).with_suffix(".md")
            adapter.write_disclosure(candidate, markdown_path)
        result = adapter.export_docx(markdown_path, args.docx_output)
        print(json.dumps(result.__dict__, ensure_ascii=False, default=str), file=sys.stderr)
    return 0


def _print_jsonl(rows) -> None:
    try:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            return


if __name__ == "__main__":
    raise SystemExit(main())
