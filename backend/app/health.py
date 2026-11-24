"""健康系统接口（2026-10-31）：生活运营下与恋爱系统平级的二级分区。

数据全部由 ``health_store`` 读写 Vault 里的 Markdown；本文件只做 HTTP 形状、
校验与「读时计算」的组装：

- 每日记录：``GET/PATCH /api/health/day/{day}``、餐次与训练的增删；
- 身体档案：``GET/PUT /api/health/profile``；
- 体检检验：``GET/POST/DELETE /api/health/labs``；
- 趋势：``GET /api/health/trends``（指标目录里的任意数值指标 + 评分/BMI 等派生值）；
- 洞察：``GET /api/health/insights`` 与已读标记；
- 动作库：``GET /api/health/exercises``（文本复用 openGym，见 health_exercises）；
- 健康档案可查看以往的健康报告与问诊记录。
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import (health_catalog, health_exercises, health_score, health_store,
               pillar_notes, vault_layout)
from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

TREND_LIMIT = 4
TREND_DAYS = (7, 30, 90, 180, 365)
PSEUDO_METRICS = {
    "score.total": {"label": "健康指数", "unit": "分", "precision": 0},
    "sleep.score": {"label": "睡眠评分", "unit": "分", "precision": 0},
    "body.bmi": {"label": "BMI", "unit": "", "precision": 1},
    "training.volume_kg": {"label": "训练容量", "unit": "kg", "precision": 0},
    "training.minutes": {"label": "训练时长", "unit": "分钟", "precision": 0},
    "training.calories": {"label": "运动消耗", "unit": "kcal", "precision": 0},
}
TREND_PRESETS = (
    {"key": "sleep", "title": "睡眠质量", "metrics": ["sleep.duration_min", "sleep.score"]},
    {"key": "weight", "title": "体重管理", "metrics": ["body.weight_kg", "body.body_fat_pct"]},
    {"key": "training", "title": "运动表现", "metrics": ["training.calories", "training.minutes"]},
    {"key": "diet", "title": "饮食热量", "metrics": ["nutrition.calories", "nutrition.protein_g"]},
)


class DayPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = ""
    changes: dict[str, Any] = Field(default_factory=dict)


class MealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    meal_type: str = Field(default="加餐", max_length=8)
    name: str = Field(min_length=1, max_length=60)
    portion: str = Field(default="", max_length=30)
    amount_g: float | None = Field(default=None, ge=0, le=10000)
    calories: float | None = Field(default=None, ge=0, le=10000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    fiber_g: float | None = Field(default=None, ge=0, le=500)
    sodium_mg: float | None = Field(default=None, ge=0, le=20000)
    source: str = Field(default="", max_length=10)
    confidence: float | None = Field(default=None, ge=0, le=1)


class TrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="其他", max_length=8)
    minutes: int | None = Field(default=None, ge=1, le=600)
    calories: int | None = Field(default=None, ge=0, le=5000)
    distance_km: float | None = Field(default=None, ge=0, le=200)
    steps: int | None = Field(default=None, ge=0, le=100000)
    rpe: int | None = Field(default=None, ge=1, le=10)
    soreness: int | None = Field(default=None, ge=1, le=5)
    mood_after: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=600)
    source: str = Field(default="", max_length=10)
    intensity: str = Field(default="", max_length=10)
    confidence: float | None = Field(default=None, ge=0, le=1)
    exercises: list[dict[str, Any]] = Field(default_factory=list)


class DietSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: str = ""
    food_text: str = Field(default="", max_length=2000)
    water_ml: float | None = Field(default=None, ge=0, le=8000)
    meals: list[MealRequest] = Field(default_factory=list, max_length=60)


class ExerciseSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: str = ""
    exercise_text: str = Field(default="", max_length=2000)
    steps: int | None = Field(default=None, ge=0, le=100000)
    sessions: list[TrainingRequest] = Field(default_factory=list, max_length=40)


class LabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    date: str = Field(min_length=10, max_length=10)
    category: str = Field(default="其他", max_length=10)
    test: str = Field(min_length=1, max_length=60)
    value: float = Field(ge=-1_000_000, le=1_000_000)
    unit: str = Field(default="", max_length=12)
    ref_min: float | None = None
    ref_max: float | None = None
    abnormal: bool | None = None
    note: str = Field(default="", max_length=200)
    revision: str = ""


class InsightRead(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=3, max_length=80)
    read: bool = True


def _guard(read_only: bool):
    def check(action: str) -> None:
        try:
            ensure_vault_writable(read_only, action)
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
    return check


def _history(vault_dir: Path | str, day: str, span: int = 90) -> list[dict[str, Any]]:
    start = health_store.shift_day(day, -(max(2, span) - 1))
    return health_store.list_days(vault_dir, start=start, end=day, limit=max(2, span))


def _payload(vault_dir: Path | str, profile: dict[str, Any],
             data: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    score = health_score.score_day(data, profile, history)
    return {
        **data,
        "score": score,
        "nutrition": health_store.nutrition_totals(data),
        "training_totals": health_store.training_totals(data),
        "abnormal": score.get("derived", {}).get("abnormal", []),
    }


def _missing(data: dict[str, Any]) -> list[dict[str, str]]:
    sections = data.get("sections") or {}
    checks = (
        ("sleep", "duration_min", "睡眠", "今天还没记录睡了多久"),
        ("body", "weight_kg", "体重", "今天还没称体重"),
        ("nutrition", "calories", "饮食", "今天还没记录吃了什么"),
        ("activity", "steps", "运动", "今天还没记录运动"),
    )
    items = []
    for section, field, label, hint in checks:
        values = sections.get(section) or {}
        has = values.get(field) is not None
        if section == "nutrition" and data.get("meals"):
            has = True
        if section == "activity" and data.get("training"):
            has = True
        if not has:
            items.append({"key": f"{section}.{field}", "label": label, "hint": hint})
    return items


def _trend_point(day: dict[str, Any], key: str, profile: dict[str, Any],
                 history: list[dict[str, Any]]) -> float | None:
    if key == "score.total":
        return health_score.score_day(day, profile, history).get("total")
    if key == "sleep.score":
        detail = health_score.sleep_score((day.get("sections") or {}).get("sleep") or {})
        return detail["score"] if detail else None
    if key == "body.bmi":
        body = (day.get("sections") or {}).get("body") or {}
        return health_score.bmi(body.get("weight_kg"), profile.get("height_cm"))
    if key.startswith("custom."):
        label = key.split(".", 1)[1]
        value = (day.get("custom") or {}).get(label)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    section, _, field = key.partition(".")
    if section == "training":
        totals = health_store.training_totals(day)
        return totals.get(field)
    if section == "nutrition":
        totals = health_store.nutrition_totals(day)
        value = totals.get(field)
        if value is not None:
            return float(value)
        value = (day.get("sections") or {}).get("nutrition", {}).get(field)
        return float(value) if value is not None else None
    value = (day.get("sections") or {}).get(section, {}).get(field)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _trend_meta(key: str, profile: dict[str, Any]) -> dict[str, Any]:
    if key in PSEUDO_METRICS:
        return {"key": key, **PSEUDO_METRICS[key]}
    if key.startswith("custom."):
        label = key.split(".", 1)[1]
        custom = next((item for item in profile.get("custom_metrics") or []
                       if item.get("label") == label), {})
        return {"key": key, "label": label, "unit": custom.get("unit") or "",
                "precision": 1, "custom": True}
    spec = health_catalog.metric(key)
    if spec is None:
        raise HTTPException(422, f"未知趋势指标：{key}")
    return {"key": key, "label": spec["label"], "unit": spec["unit"],
            "precision": spec["precision"], "normal": spec["normal"],
            "target": spec["target"], "higher_better": spec["higher_better"]}


def _stats(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [point["value"] for point in points]
    if not values:
        return {"count": 0, "latest": None, "first": None, "avg": None,
                "min": None, "max": None, "delta": None, "slope": None}
    count = len(values)
    average = sum(values) / count
    slope = None
    if count >= 2:
        xs = list(range(count))
        mean_x = sum(xs) / count
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator:
            slope = sum((x - mean_x) * (value - average) for x, value in zip(xs, values)) / denominator
    return {
        "count": count, "latest": round(values[-1], 2), "first": round(values[0], 2),
        "avg": round(average, 2), "min": round(min(values), 2), "max": round(max(values), 2),
        "delta": round(values[-1] - values[0], 2),
        "slope": round(slope, 3) if slope is not None else None,
    }


def _reports(vault_dir: Path | str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        notes = pillar_notes.list_notes(vault_dir, chapter="health_report", limit=limit)
    except Exception:
        return []
    items = []
    for note in notes[:limit]:
        title = note.get("title") or "健康报告"
        date_value = str(note.get("date") or "")
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(title))
        if match:
            date_value = match.group(1)
        items.append({
            "title": title,
            "path": note.get("path") or "",
            "date": date_value[:10],
            "excerpt": note.get("excerpt") or "",
            "updated": str(note.get("updated") or "")[:19],
        })
    return items


def _consults(vault_dir: Path | str, limit: int = 20) -> list[dict[str, Any]]:
    try:
        notes = pillar_notes.list_notes(vault_dir, chapter="health_consult", limit=limit)
    except Exception:
        return []
    items = []
    for note in notes[:limit]:
        title = note.get("title") or "问诊记录"
        date_value = str(note.get("date") or "")
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(title))
        if match:
            date_value = match.group(1)
        items.append({
            "title": title,
            "path": note.get("path") or "",
            "date": date_value[:10],
            "excerpt": note.get("excerpt") or "",
            "updated": str(note.get("updated") or "")[:19],
        })
    return items


def create_health_router(database: Database, vault_dir: Path | str, *,
                         read_only: bool = False,
                         prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=f"{prefix}/health", tags=["health"])
    guard = _guard(read_only)

    @router.get("/catalog")
    def catalog():
        return {"catalog": health_catalog.catalog(),
                "capabilities": {"write": not read_only}}

    @router.get("/overview")
    def overview(day: str = Query(default="")):
        target_day = health_store.validate_day(day or health_store.today())
        profile = health_store.read_profile(vault_dir)
        history = _history(vault_dir, target_day, span=90)
        by_date = {item["date"]: item for item in history}
        current = by_date.get(target_day) or health_store.empty_day(target_day)
        payload = _payload(vault_dir, profile, current, history)
        latest = health_store.latest_payload(vault_dir, target_day, lookback=90)
        current_score = health_score.current_score(profile, history, target_day, latest)
        week_start = health_store.shift_day(
            target_day, -date.fromisoformat(target_day).weekday())
        week = []
        for offset in range(7):
            key = health_store.shift_day(week_start, offset)
            record = by_date.get(key) or health_store.empty_day(key)
            sections = record.get("sections") or {}
            score = health_score.score_day(record, profile, history)
            sleep = sections.get("sleep") or {}
            body = sections.get("body") or {}
            activity = sections.get("activity") or {}
            totals = health_store.training_totals(record)
            week.append({
                "date": key,
                "has_data": bool(record.get("exists")),
                "recorded": bool(record.get("exists") and (
                    any(sections.get(section) for section in health_store.SECTIONS)
                    or record.get("meals") or record.get("training"))),
                "score": score.get("total"),
                "sleep_minutes": sleep.get("duration_min"),
                "weight_kg": body.get("weight_kg"),
                "steps": activity.get("steps"),
                "exercise_minutes": totals["minutes"],
                "calories": health_store.nutrition_totals(record).get("calories"),
                "is_today": key == health_store.today(),
            })
        labs = health_store.read_labs(vault_dir)
        abnormal = [row for row in labs.get("rows") or [] if row.get("abnormal")]
        latest_lab = max((row["date"] for row in labs.get("rows") or []), default="")
        effective_body = (latest.get("body") or {}).get("values") or {}
        return {
            "day": target_day,
            "today_date": health_store.today(),
            "week_start": week_start,
            "week_end": health_store.shift_day(week_start, 6),
            "profile": profile,
            "catalog": health_catalog.catalog(),
            "today": payload,
            "effective": latest,
            "current": current_score,
            "week": week,
            "targets": health_score.targets(profile, {"sections": {"body": effective_body}}),
            "missing": _missing(current),
            "labs": {"count": len(labs.get("rows") or []),
                     "latest_date": latest_lab,
                     "abnormal": abnormal[-6:]},
            "reports": _reports(vault_dir, limit=8),
            "archive": {
                "days": len(history),
                "first": history[0]["date"] if history else "",
                "last": history[-1]["date"] if history else "",
            },
            "capabilities": {"write": not read_only},
        }

    @router.get("/week")
    def week(day: str = Query(default="")):
        target_day = health_store.validate_day(day or health_store.today())
        week_start = health_store.shift_day(
            target_day, -date.fromisoformat(target_day).weekday())
        records = health_store.list_days(
            vault_dir, start=week_start, end=health_store.shift_day(week_start, 6), limit=7)
        by_date = {item["date"]: item for item in records}
        rows = []
        for offset in range(7):
            key = health_store.shift_day(week_start, offset)
            record = by_date.get(key) or health_store.empty_day(key)
            sections = record.get("sections") or {}
            totals = health_store.training_totals(record)
            rows.append({
                "date": key, "has_data": bool(record.get("exists")),
                "recorded": bool(record.get("exists") and (
                    any(sections.get(section) for section in health_store.SECTIONS)
                    or record.get("meals") or record.get("training"))),
                "sleep_minutes": (sections.get("sleep") or {}).get("duration_min"),
                "weight_kg": (sections.get("body") or {}).get("weight_kg"),
                "steps": (sections.get("activity") or {}).get("steps"),
                "exercise_minutes": totals["minutes"],
                "calories": health_store.nutrition_totals(record).get("calories"),
                "is_today": key == health_store.today(),
            })
        return {"week_start": week_start,
                "week_end": health_store.shift_day(week_start, 6),
                "days": rows}

    @router.get("/archive")
    def archive(day: str = Query(default="")):
        target_day = health_store.validate_day(day or health_store.today())
        profile = health_store.read_profile(vault_dir)
        history = _history(vault_dir, target_day, span=365)
        latest = health_store.latest_payload(vault_dir, target_day, lookback=365)
        current_score = health_score.current_score(profile, history, target_day, latest)
        labs = health_store.read_labs(vault_dir)
        reports = _reports(vault_dir, limit=50)
        consults = _consults(vault_dir, limit=20)
        return {
            "day": target_day,
            "profile": profile,
            "latest": latest,
            "current": current_score,
            "targets": health_score.targets(profile),
            "labs": {**labs,
                     "abnormal_count": sum(1 for row in labs.get("rows") or []
                                           if row.get("abnormal")),
                     "categories": list(health_catalog.LAB_CATEGORIES),
                     "tests": [dict(item) for item in health_catalog.LAB_TESTS]},
            "reports": reports,
            "consults": consults,
            "archive": {
                "days": len(history),
                "first": history[0]["date"] if history else "",
                "last": history[-1]["date"] if history else "",
            },
            "capabilities": {"write": not read_only},
        }

    @router.get("/foods")
    def foods(q: str = Query(default="", max_length=60),
              limit: int = Query(default=80, ge=1, le=200)):
        return {"items": health_catalog.food_items(q, limit)}

    @router.get("/day/{day}")
    def read_day(day: str):
        target_day = health_store.validate_day(day)
        profile = health_store.read_profile(vault_dir)
        history = _history(vault_dir, target_day)
        data = health_store.read_day(vault_dir, target_day)
        return {"day": target_day, "targets": health_score.targets(profile, data),
                "record": _payload(vault_dir, profile, data, history)}

    @router.patch("/day/{day}")
    def patch_day(day: str, payload: DayPatch):
        guard("修改健康记录")
        target_day = health_store.validate_day(day)
        health_store.patch_day(vault_dir, target_day, payload.changes,
                               expected_revision=payload.revision)
        profile = health_store.read_profile(vault_dir)
        data = health_store.read_day(vault_dir, target_day)
        history = _history(vault_dir, target_day)
        return {"day": target_day, "targets": health_score.targets(profile, data),
                "record": _payload(vault_dir, profile, data, history)}

    @router.post("/day/{day}/meals")
    def add_meal(day: str, payload: MealRequest):
        guard("记录餐次")
        target_day = health_store.validate_day(day)
        health_store.add_meal(vault_dir, target_day, payload.model_dump())
        profile = health_store.read_profile(vault_dir)
        data = health_store.read_day(vault_dir, target_day)
        return {"day": target_day, "record": _payload(vault_dir, profile, data,
                                                      _history(vault_dir, target_day))}

    @router.delete("/day/{day}/meals/{meal_id}")
    def delete_meal(day: str, meal_id: str):
        guard("删除餐次")
        target_day = health_store.validate_day(day)
        health_store.delete_meal(vault_dir, target_day, meal_id)
        profile = health_store.read_profile(vault_dir)
        data = health_store.read_day(vault_dir, target_day)
        return {"day": target_day, "record": _payload(vault_dir, profile, data,
                                                      _history(vault_dir, target_day))}

    @router.post("/day/{day}/training")
    def add_training(day: str, payload: TrainingRequest):
        guard("记录训练")
        target_day = health_store.validate_day(day)
        health_store.add_training(vault_dir, target_day, payload.model_dump())
        profile = health_store.read_profile(vault_dir)
        data = health_store.read_day(vault_dir, target_day)
        return {"day": target_day, "record": _payload(vault_dir, profile, data,
                                                      _history(vault_dir, target_day))}

    @router.delete("/day/{day}/training/{session_id}")
    def delete_training(day: str, session_id: str):
        guard("删除训练")
        target_day = health_store.validate_day(day)
        health_store.delete_training(vault_dir, target_day, session_id)
        profile = health_store.read_profile(vault_dir)
        data = health_store.read_day(vault_dir, target_day)
        return {"day": target_day, "record": _payload(vault_dir, profile, data,
                                                      _history(vault_dir, target_day))}

    @router.put("/day/{day}/diet")
    def save_diet(day: str, payload: DietSaveRequest):
        guard("保存饮食记录")
        target_day = health_store.validate_day(day)
        data = health_store.replace_diet(
            vault_dir, target_day, food_text=payload.food_text,
            water_ml=payload.water_ml,
            meals=[item.model_dump() for item in payload.meals],
            expected_revision=payload.revision)
        profile = health_store.read_profile(vault_dir)
        return {"day": target_day, "record": _payload(
            vault_dir, profile, data, _history(vault_dir, target_day))}

    @router.put("/day/{day}/exercise")
    def save_exercise(day: str, payload: ExerciseSaveRequest):
        guard("保存运动记录")
        target_day = health_store.validate_day(day)
        data = health_store.replace_exercise(
            vault_dir, target_day, exercise_text=payload.exercise_text,
            steps=payload.steps,
            sessions=[item.model_dump() for item in payload.sessions],
            expected_revision=payload.revision)
        profile = health_store.read_profile(vault_dir)
        return {"day": target_day, "record": _payload(
            vault_dir, profile, data, _history(vault_dir, target_day))}

    @router.get("/profile")
    def read_profile():
        profile = health_store.read_profile(vault_dir)
        return {"profile": profile, "targets": health_score.targets(profile),
                "labels": health_store.GOAL_LABELS}

    @router.put("/profile")
    def write_profile(payload: dict[str, Any]):
        guard("修改身体档案")
        revision = str(payload.pop("revision", "") or "")
        profile = health_store.write_profile(vault_dir, payload, expected_revision=revision)
        return {"profile": profile, "targets": health_score.targets(profile),
                "labels": health_store.GOAL_LABELS}

    @router.get("/labs")
    def read_labs():
        labs = health_store.read_labs(vault_dir)
        rows = labs.get("rows") or []
        return {**labs, "abnormal_count": sum(1 for row in rows if row.get("abnormal")),
                "categories": list(health_catalog.LAB_CATEGORIES),
                "tests": [dict(item) for item in health_catalog.LAB_TESTS]}

    @router.post("/labs")
    def add_lab(payload: LabRequest):
        guard("记录检验结果")
        labs = health_store.read_labs(vault_dir)
        row = payload.model_dump()
        revision = str(row.pop("revision", "") or "")
        rows = [*labs.get("rows", []), row]
        saved = health_store.save_labs(vault_dir, rows, expected_revision=revision)
        return {**saved, "abnormal_count": sum(1 for item in saved["rows"] if item.get("abnormal"))}

    @router.delete("/labs/{row_id}")
    def delete_lab(row_id: str, revision: str = Query(default="")):
        guard("删除检验结果")
        labs = health_store.read_labs(vault_dir)
        rows = [row for row in labs.get("rows", []) if row.get("id") != row_id]
        if len(rows) == len(labs.get("rows", [])):
            raise HTTPException(404, "这条检验记录不存在")
        saved = health_store.save_labs(vault_dir, rows, expected_revision=revision)
        return {**saved, "abnormal_count": sum(1 for item in saved["rows"] if item.get("abnormal"))}

    @router.get("/history")
    def history(days: int = Query(default=30, ge=1, le=366),
                end: str = Query(default="")):
        """一段时间的完整每日记录（训练历史、餐次历史用；只读）。"""
        end_day = health_store.validate_day(end or health_store.today())
        start = health_store.shift_day(end_day, -(days - 1))
        profile = health_store.read_profile(vault_dir)
        records = health_store.list_days(vault_dir, start=start, end=end_day, limit=days)
        return {"range": {"start": start, "end": end_day},
                "records": [_payload(vault_dir, profile, record, records)
                            for record in records]}

    @router.get("/trends")
    def trends(metrics: str = Query(default=""), days: int = Query(default=30, ge=7, le=366),
               end: str = Query(default="")):
        end_day = health_store.validate_day(end or health_store.today())
        keys = [item.strip() for item in str(metrics or "").split(",") if item.strip()][:TREND_LIMIT]
        if not keys:
            keys = list(TREND_PRESETS[0]["metrics"])
        profile = health_store.read_profile(vault_dir)
        start = health_store.shift_day(end_day, -(days - 1))
        records = health_store.list_days(vault_dir, start=start, end=end_day, limit=days)
        result = []
        for key in keys:
            meta = _trend_meta(key, profile)
            points = []
            for record in records:
                value = _trend_point(record, key, profile, records)
                if value is not None:
                    points.append({"date": record["date"], "value": round(float(value), 2)})
            result.append({**meta, "points": points, "stats": _stats(points)})
        return {"range": {"start": start, "end": end_day}, "days": days,
                "metrics": result, "presets": [dict(item) for item in TREND_PRESETS],
                "available": _available_metrics(profile)}

    @router.get("/insights")
    def insights(days: int = Query(default=90, ge=7, le=366),
                 end: str = Query(default="")):
        end_day = health_store.validate_day(end or health_store.today())
        profile = health_store.read_profile(vault_dir)
        start = health_store.shift_day(end_day, -(days - 1))
        records = health_store.list_days(vault_dir, start=start, end=end_day, limit=days)
        state = health_store.read_insight_state(database)
        items = health_score.insights(records, profile, state.get("read") or [])
        return {"range": {"start": start, "end": end_day}, "days": len(records),
                "insights": items,
                "unread": sum(1 for item in items if not item.get("read"))}

    @router.post("/insights/read")
    def mark_insight(payload: InsightRead):
        guard("标记洞察已读")
        state = health_store.read_insight_state(database)
        read = set(state.get("read") or [])
        if payload.read:
            read.add(payload.id)
        else:
            read.discard(payload.id)
        saved = health_store.write_insight_state(database, sorted(read))
        return {"read": saved.get("read") or []}

    @router.get("/exercises")
    def exercises(q: str = Query(default="", max_length=60),
                  body: str = Query(default="", max_length=30),
                  equipment: str = Query(default="", max_length=30),
                  limit: int = Query(default=40, ge=1, le=60)):
        return {"items": health_exercises.search(q, body, equipment, limit),
                "facets": health_exercises.facets()}

    @router.get("/reports")
    def reports():
        return {"reports": _reports(vault_dir, limit=50)}

    @router.delete("/reports")
    def delete_report(path: str = Query(min_length=1, max_length=2048),
                      revision: str = Query(default="", max_length=64)):
        guard("删除健康报告")
        try:
            chapter = vault_layout.chapter_key_of(path)
        except Exception as error:
            raise HTTPException(422, "这条报告路径不可用") from error
        if chapter != "health_report":
            raise HTTPException(422, "只能删除「健康报告」本子里的文档")
        result = pillar_notes.trash_note(vault_dir, path, expected_revision=revision)
        database.audit("health_report_deleted", {"path": path,
                                                 "trash": result.get("trash_path", "")})
        return {**result, "reports": _reports(vault_dir, limit=50)}

    @router.get("/consults")
    def consults():
        return {"consults": _consults(vault_dir, limit=50)}

    @router.delete("/consults")
    def delete_consult(path: str = Query(min_length=1, max_length=2048),
                       revision: str = Query(default="", max_length=64)):
        guard("删除问诊记录")
        try:
            chapter = vault_layout.chapter_key_of(path)
        except Exception as error:
            raise HTTPException(422, "这条问诊记录路径不可用") from error
        if chapter != "health_consult":
            raise HTTPException(422, "只能删除「问诊记录」本子里的文档")
        result = pillar_notes.trash_note(vault_dir, path, expected_revision=revision)
        database.audit("health_consult_deleted", {"path": path,
                                                   "trash": result.get("trash_path", "")})
        return {**result, "consults": _consults(vault_dir, limit=50)}

    return router


def _available_metrics(profile: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in health_catalog.GROUPS:
        if group["key"] == "custom":
            continue
        for field in health_catalog.section_fields(group["key"]):
            if field["kind"] in {"text", "time"}:
                continue
            items.append({"key": field["key"], "label": field["label"],
                          "unit": field["unit"], "group": group["title"]})
    for key, meta in PSEUDO_METRICS.items():
        items.append({"key": key, "label": meta["label"], "unit": meta["unit"],
                      "group": "评分与派生"})
    for custom in health_store.custom_metric_keys(profile):
        label = custom.get("label")
        items.append({"key": f"custom.{label}", "label": label,
                      "unit": custom.get("unit") or "", "group": "自定义"})
    return items


__all__ = ["create_health_router", "TREND_PRESETS", "PSEUDO_METRICS"]
