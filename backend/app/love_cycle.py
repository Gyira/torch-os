"""恋爱系统 · 经期关怀（纯本地）。

给「另一半」记录经期与每日身体状态，并在本机推算下次经期、排卵日和易孕期。
数据存在当前个人数据库的 ``.torch-os/love/cycle.db``；只在第一次写入时创建，
页面读取不会产生文件。预测只是基于历史记录的估算，不是诊断，也不能作为避孕依据；
本模块不调用任何 AI 或外部网络服务。

文件保持独立：路由、存储、预测纯函数都在这里，``love.py`` 与 ``love_schedule.py``
不感知经期数据；前端由 ``love.js`` 的二级页面「经期关怀」承载。
"""
from __future__ import annotations

import calendar as calendar_module
import json
import re
import sqlite3
import statistics
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .vault_connection import VaultReadOnlyError, ensure_vault_writable

TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_CYCLE = 28
DEFAULT_PERIOD = 5
DEFAULT_LUTEAL = 14
DEFAULT_REMINDER = 3
VALID_INTERVAL_MIN = 15
VALID_INTERVAL_MAX = 50
CYCLE_MIN = 21
CYCLE_MAX = 45
PERIOD_MIN = 2
PERIOD_MAX = 10
FERTILE_BEFORE = 5
FERTILE_AFTER = 1
OPEN_PERIOD_MAX_DAYS = 10
PROJECTION_CYCLES = 12
MAX_NOTE = 500
MAX_NAME = 40
MIN_YEAR = 2000
MAX_YEAR = 2100

FLOW_OPTIONS: tuple[tuple[str, str], ...] = (
    ("spotting", "点滴"),
    ("light", "少量"),
    ("medium", "中等"),
    ("heavy", "较多"),
)
SYMPTOM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("cramps", "痛经"),
    ("headache", "头痛"),
    ("bloating", "腹胀"),
    ("breast", "乳房胀痛"),
    ("acne", "长痘"),
    ("backache", "腰酸"),
    ("fatigue", "乏力"),
    ("insomnia", "失眠"),
    ("nausea", "恶心"),
    ("none", "无症状"),
)
MOOD_OPTIONS: tuple[tuple[str, str], ...] = (
    ("calm", "平静"),
    ("happy", "开心"),
    ("sensitive", "敏感"),
    ("low", "低落"),
    ("tired", "疲惫"),
    ("anxious", "焦虑"),
)
PAIN_LEVELS: tuple[tuple[int, str], ...] = (
    (0, "无"),
    (1, "轻微"),
    (2, "明显"),
    (3, "严重"),
)
ENERGY_LEVELS: tuple[tuple[int, str], ...] = (
    (1, "很低"),
    (2, "偏低"),
    (3, "正常"),
    (4, "充沛"),
    (5, "很好"),
)

FLOW_VALUES = tuple(value for value, _label in FLOW_OPTIONS)
SYMPTOM_VALUES = tuple(value for value, _label in SYMPTOM_OPTIONS)
MOOD_VALUES = tuple(value for value, _label in MOOD_OPTIONS)
FLOW_LABELS = dict(FLOW_OPTIONS)
SYMPTOM_LABELS = dict(SYMPTOM_OPTIONS)
MOOD_LABELS = dict(MOOD_OPTIONS)
PAIN_LABELS = dict(PAIN_LEVELS)
ENERGY_LABELS = dict(ENERGY_LEVELS)

# 护理文案全部为原创，只陈述通用生活建议与就医边界。
PHASE_META: dict[str, dict[str, Any]] = {
    "period": {
        "label": "月经期",
        "summary": "这几天身体在放慢节奏，休息和保暖比硬撑更重要。",
        "body_tips": [
            "腰腹注意保暖，累了就减少高强度安排。",
            "多喝水，适当吃瘦肉、深色蔬菜、豆类等含铁食物。",
            "疼痛影响生活时可按说明书使用止痛药；持续剧痛或出血异常请就医。",
        ],
        "partner_tips": [
            "先把热水袋、热饮和干净用品准备好，少问“要不要”，直接做。",
            "主动分担家务和外出安排，给她留出可以随时休息的空间。",
            "她不想说话时不用追问，陪着就好；疼痛严重时陪她联系医生。",
        ],
    },
    "follicular": {
        "label": "卵泡期",
        "summary": "经期结束后精力通常会回升，是安排活动和新鲜事的好窗口。",
        "body_tips": [
            "保持规律睡眠和三餐，蛋白质、蔬菜和主食都要有。",
            "体力允许时可以安排有氧或力量训练，循序渐进。",
            "继续记录经期和身体变化，周期会越算越准。",
        ],
        "partner_tips": [
            "可以一起安排出门、运动或她一直想做的事。",
            "精力好不等于没有不适，观察她的状态，不要替她逞强。",
            "把日常的小事做好，比突然的大惊喜更让她安心。",
        ],
    },
    "fertile": {
        "label": "易孕期",
        "summary": "这是按历史周期估算的易孕期，只作参考，不是避孕依据。",
        "body_tips": [
            "分泌物变多、轻微下腹坠胀可能是正常变化，注意清洁和休息。",
            "没有备孕计划时，请继续使用可靠的避孕方式，不要只依赖日期推算。",
            "如果计划备孕，可以结合排卵试纸或医生建议，不要把预测当成诊断。",
        ],
        "partner_tips": [
            "如果你们没有备孕计划，这段时间更要主动准备可靠的避孕方式。",
            "如果正在备孕，先听她的想法，再一起决定下一步。",
            "不要把预测结果当成命令或玩笑，她的身体感受更重要。",
        ],
    },
    "luteal": {
        "label": "黄体期",
        "summary": "黄体期容易出现情绪波动、乳房胀痛或食欲变化，多一些理解。",
        "body_tips": [
            "少喝咖啡、少吃高盐外卖，规律睡眠有助于缓解不适。",
            "选择散步、拉伸等温和运动，不必强迫自己高强度训练。",
            "如果症状每月都明显影响生活，可以记录下来并咨询医生。",
        ],
        "partner_tips": [
            "她情绪起伏可能和激素有关，不是针对你；先听完再回应。",
            "一起减少熬夜和外卖，准备一些温热、清淡的食物。",
            "多问“现在需要我做什么”，少讲大道理。",
        ],
    },
    "late": {
        "label": "经期推迟",
        "summary": "预计经期已经超过，先观察，不急着下结论。",
        "body_tips": [
            "压力、疾病、旅行和作息变化都可能让经期推迟。",
            "有性生活且没有可靠避孕时，可以考虑验孕；推迟超过 7 天更值得确认。",
            "反复不规律、异常出血或明显疼痛时，请咨询医生。",
        ],
        "partner_tips": [
            "先陪她确认最近的压力、作息和身体感受，不要催或开玩笑。",
            "需要时陪她买验孕棒或就医，结果没出来前不要替她下判断。",
            "无论结果如何，先让她知道你站在她这边。",
        ],
    },
    "unknown": {
        "label": "待记录",
        "summary": "先记录最近一次经期的开始和结束日期，预测才有依据。",
        "body_tips": [
            "记录经期开始日、结束日、流量和身体感受。",
            "不必追求一次记全，先留下开始日期就能开始估算。",
            "数据越多，周期和易孕期的估算越稳定。",
        ],
        "partner_tips": [
            "提醒她记录就好，不要替她猜日期。",
            "多关心她的实际感受，而不是只盯着日历。",
            "第一次记录后可以一起看看页面上的提示，但不要把预测当成结论。",
        ],
    },
}

