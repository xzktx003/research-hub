from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from research_hub.services import _hit_date
from scripts.scheduler import _daily_window


def test_daily_window_uses_inclusive_seven_day_lookback() -> None:
    selected = datetime(2026, 8, 2, 13, 30, tzinfo=timezone.utc)

    window_start, window_end = _daily_window(selected, 7)

    assert window_start == datetime(2026, 7, 27, tzinfo=timezone.utc)
    assert window_end == datetime(2026, 8, 3, tzinfo=timezone.utc)


def test_discovery_hit_date_uses_scheduler_run_date_not_window_start() -> None:
    run = SimpleNamespace(
        metadata={"hit_date": "2026-08-02"},
        window_start="2026-07-27T00:00:00+00:00",
        window_end="2026-08-03T00:00:00+00:00",
    )

    assert _hit_date(run) == date(2026, 8, 2)