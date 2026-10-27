"""Pure habit scheduling rules (2026-10-28)."""

from datetime import date

import pytest

from backend.app import habit_schedule as schedule


def habit(**changes):
    base = {
        "start_date": "2026-10-01",
        "frequency": {"kind": "daily"},
        "target_days": None,
    }
    base.update(changes)
    return base


def test_daily_weekly_monthly_interval_and_target_window():
    start = date(2026, 10, 1)  # Thursday
    daily = habit()
    assert schedule.scheduled_on(daily, start)
    assert schedule.scheduled_on(daily, date(2027, 10, 1))

    weekly = habit(frequency={"kind": "weekly", "weekdays": [0, 4]})
    assert schedule.scheduled_on(weekly, date(2026, 10, 2))  # Friday
    assert not schedule.scheduled_on(weekly, date(2026, 10, 3))  # Saturday

    monthly = habit(frequency={"kind": "monthly", "month_days": [1, 15, 31]})
    assert schedule.scheduled_on(monthly, date(2026, 10, 1))
    assert schedule.scheduled_on(monthly, date(2026, 10, 15))
    assert not schedule.scheduled_on(monthly, date(2026, 10, 2))
    assert not schedule.scheduled_on(monthly, date(2026, 9, 30))  # before start
    assert not schedule.scheduled_on(monthly, date(2026, 11, 30))  # no 31st

    interval = habit(frequency={"kind": "interval", "interval_days": 3})
    assert schedule.scheduled_on(interval, date(2026, 10, 1))
    assert not schedule.scheduled_on(interval, date(2026, 10, 2))
    assert schedule.scheduled_on(interval, date(2026, 10, 4))
    assert schedule.scheduled_on(interval, date(2026, 10, 31))

    challenge = habit(target_days=3)
    assert schedule.scheduled_on(challenge, date(2026, 10, 3))
    assert not schedule.scheduled_on(challenge, date(2026, 10, 4))
    assert schedule.target_end_date("2026-10-01", 3) == date(2026, 10, 3)
    assert schedule.target_end_date("2026-10-01", None) is None


def test_scheduled_days_are_inclusive_and_sorted():
    days = schedule.scheduled_days(
        habit(frequency={"kind": "weekly", "weekdays": [0, 2]}),
        date(2026, 10, 1), date(2026, 10, 7))
    assert [day.isoformat() for day in days] == ["2026-10-05", "2026-10-07"]


def test_frequency_normalization_and_labels():
    assert schedule.normalize_frequency(None, [0, 1, 2, 3, 4, 5, 6]) == {"kind": "daily"}
    assert schedule.normalize_frequency(None, [0, 2]) == {
        "kind": "weekly", "weekdays": [0, 2]}
    assert schedule.normalize_frequency({"kind": "monthly", "month_days": [15, 1]}) == {
        "kind": "monthly", "month_days": [1, 15]}
    assert schedule.normalize_frequency({"kind": "interval", "interval_days": 3}) == {
        "kind": "interval", "interval_days": 3}
    assert schedule.frequency_label({"kind": "daily"}) == "每天"
    assert schedule.frequency_label({"kind": "weekly", "weekdays": [0, 2]}) == "每周周一、周三"
    assert schedule.frequency_label({"kind": "monthly", "month_days": [1, 15]}) == "每月1日、15日"
    assert schedule.frequency_label({"kind": "interval", "interval_days": 3}) == "每隔3天"
    assert schedule.target_label(None) == "永远"
    assert schedule.target_label(21) == "21天"


@pytest.mark.parametrize("raw, legacy", [
    ({"kind": "weekly", "weekdays": []}, None),
    ({"kind": "monthly", "month_days": [0]}, None),
    ({"kind": "monthly", "month_days": [32]}, None),
    ({"kind": "interval", "interval_days": 0}, None),
    ({"kind": "interval", "interval_days": 366}, None),
    ({"kind": "unknown"}, None),
    (None, []),
    (None, [1, 1]),
])
def test_invalid_frequency_raises(raw, legacy):
    with pytest.raises(ValueError):
        schedule.normalize_frequency(raw, legacy)


def test_target_progress_clamps_at_window():
    assert schedule.target_progress("2026-10-01", 21, date(2026, 9, 30)) == {
        "target_days": 21, "target_elapsed_days": 0, "target_percent": 0.0}
    progress = schedule.target_progress("2026-10-01", 21, date(2026, 10, 5))
    assert progress["target_elapsed_days"] == 5
    assert progress["target_percent"] == 0.2381
    assert schedule.target_progress("2026-10-01", None, date(2026, 10, 5))[
        "target_days"] is None
