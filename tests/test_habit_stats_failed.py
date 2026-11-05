"""新增 failed 状态后的统计口径：失败不计完成、计入分母、打断连续。"""

from __future__ import annotations

from datetime import date

from backend.app import habit_stats

HABIT = {
    "id": 1,
    "start_date": "2026-11-01",
    "target_days": None,
    "frequency": {"kind": "daily"},
}


def test_range_stats_counts_failed_in_denominator_but_not_done():
    entries = {
        "2026-11-01": {"status": "done"},
        "2026-11-02": {"status": "failed"},
        "2026-11-03": {"status": "done"},
    }
    stats = habit_stats.range_stats(
        HABIT, entries, date(2026, 11, 1), date(2026, 11, 3),
        today=date(2026, 11, 3))
    assert stats["done"] == 2
    assert stats["failed"] == 1
    assert stats["scheduled_days"] == 3
    assert stats["completion_rate"] == round(2 / 3, 4)


def test_failed_breaks_current_streak():
    entries = {
        "2026-11-01": {"status": "done"},
        "2026-11-02": {"status": "failed"},
    }
    streak = habit_stats.streak_stats(HABIT, entries, date(2026, 11, 2))
    assert streak["current_streak"] == 0
    assert streak["best_streak"] == 1


def test_skipped_remains_neutral_and_not_in_denominator():
    entries = {
        "2026-11-01": {"status": "done"},
        "2026-11-02": {"status": "skipped"},
        "2026-11-03": {"status": "done"},
    }
    stats = habit_stats.range_stats(
        HABIT, entries, date(2026, 11, 1), date(2026, 11, 3),
        today=date(2026, 11, 3))
    assert stats["skipped"] == 1
    assert stats["completion_rate"] == 1.0
    streak = habit_stats.streak_stats(HABIT, entries, date(2026, 11, 3))
    assert streak["current_streak"] == 2


def test_month_stats_reports_failed():
    entries = {"2026-11-02": {"status": "failed"}}
    stats = habit_stats.month_stats(HABIT, entries, "2026-11", date(2026, 11, 5))
    assert stats["month_failed"] == 1
    assert stats["month_done"] == 0