DISCLAIMER = (
    "周期、排卵和易孕期都是根据历史记录做的估算，不是诊断，也不能作为避孕依据。"
    "压力、疾病、旅行和作息变化都会影响周期；如有异常出血、剧烈疼痛或长期不规律，请咨询医生。"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cycle_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    partner_name TEXT NOT NULL DEFAULT '',
    cycle_length INTEGER NOT NULL DEFAULT 0,
    period_length INTEGER NOT NULL DEFAULT 0,
    luteal_phase INTEGER NOT NULL DEFAULT 14,
    reminder_days INTEGER NOT NULL DEFAULT 3,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cycle_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    flow TEXT NOT NULL DEFAULT 'medium'
        CHECK (flow IN ('spotting', 'light', 'medium', 'heavy')),
    symptoms_json TEXT NOT NULL DEFAULT '[]',
    moods_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_cycle_periods_start ON cycle_periods(start_date);
CREATE TABLE IF NOT EXISTS cycle_logs (
    day TEXT PRIMARY KEY,
    mood TEXT,
    pain INTEGER,
    sleep_hours REAL,
    water_ml INTEGER,
    energy INTEGER,
    symptoms_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass(frozen=True)
class Profile:
    partner_name: str = ""
    cycle_length: int = 0
    period_length: int = 0
    luteal_phase: int = DEFAULT_LUTEAL
    reminder_days: int = DEFAULT_REMINDER
    updated_at: str | None = None


@dataclass(frozen=True)
class PeriodRecord:
    id: int
    start: date
    end: date | None
    flow: str
    symptoms: tuple[str, ...]
    moods: tuple[str, ...]
    notes: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DailyLog:
    day: date
    mood: str | None
    pain: int | None
    sleep_hours: float | None
    water_ml: int | None
    energy: int | None
    symptoms: tuple[str, ...]
    notes: str
    updated_at: str


# ── 基础工具 ─────────────────────────────────────────────────────────

def _today() -> date:
    return datetime.now(TZ).date()


def _now_text() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _normalize_day(value: str) -> str:
    text = str(value or "").strip()
    if not _DAY_RE.fullmatch(text):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        day = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("日期不是有效的日历日期") from exc
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise ValueError(f"日期需在 {MIN_YEAR}-{MAX_YEAR} 年之间")
    return day.isoformat()


def _normalize_month(value: str) -> str:
    text = str(value or "").strip()
    if not _MONTH_RE.fullmatch(text):
        raise ValueError("月份格式应为 YYYY-MM")
    year, month = (int(part) for part in text.split("-"))
    if not MIN_YEAR <= year <= MAX_YEAR or not 1 <= month <= 12:
        raise ValueError("月份不在有效范围内")
    return f"{year:04d}-{month:02d}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _loads_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _dump_list(values: list[str] | tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _mean_round(values: list[int]) -> int | None:
    if not values:
        return None
    return int(sum(values) / len(values) + 0.5)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ranges_overlap(start_a: date, end_a: date | None,
                    start_b: date, end_b: date | None) -> bool:
    stop_a = end_a or date.max
    stop_b = end_b or date.max
    return start_a <= stop_b and start_b <= stop_a


def _period_effective_end(period: PeriodRecord, today: date) -> date:
    """未结束记录最多展示到开始日 + 9 天，避免一条忘记结束的记录永久占据日历。"""
    if period.end is not None:
        return period.end
    return min(today, period.start + timedelta(days=OPEN_PERIOD_MAX_DAYS - 1))


def _period_covers(period: PeriodRecord, day: date, today: date) -> bool:
    return period.start <= day <= _period_effective_end(period, today)


def _phase_meta(phase: str) -> dict[str, Any]:
    return PHASE_META.get(phase, PHASE_META["unknown"])


# ── 预测纯函数 ───────────────────────────────────────────────────────

def analyze(periods: list[PeriodRecord], profile: Profile, today: date) -> dict[str, Any]:
    """根据经期记录推算阶段、下次经期、排卵与易孕期。"""
    ordered = sorted(periods, key=lambda item: item.start)
    base: dict[str, Any] = {
        "has_data": False,
        "cycle_day": None,
        "phase": "unknown",
        "phase_label": PHASE_META["unknown"]["label"],
        "summary": PHASE_META["unknown"]["summary"],
        "latest_start": None,
        "next_expected": None,
        "days_until": None,
        "late_days": 0,
        "in_period": False,
        "current_period_id": None,
        "ovulation_date": None,
        "fertile_start": None,
        "fertile_end": None,
        "avg_cycle": DEFAULT_CYCLE,
        "avg_period": DEFAULT_PERIOD,
        "cycle_source": "default",
        "period_source": "default",
        "luteal_phase": profile.luteal_phase,
        "reminder_days": profile.reminder_days,
        "confidence": "none",
        "confidence_label": "暂无",
        "regularity": "unknown",
        "regularity_label": "未知",
        "regularity_sd": None,
        "observed_cycles": 0,
        "cycle_lengths": [],
        "progress": 0,
    }
    if not ordered:
        return base

    intervals: list[int] = []
    cycle_lengths: list[dict[str, Any]] = []
    for previous, current in zip(ordered, ordered[1:]):
        length = (current.start - previous.start).days
        if VALID_INTERVAL_MIN <= length <= VALID_INTERVAL_MAX:
            intervals.append(length)
            cycle_lengths.append({"date": current.start.isoformat(), "length": length})

    if profile.cycle_length:
        avg_cycle = profile.cycle_length
        cycle_source = "manual"
    elif intervals:
        avg_cycle = _mean_round(intervals) or DEFAULT_CYCLE
        cycle_source = "observed"
    else:
        avg_cycle = DEFAULT_CYCLE
        cycle_source = "default"
    avg_cycle = max(CYCLE_MIN, min(CYCLE_MAX, avg_cycle))

    durations: list[int] = []
    for period in ordered:
        if period.end is None:
            continue
        duration = (period.end - period.start).days + 1
        if 1 <= duration <= 15:
            durations.append(duration)
    if profile.period_length:
        avg_period = profile.period_length
        period_source = "manual"
    elif durations:
        avg_period = _mean_round(durations) or DEFAULT_PERIOD
        period_source = "observed"
    else:
        avg_period = DEFAULT_PERIOD
        period_source = "default"
    avg_period = max(PERIOD_MIN, min(PERIOD_MAX, avg_period))

    latest_start = ordered[-1].start
    cycle_day = max(1, (today - latest_start).days + 1)
    next_expected = latest_start + timedelta(days=avg_cycle)
    days_until = (next_expected - today).days
    late_days = max(0, -days_until)
    ovulation = next_expected - timedelta(days=profile.luteal_phase)
    fertile_start = ovulation - timedelta(days=FERTILE_BEFORE)
    fertile_end = ovulation + timedelta(days=FERTILE_AFTER)

    current_period: PeriodRecord | None = None
    for period in reversed(ordered):
        if _period_covers(period, today, today):
            current_period = period
            break
    in_period = current_period is not None

    if in_period:
        phase = "period"
    elif days_until < 0:
        phase = "late"
    elif fertile_start <= today <= fertile_end:
        phase = "fertile"
    elif today > ovulation:
        phase = "luteal"
    else:
        phase = "follicular"
    meta = _phase_meta(phase)

    if phase == "late":
        progress = 100
    else:
        progress = int(max(0, min(100, round((cycle_day - 1) * 100 / avg_cycle))))

    if profile.cycle_length:
        confidence, confidence_label = "manual", "手动周期"
    elif len(intervals) >= 6:
        confidence, confidence_label = "high", "较高"
    elif len(intervals) >= 2:
        confidence, confidence_label = "medium", "中等"
    else:
        confidence, confidence_label = "low", "较低"

    if len(intervals) >= 2:
        sd = statistics.stdev(intervals)
        if sd < 2:
            regularity, regularity_label = "regular", "规律"
        elif sd < 4:
            regularity, regularity_label = "slight", "轻微波动"
        else:
            regularity, regularity_label = "irregular", "波动较大"
        regularity_sd: float | None = round(sd, 1)
    else:
        regularity, regularity_label, regularity_sd = "unknown", "未知", None

    return {
        **base,
        "has_data": True,
        "cycle_day": cycle_day,
        "phase": phase,
        "phase_label": meta["label"],
        "summary": meta["summary"],
        "latest_start": latest_start.isoformat(),
        "next_expected": next_expected.isoformat(),
        "days_until": days_until,
        "late_days": late_days,
        "in_period": in_period,
        "current_period_id": current_period.id if current_period else None,
        "ovulation_date": ovulation.isoformat(),
        "fertile_start": fertile_start.isoformat(),
        "fertile_end": fertile_end.isoformat(),
        "avg_cycle": avg_cycle,
        "avg_period": avg_period,
        "cycle_source": cycle_source,
        "period_source": period_source,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "regularity": regularity,
        "regularity_label": regularity_label,
        "regularity_sd": regularity_sd,
        "observed_cycles": len(intervals),
        "cycle_lengths": cycle_lengths[-8:],
        "progress": progress,
    }


def build_calendar(month: str, periods: list[PeriodRecord], analysis: dict[str, Any],
                   logs: list[DailyLog], today: date) -> dict[str, Any]:
    month_text = _normalize_month(month)
    year, month_number = (int(part) for part in month_text.split("-"))
    first = date(year, month_number, 1)
    last = date(year, month_number, calendar_module.monthrange(year, month_number)[1])

    actual: dict[date, int] = {}
    ongoing_days: set[date] = set()
    for period in periods:
        start = max(period.start, first)
        stop = min(_period_effective_end(period, today), last)
        cursor = start
        while cursor <= stop:
            actual[cursor] = period.id
            if period.end is None:
                ongoing_days.add(cursor)
            cursor += timedelta(days=1)

    predicted: set[date] = set()
    fertile: set[date] = set()
    ovulation: set[date] = set()
    if analysis.get("has_data") and analysis.get("latest_start"):
        latest = date.fromisoformat(analysis["latest_start"])
        avg_cycle = int(analysis["avg_cycle"])
        avg_period = int(analysis["avg_period"])
        luteal = int(analysis["luteal_phase"])
        for index in range(1, PROJECTION_CYCLES + 1):
            predicted_start = latest + timedelta(days=avg_cycle * index)
            if predicted_start > last + timedelta(days=45):
                break
            predicted_end = predicted_start + timedelta(days=avg_period - 1)
            cursor = max(predicted_start, first)
            stop = min(predicted_end, last)
            while cursor <= stop:
                predicted.add(cursor)
                cursor += timedelta(days=1)
            predicted_ovulation = predicted_start - timedelta(days=luteal)
            fertile_start = predicted_ovulation - timedelta(days=FERTILE_BEFORE)
            fertile_end = predicted_ovulation + timedelta(days=FERTILE_AFTER)
            cursor = max(fertile_start, first)
            stop = min(fertile_end, last)
            while cursor <= stop:
                fertile.add(cursor)
                cursor += timedelta(days=1)
            if first <= predicted_ovulation <= last:
                ovulation.add(predicted_ovulation)

    log_by_day = {log.day: log for log in logs}
    days: list[dict[str, Any]] = []
    cursor = first
    while cursor <= last:
        period_id = actual.get(cursor)
        is_period = period_id is not None
        is_predicted = cursor in predicted and not is_period
        is_ovulation = cursor in ovulation and not is_period and not is_predicted
        is_fertile = (
            cursor in fertile and not is_period and not is_predicted and not is_ovulation
        )
        days.append({
            "date": cursor.isoformat(),
            "is_today": cursor == today,
            "is_future": cursor > today,
            "is_period": is_period,
            "period_id": period_id,
            "is_predicted": is_predicted,
            "is_fertile": is_fertile,
            "is_ovulation": is_ovulation,
            "is_ongoing": is_period and cursor in ongoing_days,
            "has_log": cursor in log_by_day,
        })
        cursor += timedelta(days=1)

    month_logs = {
        day.isoformat(): _log_json(log)
        for day, log in sorted(log_by_day.items())
        if first <= day <= last
    }
    return {"month": month_text, "today": today.isoformat(), "days": days, "logs": month_logs}


def build_stats(periods: list[PeriodRecord], logs: list[DailyLog],
                analysis: dict[str, Any]) -> dict[str, Any]:
    completed = [period for period in periods if period.end is not None]
    symptom_counts: Counter[str] = Counter()
    mood_counts: Counter[str] = Counter()
    for period in periods:
        symptom_counts.update(value for value in period.symptoms if value != "none")
        mood_counts.update(value for value in period.moods if value in MOOD_LABELS)
    for log in logs:
        symptom_counts.update(value for value in log.symptoms if value != "none")
        if log.mood in MOOD_LABELS:
            mood_counts[log.mood] += 1

    pain_values = [float(log.pain) for log in logs if log.pain is not None]
    sleep_values = [float(log.sleep_hours) for log in logs if log.sleep_hours is not None]
    water_values = [float(log.water_ml) for log in logs if log.water_ml is not None]
    energy_values = [float(log.energy) for log in logs if log.energy is not None]

    last_start = max((period.start for period in periods), default=None)
    return {
        "total_periods": len(periods),
        "completed_periods": len(completed),
        "ongoing_periods": len(periods) - len(completed),
        "logged_days": len(logs),
        "last_period_start": last_start.isoformat() if last_start else None,
        "avg_cycle": analysis["avg_cycle"],
        "avg_period": analysis["avg_period"],
        "regularity": analysis["regularity"],
        "regularity_label": analysis["regularity_label"],
        "regularity_sd": analysis["regularity_sd"],
        "observed_cycles": analysis["observed_cycles"],
        "cycle_lengths": analysis["cycle_lengths"],
        "top_symptoms": [
            {"value": value, "label": SYMPTOM_LABELS.get(value, value), "count": count}
            for value, count in symptom_counts.most_common(5)
        ],
        "top_moods": [
            {"value": value, "label": MOOD_LABELS.get(value, value), "count": count}
            for value, count in mood_counts.most_common(5)
        ],
        "pain_average": round(_mean(pain_values), 1) if pain_values else None,
        "sleep_average": round(_mean(sleep_values), 1) if sleep_values else None,
        "water_average": round(_mean(water_values)) if water_values else None,
        "energy_average": round(_mean(energy_values), 1) if energy_values else None,
    }


def build_upcoming(analysis: dict[str, Any], today: date) -> list[dict[str, Any]]:
    if not analysis.get("has_data") or not analysis.get("latest_start"):
        return []
    latest = date.fromisoformat(analysis["latest_start"])
    avg_cycle = int(analysis["avg_cycle"])
    avg_period = int(analysis["avg_period"])
    luteal = int(analysis["luteal_phase"])
    upcoming: list[dict[str, Any]] = []
    for index in range(1, 4):
        start = latest + timedelta(days=avg_cycle * index)
        end = start + timedelta(days=avg_period - 1)
        ovulation = start - timedelta(days=luteal)
        upcoming.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "ovulation": ovulation.isoformat(),
            "fertile_start": (ovulation - timedelta(days=FERTILE_BEFORE)).isoformat(),
            "fertile_end": (ovulation + timedelta(days=FERTILE_AFTER)).isoformat(),
            "days_until": (start - today).days,
        })
    return upcoming


