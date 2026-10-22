"""Body records derived from the health monthly Markdown tables.

The old SQLite ``body_entries`` backend is retired (2026-10-22). The health
monthly table (``02_生活运营/01_健康与体重/YYYY-MM.md``) is the authority.

One day may contain several rows. Aggregation is deliberately mechanical:
sum sleep/exercise minutes, keep the last non-empty weight, and ignore rows
that carry no numeric body value. No health inference is performed.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import Any

from . import structured_notes, vault_layout

DATASET = "health"


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _month_keys(first: date, last: date) -> list[str]:
    keys: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def _collect(vault_dir: Path | str, months: list[str], first: date, last: date) -> list[dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for month in months:
        data = structured_notes.read_month(vault_dir, DATASET, month)
        for row in data["rows"]:
            day = _day(row.get("date"))
            if day is None or day < first or day > last:
                continue
            weight = _number(row.get("weight_kg"))
            sleep = _number(row.get("sleep_minutes"))
            exercise = _number(row.get("exercise_minutes"))
            if weight is None and sleep is None and exercise is None:
                continue
            item = aggregated.setdefault(day.isoformat(), {
                "date": day.isoformat(), "recorded": True,
                "weight_kg": None, "sleep_minutes": None, "exercise_minutes": None,
                "revision": 0, "updated_at": None,
            })
            if weight is not None:
                item["weight_kg"] = round(weight, 1)
            if sleep is not None:
                item["sleep_minutes"] = int(item["sleep_minutes"] or 0) + int(sleep)
            if exercise is not None:
                item["exercise_minutes"] = int(item["exercise_minutes"] or 0) + int(exercise)
    return [aggregated[key] for key in sorted(aggregated)]


def entries_between(vault_dir: Path | str, first: date, last: date) -> list[dict[str, Any]]:
    """Recorded body days in the inclusive range, sorted oldest first."""
    if last < first:
        return []
    return _collect(vault_dir, _month_keys(first, last), first, last)


def day_entry(vault_dir: Path | str, day: str) -> dict[str, Any]:
    """One day in the shape consumers used for the retired body table."""
    parsed = _day(day)
    if parsed is None:
        return {"date": day, "recorded": False, "weight_kg": None, "sleep_minutes": None,
                "exercise_minutes": None, "revision": 0, "updated_at": None}
    rows = entries_between(vault_dir, parsed, parsed)
    if rows:
        return rows[0]
    return {"date": parsed.isoformat(), "recorded": False, "weight_kg": None,
            "sleep_minutes": None, "exercise_minutes": None, "revision": 0, "updated_at": None}


def all_entries(vault_dir: Path | str) -> list[dict[str, Any]]:
    """Every body day in the health chapter, across all monthly tables."""
    root = Path(vault_dir) / vault_layout.chapter_dir(DATASET)
    if not root.is_dir():
        return []
    months = sorted(
        path.stem for path in root.glob("*.md")
        if path.is_file() and structured_notes.MONTH_RE.fullmatch(path.stem)
    )
    if not months:
        return []
    first = date.fromisoformat(f"{months[0]}-01")
    last_year, last_month = int(months[-1][:4]), int(months[-1][5:7])
    last = date(last_year, last_month, calendar.monthrange(last_year, last_month)[1])
    return _collect(vault_dir, months, first, last)


__all__ = ["entries_between", "day_entry", "all_entries"]
