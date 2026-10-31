"""Body records derived from the health system Markdown (2026-10-31).

The old SQLite ``body_entries`` backend is retired (2026-10-22). Two Markdown
sources are read here, in order:

1. `02_生活运营/02_健康系统/01_每日健康/YYYY-MM-DD.md` — the current authority:
   sleep duration, weight and training minutes come from the daily record;
2. `02_生活运营/02_健康系统/<YYYY-MM>.md`（旧 `01_健康与体重`）— only for vaults
   that still have the retired monthly table and no daily records.

Aggregation is deliberately mechanical: one day = one row, keep the last
non-empty weight, sum sleep/training minutes, ignore days with no numeric body
value. No health inference is performed.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import Any

from . import structured_notes, vault_layout

DATASET = "health"
DAILY_CHAPTER = "health_daily"
MAX_DAILY_FILES = 2000


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


def _empty(day: str, recorded: bool = False) -> dict[str, Any]:
    return {"date": day, "recorded": recorded, "weight_kg": None,
            "sleep_minutes": None, "exercise_minutes": None,
            "revision": 0, "updated_at": None}


def _daily_rows(vault_dir: Path | str, first: date | None = None,
                last: date | None = None) -> list[dict[str, Any]]:
    """Read the health system daily notes; optional inclusive date filter."""
    from . import health_store

    directory = Path(vault_dir) / vault_layout.chapter_dir(DAILY_CHAPTER)
    if not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md"))[:MAX_DAILY_FILES]:
        day = _day(path.stem)
        if day is None:
            continue
        if first is not None and day < first:
            continue
        if last is not None and day > last:
            continue
        try:
            record = health_store.read_day(vault_dir, day.isoformat())
        except Exception:
            continue
        sleep = (record.get("sections") or {}).get("sleep") or {}
        body = (record.get("sections") or {}).get("body") or {}
        weight = _number(body.get("weight_kg"))
        sleep_minutes = _number(sleep.get("duration_min"))
        exercise = sum(float(item.get("minutes") or 0) for item in record.get("training") or [])
        if weight is None and sleep_minutes is None and exercise <= 0:
            continue
        rows.append({
            "date": day.isoformat(), "recorded": True,
            "weight_kg": round(weight, 1) if weight is not None else None,
            "sleep_minutes": int(sleep_minutes) if sleep_minutes is not None else None,
            "exercise_minutes": int(exercise) if exercise > 0 else None,
            "revision": 1, "updated_at": record.get("updated") or None,
        })
    return rows


def _legacy_rows(vault_dir: Path | str, months: list[str], first: date,
                 last: date) -> list[dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for month in months:
        try:
            data = structured_notes.read_month(vault_dir, DATASET, month)
        except Exception:
            continue
        for row in data["rows"]:
            day = _day(row.get("date"))
            if day is None or day < first or day > last:
                continue
            weight = _number(row.get("weight_kg"))
            sleep = _number(row.get("sleep_minutes"))
            exercise = _number(row.get("exercise_minutes"))
            if weight is None and sleep is None and exercise is None:
                continue
            item = aggregated.setdefault(day.isoformat(), _empty(day.isoformat(), True))
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
    daily = _daily_rows(vault_dir, first, last)
    if daily:
        return daily
    return _legacy_rows(vault_dir, _month_keys(first, last), first, last)


def day_entry(vault_dir: Path | str, day: str) -> dict[str, Any]:
    """One day in the shape consumers used for the retired body table."""
    parsed = _day(day)
    if parsed is None:
        return _empty(str(day))
    rows = entries_between(vault_dir, parsed, parsed)
    return rows[0] if rows else _empty(parsed.isoformat())


def all_entries(vault_dir: Path | str) -> list[dict[str, Any]]:
    """Every recorded body day, from daily notes first, then legacy months."""
    daily = _daily_rows(vault_dir)
    if daily:
        return daily
    roots = [Path(vault_dir) / vault_layout.chapter_dir(DATASET)]
    legacy_root = Path(vault_dir) / "02_生活运营" / "01_健康与体重"
    if legacy_root.is_dir():
        roots.append(legacy_root)
    months: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            if path.is_file() and structured_notes.MONTH_RE.fullmatch(path.stem) \
                    and path.stem not in months:
                months.append(path.stem)
    if not months:
        return []
    months.sort()
    first = date.fromisoformat(f"{months[0]}-01")
    last_year, last_month = int(months[-1][:4]), int(months[-1][5:7])
    last = date(last_year, last_month, calendar.monthrange(last_year, last_month)[1])
    return _legacy_rows(vault_dir, months, first, last)


__all__ = ["entries_between", "day_entry", "all_entries"]