def build_care(analysis: dict[str, Any], periods: list[PeriodRecord],
               today: date) -> dict[str, Any]:
    phase = str(analysis.get("phase") or "unknown")
    meta = _phase_meta(phase)
    alerts: list[dict[str, str]] = []
    in_period = bool(analysis.get("in_period"))
    days_until = analysis.get("days_until")
    avg_period = int(analysis.get("avg_period") or DEFAULT_PERIOD)

    if not analysis.get("has_data"):
        alerts.append({
            "kind": "no_data",
            "level": "info",
            "title": "还没有经期记录",
            "text": "先补记最近一次经期的开始和结束日期，预测才有依据。",
        })
    else:
        overdue_open = [
            period for period in periods
            if period.end is None
            and today > period.start + timedelta(days=avg_period - 1)
        ]
        if overdue_open:
            alerts.append({
                "kind": "open_period",
                "level": "warn",
                "title": "有一条经期记录还没结束",
                "text": "如果已经结束，请补上结束日期；否则预测会被拉长。",
            })
        late_days = int(analysis.get("late_days") or 0)
        if late_days >= 7:
            alerts.append({
                "kind": "late",
                "level": "warn",
                "title": f"预计经期已推迟 {late_days} 天",
                "text": "先别紧张。若有性生活且未采取可靠避孕，可以考虑验孕；反复推迟请咨询医生。",
            })
        elif late_days > 0:
            alerts.append({
                "kind": "late",
                "level": "info",
                "title": f"预计经期已超过 {late_days} 天",
                "text": "可以再观察几天并继续记录；如果持续推迟，参考页面底部的就医提示。",
            })
        reminder_days = int(analysis.get("reminder_days") or DEFAULT_REMINDER)
        if (
            not in_period
            and isinstance(days_until, int)
            and 0 <= days_until <= reminder_days
        ):
            if days_until == 0:
                text = "今天就是预计开始日，可以提前准备卫生用品和热饮。"
            else:
                text = f"距离预计经期还有 {days_until} 天，可以提前准备。"
            alerts.append({
                "kind": "reminder",
                "level": "info",
                "title": "经期临近",
                "text": text,
            })
        if analysis.get("confidence") == "low":
            observed = int(analysis.get("observed_cycles") or 0)
            text = (
                "目前只有一次经期记录，先按默认周期估算；记录更多后会更稳定。"
                if observed == 0
                else f"目前只有 {observed} 个完整周期，记录越多，预测会越稳定。"
            )
            alerts.append({
                "kind": "low_confidence",
                "level": "info",
                "title": "预测还在起步",
                "text": text,
            })
        if analysis.get("regularity") == "irregular":
            alerts.append({
                "kind": "irregular",
                "level": "warn",
                "title": "最近周期波动较大",
                "text": "预测仅供参考。如果长期不规律，建议记录更多并咨询医生。",
            })

    return {
        "phase": phase,
        "phase_label": meta["label"],
        "summary": meta["summary"],
        "body_tips": list(meta["body_tips"]),
        "partner_tips": list(meta["partner_tips"]),
        "alerts": alerts,
        "disclaimer": DISCLAIMER,
    }


