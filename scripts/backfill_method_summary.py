#!/usr/bin/env python3
"""One-time backfill: generate one-line method summaries (method_summary) for
papers that already have a translated_abstract but lack method_summary.

This re-runs the LLM abstract job which now also returns a one-line Chinese
method summary. It is safe to re-run: it only creates a job for papers whose
method_summary is empty, and reuses the same adapter as normal processing.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings  # noqa: E402
from research_hub.database import Database, ensure_schema_compatibility  # noqa: E402
from research_hub.repository import Repository, stable_hash  # noqa: E402
from research_hub.services import ResearchJobService  # noqa: E402


def main() -> None:
    settings = Settings(
        database_path=str(PROJECT_ROOT / "config" / "research_hub.sqlite3"),
        api_key=None,
        static_dir=str(PROJECT_ROOT / "web"),
    )
    db = Database(settings.database_path)
    with db.connect() as conn:
        ensure_schema_compatibility(conn)
        repo = Repository(conn)
        service = ResearchJobService(conn)

        papers = [
            p
            for p in repo.list_papers()
            if p.current_version_id
            and p.abstract.strip()
            and p.translated_abstract
            and not (p.method_summary or "").strip()
        ]
        print(f"papers needing method_summary backfill: {len(papers)}")

        done = 0
        failed = 0
        for paper in papers:
            # Create a fresh abstract job (ignore existing succeeded one).
            created = repo.create_job(
                "translate",
                "paper_version",
                paper.current_version_id,
                {
                    "source": "method_summary_backfill",
                    "mode": "abstract",
                    "paper_id": paper.id,
                },
                idempotency_key=(
                    f"method-summary-backfill:{paper.current_version_id}"
                    f":{stable_hash(paper.abstract)[:16]}"
                ),
            )
            try:
                result = service.run_translate_job(created.job_id)
                saved = repo.get_paper(paper.id)
                if (saved.method_summary or "").strip():
                    done += 1
                    print(f"  OK   {paper.id[:24]} -> {saved.method_summary[:60]}")
                else:
                    failed += 1
                    print(f"  WARN {paper.id[:24]} no method_summary: status={result.get('status')}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  ERR  {paper.id[:24]}: {exc}")

        print(f"\nbackfill done: {done} ok, {failed} failed")


if __name__ == "__main__":
    main()
