"""健康系统接口（2026-10-31）：生活运营下与恋爱系统平级的二级分区。

数据全部由 ``health_store`` 读写 Vault 里的 Markdown；本文件只做 HTTP 形状、
校验与「读时计算」的组装：

- 每日记录：``GET/PATCH /api/health/day/{day}``、餐次与训练的增删；
- 身体档案：``GET/PUT /api/health/profile``；
- 体检检验：``GET/POST/DELETE /api/health/labs``；
- 趋势：``GET /api/health/trends``（指标目录里的任意数值指标 + 评分/BMI 等派生值）；
- 洞察：``GET /api/health/insights`` 与已读标记；
- 动作库：``GET /api/health/exercises``（文本复用 openGym，见 health_exercises）；
- 报告：``POST /api/health/report`` 走「私人医生」角色，落 ``00_健康报告``。

写入一律先过 ``ensure_vault_writable``；AI 不可用时报告返回 409，普通记录不受影响。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import health_catalog, health_exercises, health_score, health_store, pillar_notes
from .ai_schemas import extract_json
from .db import Database
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

REPORT_KINDS = {"week": ("周报", 7), "month": ("月报", 30)}
TREND_LIMIT = 4
TREND_DAYS = (7, 30, 90, 180, 365)
PSEUDO_METRICS = {
    "score.total": {"label": "健康指数", "unit": "分", "precision": 0},
    "sleep.score": {"label": "睡眠评分", "unit": "分", "precision": 0},
    "body.bmi": {"label": "BMI", "unit": "", "precision": 1},
    "training.volume_kg": {"label": "训练容量", "unit": "kg", "precision": 0},
    "training.minutes": {"label": "训练时长", "unit": "分钟", "precision": 0},
}
TREND_PRESETS = (
    {"key": "sleep", "title": "睡眠质量", "metrics": ["sleep.duration_min", "sleep.score"]},
    {"key": "weight", "title": "体重管理", "metrics": ["body.weight_kg", "body.body_fat_pct"]},
    {"key": "training", "title": "训练表现", "metrics": ["training.volume_kg", "training.minutes"]},
    {"key": "mind", "title": "心理状态", "metrics": ["mind.mood", "mind.stress", "activity.steps"]},
    {"key": "vitals", "title": "生命体征", "metrics": ["vitals.resting_hr", "vitals.systolic"]},
)


class DayPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = ""
    changes: dict[str, Any] = Field(default_factory=dict)


class MealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    meal_type: str = Field(default="加餐", max_length=8)
    name: str = Field(min_length=1, max_length=60)
    amount_g: float | None = Field(default=None, ge=0, le=10000)
    calories: float | None = Field(default=None, ge=0, le=10000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    fiber_g: float | None = Field(default=None, ge=0, le=500)
    sodium_mg: float | None = Field(default=None, ge=0, le=20000)


class TrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="力量", max_length=8)
    minutes: int | None = Field(default=None, ge=1, le=600)
    rpe: int | None = Field(default=None, ge=1, le=10)
    soreness: int | None = Field(default=None, ge=1, le=5)
    mood_after: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=600)
    exercises: list[dict[str, Any]] = Field(default_factory=list)


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


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: str = Field(default="week", min_length=3, max_length=8)
    confirm: bool = Field(strict=True)


def _guard(read_only: bool):
    def check(action: str) -> None:
        try:
            ensure_vault_writable(read_only, action)
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
    return check


def _history(vault_dir: Path | str, day: str, span: int = 8) -> list[dict[str, Any]]:
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
        ("sleep", "duration_min", "睡眠", "今天还没记录睡眠"),
        ("body", "weight_kg", "体重", "今天还没称体重"),
        ("nutrition", "water_ml", "饮水", "今天还没记录饮水"),
        ("activity", "steps", "步数", "今天还没记录步数"),
        ("mind", "mood", "情绪", "今天还没打分情绪"),
    )
    items = []
    for section, field, label, hint in checks:
        if (sections.get(section) or {}).get(field) is None:
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


def _reports(vault_dir: Path | str, limit: int = 8) -> list[dict[str, Any]]:
    try:
        notes = pillar_notes.list_notes(vault_dir, chapter="health_report", limit=limit)
    except Exception:
        return []
    return [{key: note.get(key) for key in ("title", "path", "date", "summary")}
            for note in notes[:limit]]


def _report_body(database: Database, vault_dir: Path | str, role_service: Any,
                 kind: str, *, read_only: bool) -> dict[str, Any]:
    if kind not in REPORT_KINDS:
        raise HTTPException(422, "报告类型只能是 week 或 month")
    if role_service is None:
        raise HTTPException(409, "AI 不可用：没有配置 dsh 或 DeepSeek Key")
    try:
        ensure_vault_writable(read_only, "生成健康报告")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    label, span = REPORT_KINDS[kind]
    context, stats = health_store.summary(vault_dir, days=span)
    user_text = (
        f"请按私人医生模式，基于最近 {span} 天的健康系统记录，写一份{label}。"
        "先陈述事实（记录天数、指标、趋势），再给解读与最多三条可执行建议；"
        "数据不足的地方明确说不足，不诊断疾病、不开药。"
        "输出约定 JSON（title/summary/sections/recommendations/note）。"
    )
    try:
        result = role_service.run("health_doctor", "report",
                                  context_text=context, user_text=user_text)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:  # 角色层可能抛 503/超时
        raise HTTPException(getattr(error, "status_code", 503), str(error)) from error
    structured = extract_json(result.text) or {}
    note_data = structured.get("note") if isinstance(structured, dict) else None
    note_data = note_data if isinstance(note_data, dict) else {}
    title = str(note_data.get("title") or f"健康{label}-{health_store.today()}")[:120]
    body = str(note_data.get("markdown") or "").strip() or result.text
    note = pillar_notes.create_note(
        vault_dir, pillar="ops", chapter="health_report", title=title, body=body,
        note_type="report", tags=["AI", "health", kind], source="AI",
        extra_metadata={"memory": False, "generated_by": "health_doctor",
                        "health_kind": kind},
    )
    database.audit("health_report", {"kind": kind, "path": note.get("path", ""),
                                     "chars": len(body)})
    return {
        "kind": kind, "label": label, "text": result.text,
        "structured": structured if isinstance(structured, dict) else {},
        "note": note, "stats": stats, "seconds": getattr(result, "seconds", None),
        "reports": _reports(vault_dir),
    }


def create_health_router(database: Database, vault_dir: Path | str, *,
                         role_service: Any = None, read_only: bool = False,
                         prefix: str = "/api") -> APIRouter:
    router = APIRouter(prefix=f"{prefix}/health", tags=["health"])
    guard = _guard(read_only)

    @router.get("/catalog")
    def catalog():
        return {"catalog": health_catalog.catalog(),
                "capabilities": {"write": not read_only, "ai": role_service is not None}}

    @router.get("/overview")
    def overview(day: str = Query(default="")):
        target_day = health_store.validate_day(day or health_store.today())
        profile = health_store.read_profile(vault_dir)
        days = health_store.list_days(vault_dir, start=health_store.shift_day(target_day, -6),
                                      end=target_day, limit=7)
        by_date = {item["date"]: item for item in days}
        current = by_date.get(target_day) or health_store.empty_day(target_day)
        history = _history(vault_dir, target_day)
        payload = _payload(vault_dir, profile, current, history)
        week = []
        for offset in range(7):
            key = health_store.shift_day(target_day, offset - 6)
            record = by_date.get(key) or health_store.empty_day(key)
            score = health_score.score_day(record, profile, days)
            sleep = (record.get("sections") or {}).get("sleep") or {}
            body = (record.get("sections") or {}).get("body") or {}
            activity = (record.get("sections") or {}).get("activity") or {}
            mind = (record.get("sections") or {}).get("mind") or {}
            week.append({
                "date": key, "has_data": record.get("exists", False),
                "score": score.get("total"),
                "sleep_minutes": sleep.get("duration_min"),
                "weight_kg": body.get("weight_kg"),
                "steps": activity.get("steps"),
                "mood": mind.get("mood"),
            })
        labs = health_store.read_labs(vault_dir)
        abnormal = [row for row in labs.get("rows") or [] if row.get("abnormal")]
        latest_lab = max((row["date"] for row in labs.get("rows") or []), default="")
        return {
            "day": target_day,
            "profile": profile,
            "catalog": health_catalog.catalog(),
            "today": payload,
            "week": week,
            "targets": health_score.targets(profile, current),
            "missing": _missing(current),
            "labs": {"count": len(labs.get("rows") or []),
                     "latest_date": latest_lab,
                     "abnormal": abnormal[-6:]},
            "reports": _reports(vault_dir),
            "capabilities": {"write": not read_only, "ai": role_service is not None},
        }

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
        return {"reports": _reports(vault_dir, limit=20)}

    @router.post("/report")
    def report(payload: ReportRequest):
        if payload.confirm is not True:
            raise HTTPException(422, "请明确确认生成并保存报告")
        return _report_body(database, vault_dir, role_service, payload.kind,
                            read_only=read_only)

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