def build_overview(profile: Profile, periods: list[PeriodRecord], logs: list[DailyLog],
                   today: date, month: str, *, read_only: bool = False) -> dict[str, Any]:
    analysis = analyze(periods, profile, today)
    calendar = build_calendar(month, periods, analysis, logs, today)
    stats = build_stats(periods, logs, analysis)
    care = build_care(analysis, periods, today)
    today_log = next((log for log in logs if log.day == today), None)
    ordered = sorted(periods, key=lambda item: item.start, reverse=True)
    return {
        "month": calendar["month"],
        "today": today.isoformat(),
        "read_only": read_only,
        "profile": _profile_json(profile),
        "catalogs": _catalogs(),
        "analysis": analysis,
        "periods": [_period_json(period) for period in ordered],
        "today_log": _log_json(today_log) if today_log else None,
        "calendar": calendar,
        "stats": stats,
        "upcoming": build_upcoming(analysis, today),
        "care": care,
    }


# ── JSON 投影 ────────────────────────────────────────────────────────

def _catalogs() -> dict[str, Any]:
    return {
        "flows": [{"value": value, "label": label} for value, label in FLOW_OPTIONS],
        "symptoms": [{"value": value, "label": label} for value, label in SYMPTOM_OPTIONS],
        "moods": [{"value": value, "label": label} for value, label in MOOD_OPTIONS],
        "pain_levels": [{"value": value, "label": label} for value, label in PAIN_LEVELS],
        "energy_levels": [{"value": value, "label": label} for value, label in ENERGY_LEVELS],
    }


