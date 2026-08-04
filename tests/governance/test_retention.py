from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research_hub.retention import apply_retention_plan, plan_retention


def test_retention_plan_is_dry_run_with_explicit_roots_and_checksums(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    old_file = root / "old.txt"
    old_file.write_text("expired artifact\n", encoding="utf-8")
    fresh_file = root / "fresh.txt"
    fresh_file.write_text("fresh artifact\n", encoding="utf-8")
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    old_timestamp = (now - timedelta(days=45)).timestamp()
    fresh_timestamp = (now - timedelta(days=1)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))
    os.utime(fresh_file, (fresh_timestamp, fresh_timestamp))

    plan = plan_retention([root], older_than_days=30, now=now)
    apply_report = apply_retention_plan(plan)

    assert plan["mode"] == "dry-run"
    assert plan["roots"] == [str(root.resolve())]
    assert plan["candidate_count"] == 1
    assert plan["candidates"][0]["relative_path"] == "old.txt"
    assert len(plan["candidates"][0]["checksum_sha256"]) == 64
    assert apply_report["mode"] == "dry-run"
    assert old_file.exists()
    assert fresh_file.exists()


def test_retention_delete_requires_matching_checksum_inside_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    root.mkdir()
    old_file = root / "old.txt"
    old_file.write_text("expired export\n", encoding="utf-8")
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    old_timestamp = (now - timedelta(days=45)).timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))
    plan = plan_retention([root], older_than_days=30, now=now)

    old_file.write_text("changed after plan\n", encoding="utf-8")
    skipped = apply_retention_plan(plan, delete=True)
    assert skipped["status"] == "partial"
    assert skipped["skipped"][0]["reason"] == "checksum mismatch"
    assert old_file.exists()

    os.utime(old_file, (old_timestamp, old_timestamp))
    refreshed_plan = plan_retention([root], older_than_days=30, now=now)
    deleted = apply_retention_plan(refreshed_plan, delete=True)
    assert deleted["status"] == "ok"
    assert deleted["deleted"][0]["path"] == str(old_file.resolve())
    assert not old_file.exists()
    assert root.exists()


def test_retention_rejects_invalid_age() -> None:
    with pytest.raises(ValueError, match="older_than_days"):
        plan_retention([Path("/tmp")], older_than_days=0)
