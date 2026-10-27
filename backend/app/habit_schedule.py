"""Pure scheduling rules for habits (2026-10-28).

Markdown is the authoritative store; this module never touches the filesystem or
the database.  A habit's schedule is described by a ``frequency`` object:

- ``{"kind": "daily"}``
- ``{"kind": "weekly", "weekdays": [0, 2, 4]}``  (0 = Monday)
- ``{"kind": "monthly", "month_days": [1, 15]}``
- ``{"kind": "interval", "interval_days": 3}``  (anchored on ``start_date``)

``target_days`` is a calendar window, not a check-in count: a 21-day habit is
scheduled from ``start_date`` through ``start_date + 20``.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Iterable

FREQUENCY_KINDS = ("daily", "weekly", "monthly", "interval")
WEEKDAY_LABELS = "一二三四五六日"
MONTH_DAYS_MIN = 1
MONTH_DAYS_MAX = 31
INTERVAL_MIN = 1
INTERVAL_MAX = 365
TARGET_DAYS_MAX = 3650


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _unique_sorted(numbers: Iterable[Any], low: int, high: int) -> list[int]:
    values: list[int] = []
    for raw in numbers:
        number = _as_int(raw)
        if number is None or number < low or number > high:
            raise ValueError(f"数值必须在 {low}–{high} 之间")
        if number in values:
            raise ValueError("数值不能重复")
        values.append(number)
    return sorted(values)


def frequency_from_habit(habit: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical frequency stored on a habit shape.

    Accepts both the API/cache shape (``frequency``) and the legacy
    ``frequency_json`` / ``weekdays`` fields, so callers can pass either.
    """
    raw = habit.get("frequency")
    if isinstance(raw, dict):
        return normalize_frequency(raw)
    raw_json = habit.get("frequency_json")
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return normalize_frequency(parsed)
    legacy = habit.get("weekdays")
    if legacy is None and habit.get("weekdays_json"):
        try:
            legacy = json.loads(habit["weekdays_json"])
        except (TypeError, ValueError):
            legacy = None
    return normalize_frequency(None, legacy)


def normalize_frequency(raw: Any = None, legacy_weekdays: Any = None) -> dict[str, Any]:
    """Validate and canonicalize a frequency rule.

    ``weekly`` with all seven days is normalized to ``daily``.  Unknown keys
    are ignored; missing subfields raise ``ValueError``.
    """
    if raw is None:
        if legacy_weekdays is None:
            return {"kind": "daily"}
        weekdays = _unique_sorted(legacy_weekdays, 0, 6)
        if not weekdays:
            raise ValueError("每周计划至少要选择一天")
        if len(weekdays) == 7:
            return {"kind": "daily"}
        return {"kind": "weekly", "weekdays": weekdays}

    if isinstance(raw, str):
        raw = {"kind": raw}
    if not isinstance(raw, dict):
        raise ValueError("频率必须是对象")

    kind = str(raw.get("kind") or "daily").strip().lower()
    if kind not in FREQUENCY_KINDS:
        raise ValueError("频率只能是每天、每周、每月或每隔 N 天")

    if kind == "daily":
        return {"kind": "daily"}

    if kind == "weekly":
        weekdays = _unique_sorted(raw.get("weekdays") or [], 0, 6)
        if not weekdays:
            raise ValueError("每周计划至少要选择一天")
        if len(weekdays) == 7:
            return {"kind": "daily"}
        return {"kind": "weekly", "weekdays": weekdays}

    if kind == "monthly":
        month_days = _unique_sorted(raw.get("month_days") or [], MONTH_DAYS_MIN, MONTH_DAYS_MAX)
        if not month_days:
            raise ValueError("每月计划至少选择一天")
        return {"kind": "monthly", "month_days": month_days}

    interval_days = _as_int(raw.get("interval_days"))
    if interval_days is None or not INTERVAL_MIN <= interval_days <= INTERVAL_MAX:
        raise ValueError(f"间隔天数必须在 {INTERVAL_MIN}–{INTERVAL_MAX} 之间")
    return {"kind": "interval", "interval_days": interval_days}


def weekdays_for(frequency: dict[str, Any]) -> list[int]:
    kind = str(frequency.get("kind") or "daily")
    if kind == "daily":
        return list(range(7))
    if kind == "weekly":
        return list(frequency.get("weekdays") or [])
    return []


def frequency_label(frequency: dict[str, Any] | None) -> str:
    freq = normalize_frequency(frequency or {"kind": "daily"})
    kind = freq["kind"]
    if kind == "daily":
        return "每天"
    if kind == "weekly":
        return "每周" + "、".join(f"周{WEEKDAY_LABELS[day]}" for day in freq["weekdays"])
    if kind == "monthly":
        return "每月" + "、".join(f"{day}日" for day in freq["month_days"])
    return f"每隔{freq['interval_days']}天"


def target_label(target_days: Any) -> str:
    value = _as_int(target_days)
    return "永远" if value is None or value <= 0 else f"{value}天"


def target_end_date(start_date: Any, target_days: Any) -> date | None:
    start = _as_date(start_date)
    target = _as_int(target_days)
    if start is None or target is None or target <= 0:
        return None
    return start + timedelta(days=target - 1)


def target_progress(start_date: Any, target_days: Any, today: date) -> dict[str, Any]:
    start = _as_date(start_date)
    target = _as_int(target_days)
    if start is None or target is None or target <= 0:
        return {"target_days": None, "target_elapsed_days": None, "target_percent": None}
    elapsed = max(0, min(target, (today - start).days + 1))
    return {
        "target_days": target,
        "target_elapsed_days": elapsed,
        "target_percent": round(elapsed / target, 4),
    }


def scheduled_on(habit: dict[str, Any], day: date) -> bool:
    start = _as_date(habit.get("start_date"))
    if start is None or day < start:
        return False
    end = target_end_date(start, habit.get("target_days"))
    if end is not None and day > end:
        return False
    try:
        frequency = frequency_from_habit(habit)
    except ValueError:
        return False
    kind = frequency["kind"]
    if kind == "daily":
        return True
    if kind == "weekly":
        return day.weekday() in frequency["weekdays"]
    if kind == "monthly":
        return day.day in frequency["month_days"]
    interval = int(frequency["interval_days"])
    return (day - start).days % interval == 0


def scheduled_days(habit: dict[str, Any], first: date, last: date) -> list[date]:
    if last < first:
        return []
    return [first + timedelta(days=offset)
            for offset in range((last - first).days + 1)
            if scheduled_on(habit, first + timedelta(days=offset))]


__all__ = [
    "FREQUENCY_KINDS", "WEEKDAY_LABELS", "normalize_frequency", "frequency_from_habit",
    "weekdays_for", "frequency_label", "target_label", "target_end_date",
    "target_progress", "scheduled_on", "scheduled_days",
]