def _profile_json(profile: Profile) -> dict[str, Any]:
    return {
        "partner_name": profile.partner_name,
        "cycle_length": profile.cycle_length,
        "period_length": profile.period_length,
        "luteal_phase": profile.luteal_phase,
        "reminder_days": profile.reminder_days,
        "updated_at": profile.updated_at,
    }


def _period_json(period: PeriodRecord) -> dict[str, Any]:
    duration = (period.end - period.start).days + 1 if period.end else None
    return {
        "id": period.id,
        "start_date": period.start.isoformat(),
        "end_date": period.end.isoformat() if period.end else None,
        "flow": period.flow,
        "flow_label": FLOW_LABELS.get(period.flow, period.flow),
        "symptoms": list(period.symptoms),
        "symptom_labels": [SYMPTOM_LABELS.get(value, value) for value in period.symptoms],
        "moods": list(period.moods),
        "mood_labels": [MOOD_LABELS.get(value, value) for value in period.moods],
        "notes": period.notes,
        "duration_days": duration,
        "ongoing": period.end is None,
        "created_at": period.created_at,
        "updated_at": period.updated_at,
    }


def _log_json(log: DailyLog) -> dict[str, Any]:
    return {
        "day": log.day.isoformat(),
        "mood": log.mood,
        "mood_label": MOOD_LABELS.get(log.mood, "") if log.mood else "",
        "pain": log.pain,
        "pain_label": PAIN_LABELS.get(log.pain, "") if log.pain is not None else "",
        "sleep_hours": log.sleep_hours,
        "water_ml": log.water_ml,
        "energy": log.energy,
        "energy_label": ENERGY_LABELS.get(log.energy, "") if log.energy is not None else "",
        "symptoms": list(log.symptoms),
        "symptom_labels": [SYMPTOM_LABELS.get(value, value) for value in log.symptoms],
        "notes": log.notes,
        "updated_at": log.updated_at,
    }


