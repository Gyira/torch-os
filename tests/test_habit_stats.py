"""Pure habit statistics (2026-10-28)."""

from datetime import date

from backend.app import habit_stats


def habit(**changes):
    base = {
        "start_date": "2026-10-01",
        "frequency": {"kind": "daily"},
        "target_days": None,
    }
    base.update(changes)
    return base


def entries(*rows):
    return {day: {"date": day, "status": status, "note": note}
            for day, status, note in rows}


def test_range_stats_completion_rate_and_extra_done():
    data = entries(
        ("2026-10-01", "done", ""),
        ("2026-10-02", "skipped", "休息"),
        ("2026-10-03", "unrecorded", ""),
    )
    stats = habit_stats.range_stats(habit(), data, date(2026, 10, 1), date(2026, 10, 3))
    assert (stats["scheduled_days"], stats["done"], stats["skipped"],
            stats["unrecorded"], stats["completion_rate"]) == (3, 1, 1, 1, 0.5)
    # A done record on a day that is no longer scheduled is extra, not numerator.
    weekly = habit(frequency={"kind": "weekly", "weekdays": [0]})
    extra = habit_stats.range_stats(
        weekly, entries(("2026-10-02", "done", "周五补记")),
        date(2026, 10, 1), date(2026, 10, 4))
    assert extra["scheduled_days"] == 0 and extra["extra_done"] == 1
    assert extra["completion_rate"] is None


def test_month_stats_stop_at_today_and_count_extra_done():
    data = entries(
        ("2026-10-01", "done", ""),
        ("2026-10-02", "done", ""),
        ("2026-10-03", "skipped", ""),
        ("2026-10-04", "unrecorded", ""),
    )
    stats = habit_stats.month_stats(habit(), data, "2026-10", date(2026, 10, 4))
    assert stats["month_scheduled"] == 4
    assert stats["month_done"] == 2
    assert stats["month_done_on_scheduled"] == 2
    assert stats["month_extra_done"] == 0
    assert stats["month_skipped"] == 1
    assert stats["month_completion_rate"] == round(2 / 3, 4)
    # Future months are empty rather than negative.
    future = habit_stats.month_stats(habit(), {}, "2026-11", date(2026, 10, 4))
    assert future["month_scheduled"] == 0 and future["month_done"] == 0


def test_streaks_skip_skipped_and_today_unrecorded():
    data = entries(
        ("2026-10-01", "done", ""),
        ("2026-10-02", "done", ""),
        ("2026-10-03", "skipped", ""),
        ("2026-10-04", "done", ""),
    )
    stats = habit_stats.streak_stats(habit(), data, date(2026, 10, 5))
    # Today is unrecorded (not over), skipped is neutral, so the streak is 3.
    assert stats["current_streak"] == 3
    assert stats["best_streak"] == 3
    assert stats["streak_unit"] == "天"

    broken = entries(
        ("2026-10-01", "done", ""),
        ("2026-10-02", "unrecorded", ""),
        ("2026-10-03", "done", ""),
    )
    stats = habit_stats.streak_stats(habit(), broken, date(2026, 10, 4))
    assert stats["current_streak"] == 1
    assert stats["best_streak"] == 1

    weekly = habit(frequency={"kind": "weekly", "weekdays": [0]})
    stats = habit_stats.streak_stats(
        weekly, entries(("2026-10-05", "done", "")), date(2026, 10, 5))
    assert stats["streak_unit"] == "次"


def test_week_strip_aggregates_habits_and_skipped_denominator():
    daily = habit()
    weekly = habit(frequency={"kind": "weekly", "weekdays": [0]})
    # Use ids explicitly; week_strip only reads habit["id"].
    daily["id"] = 1
    weekly["id"] = 2
    entries_by_habit = {
        1: {"2026-10-05": {"date": "2026-10-05", "status": "done"}},
        2: {"2026-10-05": {"date": "2026-10-05", "status": "skipped"}},
    }
    week = habit_stats.week_strip([daily, weekly], entries_by_habit,
                                  date(2026, 10, 5), date(2026, 10, 5), date(2026, 10, 5))
    day = week["days"][0]
    assert day["scheduled"] == 2 and day["done"] == 1 and day["skipped"] == 1
    assert day["completion_rate"] == 1.0


def test_total_done_and_target_stats():
    data = entries(
        ("2026-10-01", "done", ""),
        ("2026-10-01", "done", "同一日期只算一次"),
        ("2026-10-02", "skipped", ""),
    )
    assert habit_stats.habit_total_done(data) == 1
    progress = habit_stats.target_stats(habit(target_days=21), date(2026, 10, 5))
    assert progress["target_elapsed_days"] == 5
    assert progress["target_percent"] == 0.2381
