"""Pure statistics for habits (2026-10-28).

The caller supplies plain dictionaries; nothing here reads files or the DB.
Missing records mean ``unrecorded`` (not failure), and ``skipped`` is neutral:
it neither counts as done nor hurts the completion rate or a streak.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .habit_schedule import scheduled_on, target_end_date, target_progress

ENTRY_STATUSES = ("done", "skipped", "unrecorded")


def entry_map(entries: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {str(entry.get("date") or ""): entry for entry in (entries or []) if entry.get("date")}


def _status(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "unrecorded"
    status = str(entry.get("status") or "unrecorded")
    return status if status in ENTRY_STATUSES else "unrecorded"


def _days(first: date, last: date):
    count = (last - first).days
    if count < 0:
        return
    for offset in range(count + 1):
        yield first + timedelta(days=offset)


def _completion_rate(done: int, scheduled: int, skipped: int) -> float | None:
    denominator = scheduled - skipped
    return round(done / denominator, 4) if denominator > 0 else None


def range_stats(habit: dict[str, Any], entries_by_day: dict[str, dict[str, Any]],
                first: date, last: date, today: date | None = None) -> dict[str, Any]:
    """Counts and completion rate for ``first..last`` (inclusive)."""
    counts = {status: 0 for status in ENTRY_STATUSES}
    scheduled = 0
    extra_done = 0
    for day in _days(first, last):
        entry = entries_by_day.get(day.isoformat())
        if scheduled_on(habit, day):
            scheduled += 1
            counts[_status(entry)] += 1
        elif _status(entry) == "done":
            extra_done += 1
    return {
        "scheduled_days": scheduled,
        **counts,
        "extra_done": extra_done,
        "completion_rate": _completion_rate(counts["done"], scheduled, counts["skipped"]),
    }


def month_stats(habit: dict[str, Any], entries_by_day: dict[str, dict[str, Any]],
                month: str, today: date) -> dict[str, Any]:
    """Month-level stats; the current month stops at ``today``.

    ``month_done`` is the number of checked-in days in the month (including
    check-ins on days that are no longer scheduled after a schedule edit),
    matching the "月打卡" number in the habit page.
    """
    try:
        year_text, month_text = str(month).split("-", 1)
        month_start = date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError):
        return {
            "month_scheduled": 0, "month_done": 0, "month_done_on_scheduled": 0,
            "month_extra_done": 0, "month_skipped": 0, "month_unrecorded": 0,
            "month_completion_rate": None,
        }
    next_month = date(month_start.year + (1 if month_start.month == 12 else 0),
                      1 if month_start.month == 12 else month_start.month + 1, 1)
    month_end = next_month - timedelta(days=1)
    effective_end = min(month_end, today)
    if effective_end < month_start:
        return {
            "month_scheduled": 0, "month_done": 0, "month_done_on_scheduled": 0,
            "month_extra_done": 0, "month_skipped": 0, "month_unrecorded": 0,
            "month_completion_rate": None,
        }
    stats = range_stats(habit, entries_by_day, month_start, effective_end, today)
    return {
        "month_scheduled": stats["scheduled_days"],
        "month_done": stats["done"] + stats["extra_done"],
        "month_done_on_scheduled": stats["done"],
        "month_extra_done": stats["extra_done"],
        "month_skipped": stats["skipped"],
        "month_unrecorded": stats["unrecorded"],
        "month_completion_rate": stats["completion_rate"],
    }


def _scheduled_upto(habit: dict[str, Any], today: date) -> list[date]:
    start_text = str(habit.get("start_date") or "")
    try:
        start = date.fromisoformat(start_text[:10])
    except ValueError:
        return []
    if start > today:
        return []
    end = target_end_date(start, habit.get("target_days")) or today
    last = min(today, end)
    if last < start:
        return []
    return [day for day in _days(start, last) if scheduled_on(habit, day)]


def streak_stats(habit: dict[str, Any], entries_by_day: dict[str, dict[str, Any]],
                 today: date) -> dict[str, Any]:
    """Current and best streaks over scheduled days.

    - ``done`` extends a streak.
    - ``skipped`` is neutral (does not break, does not extend).
    - today's ``unrecorded`` is neutral because the day is not over.
    - a past scheduled day left unrecorded breaks the current streak.
    """
    days = _scheduled_upto(habit, today)
    frequency = habit.get("frequency") or {}
    streak_unit = "天" if str(frequency.get("kind") or "daily") == "daily" else "次"

    best = 0
    running = 0
    for day in days:
        status = _status(entries_by_day.get(day.isoformat()))
        if status == "done":
            running += 1
            best = max(best, running)
        elif status == "skipped":
            continue
        elif day != today:
            running = 0

    current = 0
    for day in reversed(days):
        status = _status(entries_by_day.get(day.isoformat()))
        if status == "done":
            current += 1
        elif status == "skipped":
            continue
        elif day == today:
            continue
        else:
            break

    return {"current_streak": current, "best_streak": best, "streak_unit": streak_unit}


def target_stats(habit: dict[str, Any], today: date) -> dict[str, Any]:
    return target_progress(habit.get("start_date"), habit.get("target_days"), today)


def habit_total_done(entries_by_day: dict[str, dict[str, Any]]) -> int:
    return sum(1 for entry in entries_by_day.values() if _status(entry) == "done")


def week_strip(habits: list[dict[str, Any]],
               entries_by_habit: dict[int, dict[str, dict[str, Any]]],
               first: date, last: date, today: date) -> dict[str, Any]:
    days: list[dict[str, Any]] = []
    for day in _days(first, last):
        scheduled = done = skipped = 0
        for habit in habits:
            if not scheduled_on(habit, day):
                continue
            scheduled += 1
            status = _status(entries_by_habit.get(int(habit["id"]), {}).get(day.isoformat()))
            if status == "done":
                done += 1
            elif status == "skipped":
                skipped += 1
        days.append({
            "date": day.isoformat(),
            "weekday": day.weekday(),
            "scheduled": scheduled,
            "done": done,
            "skipped": skipped,
            "completion_rate": _completion_rate(done, scheduled, skipped),
            "is_today": day == today,
        })
    return {"start": first.isoformat(), "end": last.isoformat(), "days": days}


__all__ = [
    "ENTRY_STATUSES", "entry_map", "range_stats", "month_stats", "streak_stats",
    "target_stats", "habit_total_done", "week_strip",
]