# ── SQLite 读写 ──────────────────────────────────────────────────────

def _has_schema(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cycle_periods'"
    ).fetchone()
    return row is not None


def _open_read(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.is_file():
        return None
    uri = db_path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    return con


@contextmanager
def _open_write(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        con.executescript(SCHEMA_SQL)
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()


def _load_profile(con: sqlite3.Connection) -> Profile:
    row = con.execute("SELECT * FROM cycle_profile WHERE id = 1").fetchone()
    if row is None:
        return Profile()
    cycle_length = _safe_int(row["cycle_length"], 0)
    if cycle_length and not CYCLE_MIN <= cycle_length <= CYCLE_MAX:
        cycle_length = 0
    period_length = _safe_int(row["period_length"], 0)
    if period_length and not PERIOD_MIN <= period_length <= PERIOD_MAX:
        period_length = 0
    luteal = _safe_int(row["luteal_phase"], DEFAULT_LUTEAL)
    luteal = max(10, min(16, luteal))
    reminder = _safe_int(row["reminder_days"], DEFAULT_REMINDER)
    reminder = max(0, min(7, reminder))
    return Profile(
        partner_name=str(row["partner_name"] or "")[:MAX_NAME],
        cycle_length=cycle_length,
        period_length=period_length,
        luteal_phase=luteal,
        reminder_days=reminder,
        updated_at=str(row["updated_at"] or "") or None,
    )


def _row_to_period(row: sqlite3.Row) -> PeriodRecord | None:
    try:
        start = date.fromisoformat(str(row["start_date"])[:10])
        end = date.fromisoformat(str(row["end_date"])[:10]) if row["end_date"] else None
    except (TypeError, ValueError):
        return None
    flow = str(row["flow"] or "medium")
    if flow not in FLOW_VALUES:
        flow = "medium"
    symptoms = tuple(
        value for value in _loads_list(row["symptoms_json"]) if value in SYMPTOM_LABELS
    )
    moods = tuple(
        value for value in _loads_list(row["moods_json"]) if value in MOOD_LABELS
    )
    return PeriodRecord(
        id=int(row["id"]),
        start=start,
        end=end,
        flow=flow,
        symptoms=symptoms,
        moods=moods,
        notes=str(row["notes"] or "")[:MAX_NOTE],
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _load_periods(con: sqlite3.Connection) -> list[PeriodRecord]:
    rows = con.execute(
        "SELECT * FROM cycle_periods ORDER BY start_date ASC, id ASC"
    ).fetchall()
    periods: list[PeriodRecord] = []
    for row in rows:
        period = _row_to_period(row)
        if period is not None:
            periods.append(period)
    return periods


def _load_period(con: sqlite3.Connection, period_id: int) -> PeriodRecord | None:
    row = con.execute("SELECT * FROM cycle_periods WHERE id = ?", (period_id,)).fetchone()
    return _row_to_period(row) if row is not None else None


def _row_to_log(row: sqlite3.Row) -> DailyLog | None:
    try:
        day = date.fromisoformat(str(row["day"])[:10])
    except (TypeError, ValueError):
        return None
    mood = str(row["mood"]) if row["mood"] else None
    if mood not in MOOD_LABELS:
        mood = None
    pain = _safe_int(row["pain"], -1) if row["pain"] is not None else None
    if pain is not None and not 0 <= pain <= 3:
        pain = None
    sleep_hours = _safe_float(row["sleep_hours"]) if row["sleep_hours"] is not None else None
    if sleep_hours is not None and not 0 <= sleep_hours <= 24:
        sleep_hours = None
    water_ml = _safe_int(row["water_ml"], -1) if row["water_ml"] is not None else None
    if water_ml is not None and not 0 <= water_ml <= 5000:
        water_ml = None
    energy = _safe_int(row["energy"], -1) if row["energy"] is not None else None
    if energy is not None and not 1 <= energy <= 5:
        energy = None
    symptoms = tuple(
        value for value in _loads_list(row["symptoms_json"]) if value in SYMPTOM_LABELS
    )
    return DailyLog(
        day=day,
        mood=mood,
        pain=pain,
        sleep_hours=sleep_hours,
        water_ml=water_ml,
        energy=energy,
        symptoms=symptoms,
        notes=str(row["notes"] or "")[:MAX_NOTE],
        updated_at=str(row["updated_at"] or ""),
    )


def _load_logs(con: sqlite3.Connection) -> list[DailyLog]:
    rows = con.execute("SELECT * FROM cycle_logs ORDER BY day ASC").fetchall()
    logs: list[DailyLog] = []
    for row in rows:
        log = _row_to_log(row)
        if log is not None:
            logs.append(log)
    return logs


def _load_log(con: sqlite3.Connection, day: date) -> DailyLog | None:
    row = con.execute(
        "SELECT * FROM cycle_logs WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    return _row_to_log(row) if row is not None else None


def _upsert_profile(con: sqlite3.Connection, payload: ProfilePayload, now: str) -> None:
    con.execute(
        "INSERT INTO cycle_profile "
        "(id, partner_name, cycle_length, period_length, luteal_phase, reminder_days, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "partner_name = excluded.partner_name, cycle_length = excluded.cycle_length, "
        "period_length = excluded.period_length, luteal_phase = excluded.luteal_phase, "
        "reminder_days = excluded.reminder_days, updated_at = excluded.updated_at",
        (
            payload.partner_name,
            payload.cycle_length,
            payload.period_length,
            payload.luteal_phase,
            payload.reminder_days,
            now,
        ),
    )


def _has_overlap(con: sqlite3.Connection, start_text: str, end_text: str | None,
                 exclude_id: int | None = None) -> bool:
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text) if end_text else None
    sql = "SELECT id, start_date, end_date FROM cycle_periods"
    params: list[Any] = []
    if exclude_id is not None:
        sql += " WHERE id <> ?"
        params.append(exclude_id)
    for row in con.execute(sql, params):
        try:
            existing_start = date.fromisoformat(str(row["start_date"])[:10])
            existing_end = (
                date.fromisoformat(str(row["end_date"])[:10]) if row["end_date"] else None
            )
        except (TypeError, ValueError):
            continue
        if _ranges_overlap(start, end, existing_start, existing_end):
            return True
    return False


def _insert_period(con: sqlite3.Connection, payload: PeriodPayload, now: str) -> int:
    cursor = con.execute(
        "INSERT INTO cycle_periods "
        "(start_date, end_date, flow, symptoms_json, moods_json, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            payload.start_date,
            payload.end_date,
            payload.flow,
            _dump_list(payload.symptoms),
            _dump_list(payload.moods),
            payload.notes,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _update_period(con: sqlite3.Connection, period_id: int,
                   payload: PeriodPayload, now: str) -> bool:
    cursor = con.execute(
        "UPDATE cycle_periods SET start_date = ?, end_date = ?, flow = ?, "
        "symptoms_json = ?, moods_json = ?, notes = ?, updated_at = ? WHERE id = ?",
        (
            payload.start_date,
            payload.end_date,
            payload.flow,
            _dump_list(payload.symptoms),
            _dump_list(payload.moods),
            payload.notes,
            now,
            period_id,
        ),
    )
    return cursor.rowcount > 0


def _delete_period(con: sqlite3.Connection, period_id: int) -> bool:
    cursor = con.execute("DELETE FROM cycle_periods WHERE id = ?", (period_id,))
    return cursor.rowcount > 0


def _daily_empty(payload: DailyPayload) -> bool:
    return (
        payload.mood is None
        and payload.pain is None
        and payload.sleep_hours is None
        and payload.water_ml is None
        and payload.energy is None
        and not payload.symptoms
        and not payload.notes
    )


def _upsert_log(con: sqlite3.Connection, day: date, payload: DailyPayload, now: str) -> None:
    con.execute(
        "INSERT INTO cycle_logs "
        "(day, mood, pain, sleep_hours, water_ml, energy, symptoms_json, notes, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(day) DO UPDATE SET "
        "mood = excluded.mood, pain = excluded.pain, sleep_hours = excluded.sleep_hours, "
        "water_ml = excluded.water_ml, energy = excluded.energy, "
        "symptoms_json = excluded.symptoms_json, notes = excluded.notes, "
        "updated_at = excluded.updated_at",
        (
            day.isoformat(),
            payload.mood,
            payload.pain,
            payload.sleep_hours,
            payload.water_ml,
            payload.energy,
            _dump_list(payload.symptoms),
            payload.notes,
            now,
        ),
    )


def _delete_log(con: sqlite3.Connection, day: date) -> bool:
    cursor = con.execute("DELETE FROM cycle_logs WHERE day = ?", (day.isoformat(),))
    return cursor.rowcount > 0


# ── 请求模型 ─────────────────────────────────────────────────────────

class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_name: str = Field(default="", max_length=MAX_NAME)
    cycle_length: int = 0
    period_length: int = 0
    luteal_phase: int = DEFAULT_LUTEAL
    reminder_days: int = DEFAULT_REMINDER

    @field_validator("partner_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return value.strip()

    @field_validator("cycle_length")
    @classmethod
    def _cycle_length(cls, value: int) -> int:
        if value != 0 and not CYCLE_MIN <= value <= CYCLE_MAX:
            raise ValueError(f"周期长度应为 0（自动）或 {CYCLE_MIN}-{CYCLE_MAX} 天")
        return value

    @field_validator("period_length")
    @classmethod
    def _period_length(cls, value: int) -> int:
        if value != 0 and not PERIOD_MIN <= value <= PERIOD_MAX:
            raise ValueError(f"经期长度应为 0（自动）或 {PERIOD_MIN}-{PERIOD_MAX} 天")
        return value

    @field_validator("luteal_phase")
    @classmethod
    def _luteal(cls, value: int) -> int:
        if not 10 <= value <= 16:
            raise ValueError("黄体期应为 10-16 天")
        return value

    @field_validator("reminder_days")
    @classmethod
    def _reminder(cls, value: int) -> int:
        if not 0 <= value <= 7:
            raise ValueError("提醒天数应为 0-7 天")
        return value


class PeriodPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: str
    end_date: str | None = None
    flow: Literal["spotting", "light", "medium", "heavy"] = "medium"
    symptoms: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=MAX_NOTE)

    @field_validator("start_date")
    @classmethod
    def _start_day(cls, value: str) -> str:
        return _normalize_day(value)

    @field_validator("end_date")
    @classmethod
    def _end_day(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        return _normalize_day(value)

    @field_validator("symptoms")
    @classmethod
    def _symptoms(cls, values: list[str]) -> list[str]:
        return _validate_values(values, SYMPTOM_LABELS, "症状")

    @field_validator("moods")
    @classmethod
    def _moods(cls, values: list[str]) -> list[str]:
        return _validate_values(values, MOOD_LABELS, "情绪")

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _order(self) -> "PeriodPayload":
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class DailyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood: str | None = None
    pain: int | None = None
    sleep_hours: float | None = None
    water_ml: int | None = None
    energy: int | None = None
    symptoms: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=MAX_NOTE)

    @field_validator("mood")
    @classmethod
    def _mood(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if value not in MOOD_LABELS:
            raise ValueError("未知的情绪选项")
        return value

    @field_validator("pain")
    @classmethod
    def _pain(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 3:
            raise ValueError("疼痛程度应为 0-3")
        return value

    @field_validator("sleep_hours")
    @classmethod
    def _sleep(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not 0 <= value <= 24:
            raise ValueError("睡眠时长应为 0-24 小时")
        return round(value, 1)

    @field_validator("water_ml")
    @classmethod
    def _water(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 5000:
            raise ValueError("饮水量应为 0-5000 ml")
        return value

    @field_validator("energy")
    @classmethod
    def _energy(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 5:
            raise ValueError("精力应为 1-5")
        return value

    @field_validator("symptoms")
    @classmethod
    def _symptoms(cls, values: list[str]) -> list[str]:
        return _validate_values(values, SYMPTOM_LABELS, "症状")

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return value.strip()


def _validate_values(values: list[str], allowed: dict[str, str], label: str) -> list[str]:
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise ValueError(f"未知的{label}选项：{'、'.join(unknown)}")
    if "none" in values:
        return ["none"]
    return list(dict.fromkeys(values))


def _check_period_dates(payload: PeriodPayload, today: date) -> None:
    if date.fromisoformat(payload.start_date) > today:
        raise HTTPException(422, "经期开始日期不能晚于今天")
    if payload.end_date and date.fromisoformat(payload.end_date) > today:
        raise HTTPException(422, "经期结束日期不能晚于今天")


def _path_day(value: str, today: date) -> date:
    try:
        day = date.fromisoformat(_normalize_day(value))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if day > today:
        raise HTTPException(422, "不能记录未来日期")
    return day


# ── 路由 ─────────────────────────────────────────────────────────────

def create_cycle_router(db_path: Path | str, *, read_only: bool = False,
                        prefix: str = "/api/love/cycle") -> APIRouter:
    """经期关怀路由；路由创建本身不写磁盘，首次保存时才创建 cycle.db。"""
    path = Path(db_path)
    router = APIRouter(prefix=prefix, tags=["love-cycle"])

    def guard(action: str) -> None:
        try:
            ensure_vault_writable(read_only, action)
        except VaultReadOnlyError as exc:
            raise HTTPException(409, str(exc)) from exc

    def db_error(exc: sqlite3.Error) -> HTTPException:
        return HTTPException(503, f"经期数据库不可用：{exc}")

    def read_state() -> tuple[Profile, list[PeriodRecord], list[DailyLog]]:
        try:
            con = _open_read(path)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        if con is None:
            return Profile(), [], []
        try:
            if not _has_schema(con):
                return Profile(), [], []
            return _load_profile(con), _load_periods(con), _load_logs(con)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        finally:
            con.close()

    @router.get("/overview")
    def overview(month: str | None = Query(default=None)):
        today = _today()
        try:
            month_text = _normalize_month(month) if month else today.strftime("%Y-%m")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        profile, periods, logs = read_state()
        return build_overview(
            profile, periods, logs, today, month_text, read_only=read_only,
        )

    @router.put("/profile")
    def save_profile(payload: ProfilePayload):
        guard("保存经期设置")
        now = _now_text()
        try:
            with _open_write(path) as con:
                _upsert_profile(con, payload, now)
                profile = _load_profile(con)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        return {"status": "ok", "profile": _profile_json(profile)}

    @router.post("/periods")
    def create_period(payload: PeriodPayload):
        guard("保存经期记录")
        today = _today()
        _check_period_dates(payload, today)
        now = _now_text()
        try:
            with _open_write(path) as con:
                if _has_overlap(con, payload.start_date, payload.end_date):
                    raise HTTPException(409, "与已有经期记录重叠，请调整日期。")
                period_id = _insert_period(con, payload, now)
                period = _load_period(con, period_id)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        if period is None:
            raise HTTPException(500, "经期记录保存失败")
        return {"status": "ok", "period": _period_json(period)}

    @router.put("/periods/{period_id}")
    def update_period(period_id: int, payload: PeriodPayload):
        guard("保存经期记录")
        today = _today()
        _check_period_dates(payload, today)
        now = _now_text()
        try:
            with _open_write(path) as con:
                if _has_overlap(
                    con, payload.start_date, payload.end_date, exclude_id=period_id,
                ):
                    raise HTTPException(409, "与已有经期记录重叠，请调整日期。")
                if not _update_period(con, period_id, payload, now):
                    raise HTTPException(404, "经期记录不存在")
                period = _load_period(con, period_id)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        if period is None:
            raise HTTPException(404, "经期记录不存在")
        return {"status": "ok", "period": _period_json(period)}

    @router.delete("/periods/{period_id}")
    def delete_period(period_id: int):
        guard("删除经期记录")
        try:
            with _open_write(path) as con:
                if not _delete_period(con, period_id):
                    raise HTTPException(404, "经期记录不存在")
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        return {"status": "deleted", "id": period_id}

    @router.put("/daily/{day}")
    def save_daily(day: str, payload: DailyPayload):
        guard("保存当天状态")
        today = _today()
        value = _path_day(day, today)
        now = _now_text()
        deleted = _daily_empty(payload)
        try:
            with _open_write(path) as con:
                if deleted:
                    _delete_log(con, value)
                    log = None
                else:
                    _upsert_log(con, value, payload, now)
                    log = _load_log(con, value)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        return {
            "status": "ok",
            "deleted": deleted,
            "log": _log_json(log) if log else None,
        }

    @router.delete("/daily/{day}")
    def delete_daily(day: str):
        guard("删除当天状态")
        today = _today()
        value = _path_day(day, today)
        try:
            with _open_write(path) as con:
                deleted = _delete_log(con, value)
        except sqlite3.Error as exc:
            raise db_error(exc) from exc
        return {"status": "deleted", "day": value.isoformat(), "removed": deleted}

    return router
