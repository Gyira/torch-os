"""健康系统的 Markdown 存储层（2026-10-31）。

权威源
------
所有私人健康数据都在当前个人数据库的 ``02_生活运营/02_健康系统/``：

- ``01_每日健康/YYYY-MM-DD.md``：一天一篇，frontmatter 存结构化数值
  （``sleep`` / ``body`` / ``vitals`` / ``nutrition`` / ``activity`` / ``mind``
  六个分区 + ``meals`` / ``training`` 列表 + ``custom``），正文由后端按数据重写，
  只做可读摘要，不承载第二份事实；
- ``02_身体档案/身体档案（profile）.md``：身高/生日/性别、病史过敏用药、目标、
  自定义指标、评分权重；
- ``03_体检与检验/检验记录（labs）.md``：一张 Markdown 表，带参考区间与异常标记；
- ``00_健康报告/``：私人医生生成的报告（由 ``pillar_notes`` 写入）。

应用自己的状态（洞察已读）放 ``<个人数据库>/.torch-os/health/``，和恋爱系统
``.torch-os/love/`` 同一层：不进 Vault 扫描、不伪装成个人数据。

写入协议与 ``markdown_store`` 一致：先读（拿 revision）→ 改内存 → 比对磁盘字节 →
临时文件 + ``os.replace``。别处改过就 409，绝不覆盖。
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException

from . import health_catalog, vault_layout
from .markdown_store import guarded_write, load_note, revision
from .vault import parse_text, safe_note_path

HEALTH_ROOT = "02_生活运营/02_健康系统"
DAILY_DIR = f"{HEALTH_ROOT}/01_每日健康"
PROFILE_DIR = f"{HEALTH_ROOT}/02_身体档案"
PROFILE_REL = f"{PROFILE_DIR}/身体档案（profile）.md"
PROFILE_LEGACY = f"{PROFILE_DIR}/profile.md"
LAB_DIR = f"{HEALTH_ROOT}/03_体检与检验"
LAB_REL = f"{LAB_DIR}/检验记录（labs）.md"
LAB_LEGACY = f"{LAB_DIR}/labs.md"
TRAIN_DIR = f"{HEALTH_ROOT}/04_训练记录"
CONSULT_DIR = f"{HEALTH_ROOT}/05_问诊记录"
REPORT_DIR = f"{HEALTH_ROOT}/00_健康报告"
DAY_SCHEMA = "health-day-v1"
PROFILE_SCHEMA = "health-profile-v1"
LAB_SCHEMA = "health-labs-v1"

SECTIONS = ("sleep", "body", "vitals", "nutrition", "activity", "mind")
MAX_RANGE_DAYS = 366
MAX_DAY_BYTES = 512 * 1024

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ROW_RE = re.compile(r"^[0-9a-f]{6,24}$")

DEFAULT_WEIGHTS = {"sleep": 0.25, "nutrition": 0.2, "activity": 0.2,
                   "mental": 0.15, "body": 0.1, "training": 0.1}
DEFAULT_TARGETS = {"weight_kg": None, "body_fat_pct": None, "sleep_minutes": 450,
                   "steps": 8000, "water_ml": 2000, "weekly_training": 4,
                   "calories": None, "protein_g": None}


# ── 基础工具 ─────────────────────────────────────────────────────────

def validate_day(value: Any) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.fullmatch(text):
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD")
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise HTTPException(422, "日期不存在") from error
    return text


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def shift_day(day: str, delta: int) -> str:
    return (date.fromisoformat(validate_day(day)) + timedelta(days=delta)).isoformat()


def day_relpath(day: str) -> str:
    return f"{DAILY_DIR}/{validate_day(day)}.md"


def day_path(vault_dir: Path | str, day: str) -> Path:
    return safe_note_path(Path(vault_dir), day_relpath(day))


def profile_path(vault_dir: Path | str) -> Path:
    root = Path(vault_dir)
    current = safe_note_path(root, PROFILE_REL)
    if not current.exists():
        legacy = safe_note_path(root, PROFILE_LEGACY)
        if legacy.exists():
            return legacy
    return current


def lab_path(vault_dir: Path | str) -> Path:
    root = Path(vault_dir)
    current = safe_note_path(root, LAB_REL)
    if not current.exists():
        legacy = safe_note_path(root, LAB_LEGACY)
        if legacy.exists():
            return legacy
    return current


def relative(vault_dir: Path | str, path: Path) -> str:
    try:
        return path.relative_to(Path(vault_dir).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_text(value: Any, limit: int = 2000) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def coerce_value(key: str, value: Any) -> Any:
    """按目录校验并归一化一个值；空值返回 None（表示删除该字段）。"""
    spec = health_catalog.metric(key)
    if spec is None:
        raise HTTPException(422, f"未知健康指标：{key}")
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    kind = spec["kind"]
    if kind == "text":
        return _clean_text(value)
    if kind == "time":
        text = str(value).strip()
        if not _TIME_RE.fullmatch(text):
            raise HTTPException(422, f"{spec['label']}应为 HH:MM")
        return text
    number = _finite(value)
    if number is None:
        raise HTTPException(422, f"{spec['label']}必须是数字")
    if kind in {"int", "minutes", "rating"}:
        number = int(round(number))
    else:
        number = round(number, int(spec.get("precision") or 1))
    minimum, maximum = spec.get("min"), spec.get("max")
    if minimum is not None and number < minimum:
        raise HTTPException(422, f"{spec['label']}不能小于 {minimum:g}")
    if maximum is not None and number > maximum:
        raise HTTPException(422, f"{spec['label']}不能大于 {maximum:g}")
    return number


def clean_section(section: str, values: Any, *, strict: bool = True) -> dict[str, Any]:
    """只保留目录里声明的字段；未知字段在 strict 模式下报 422。"""
    if section not in SECTIONS:
        raise HTTPException(404, f"未知健康分区：{section}")
    if values is None:
        return {}
    if not isinstance(values, dict):
        raise HTTPException(422, "健康记录必须是键值对象")
    allowed = {item["field"] for item in health_catalog.section_fields(section)}
    result: dict[str, Any] = {}
    for field, raw in values.items():
        if field not in allowed:
            if strict:
                raise HTTPException(422, f"{section} 没有指标 {field}")
            continue
        cleaned = coerce_value(f"{section}.{field}", raw)
        if cleaned is not None:
            result[field] = cleaned
    return result


# ── 每日记录 ─────────────────────────────────────────────────────────

def empty_day(day: str) -> dict[str, Any]:
    return {
        "date": validate_day(day), "exists": False, "path": day_relpath(day),
        "revision": "", "created": "", "updated": "",
        "sections": {section: {} for section in SECTIONS},
        "meals": [], "training": [], "custom": {}, "note": "",
        "tags": [],
    }


def _section_dict(meta: dict[str, Any], section: str) -> dict[str, Any]:
    raw = meta.get(section)
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for item in health_catalog.section_fields(section):
        field = item["field"]
        if field not in raw:
            continue
        try:
            value = coerce_value(f"{section}.{field}", raw[field])
        except HTTPException:
            continue  # 手工改坏的值按没有处理，不让整篇记录读不出来
        if value is not None:
            cleaned[field] = value
    return cleaned


def read_day(vault_dir: Path | str, day: str) -> dict[str, Any]:
    day = validate_day(day)
    path = day_path(vault_dir, day)
    data = empty_day(day)
    data["path"] = relative(vault_dir, path) if path.exists() else day_relpath(day)
    loaded = load_note(path)
    if not loaded["exists"]:
        return data
    if len(loaded["raw"] or b"") > MAX_DAY_BYTES:
        raise HTTPException(413, "这一天的健康记录超过 512 KiB，已停止读取")
    try:
        meta, _body = parse_text((loaded["raw"] or b"").decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(409, f"这一天的健康记录格式坏了：{error}") from error
    if not isinstance(meta, dict):
        meta = {}
    data.update({
        "exists": True, "revision": loaded["revision"],
        "created": str(meta.get("created") or ""),
        "updated": str(meta.get("updated") or ""),
        "note": _clean_text(meta.get("note"), 4000),
        "meals": [item for item in (meta.get("meals") or []) if isinstance(item, dict)],
        "training": [item for item in (meta.get("training") or []) if isinstance(item, dict)],
        "custom": {str(key): value for key, value in (meta.get("custom") or {}).items()
                   if _finite(value) is not None},
        "tags": [str(tag) for tag in (meta.get("tags") or []) if str(tag).strip()][:20],
    })
    for section in SECTIONS:
        data["sections"][section] = _section_dict(meta, section)
    return data


def _frontmatter(data: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "type": "health_day", "title": f"{data['date']} 健康记录",
        "date": data["date"], "schema": DAY_SCHEMA, "pillar": "ops",
        "chapter": "health_daily",
        "created": data.get("created") or _now(), "updated": _now(),
    }
    for section in SECTIONS:
        values = {key: value for key, value in (data.get("sections", {}).get(section) or {}).items()
                  if value not in (None, "")}
        if values:
            meta[section] = values
    if data.get("meals"):
        meta["meals"] = data["meals"]
    if data.get("training"):
        meta["training"] = data["training"]
    if data.get("custom"):
        meta["custom"] = data["custom"]
    if data.get("note"):
        meta["note"] = data["note"]
    if data.get("tags"):
        meta["tags"] = data["tags"]
    return meta


def write_day(vault_dir: Path | str, data: dict[str, Any],
              expected_revision: str = "") -> dict[str, Any]:
    """整篇写回每日记录；正文由数据重新生成，保留原有 revision 语义。"""
    day = validate_day(data["date"])
    path = day_path(vault_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    loaded = load_note(path)
    if expected_revision and loaded["exists"] and loaded["revision"] != expected_revision:
        raise HTTPException(409, "这一天的健康记录已在别处修改，请刷新后重试")
    # 评分是读取时计算的派生值；不再把某一天的分数写进 frontmatter，
    # 避免 Obsidian 里看到的旧六维分数和新版四维分数对不上。
    meta = _frontmatter({**data, "date": day})
    body = render_body({**data, "date": day})
    text = "\n".join([
        "---",
        yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip("\n"),
        "---", "", *body, "",
    ])
    lines = text.replace("\r\n", "\n").split("\n")
    if loaded["exists"]:
        guarded_write(loaded, lines)
    else:
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return read_day(vault_dir, day)


def _merge_sections(data: dict[str, Any], section: str, values: dict[str, Any]) -> None:
    target = dict(data["sections"].get(section) or {})
    for field, value in values.items():
        if value is None:
            target.pop(field, None)
        else:
            target[field] = value
    data["sections"][section] = target


def patch_day(vault_dir: Path | str, day: str, changes: dict[str, Any],
              expected_revision: str = "") -> dict[str, Any]:
    """按分区合并写入：``changes = {"sleep": {...}, "note": "..."}``。

    值传 ``None`` 或空字符串表示删除该字段。写入后由调用方重算评分。
    """
    data = read_day(vault_dir, day)
    if expected_revision and data["exists"] and data["revision"] != expected_revision:
        raise HTTPException(409, "这一天的健康记录已在别处修改，请刷新后重试")
    for key, raw in (changes or {}).items():
        if key in SECTIONS:
            _merge_sections(data, key, clean_section(key, raw))
        elif key == "note":
            data["note"] = _clean_text(raw, 4000)
        elif key == "custom":
            values = data.setdefault("custom", {})
            for name, value in (raw or {}).items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    values.pop(str(name), None)
                else:
                    number = _finite(value)
                    if number is None:
                        raise HTTPException(422, f"自定义指标 {name} 必须是数字")
                    values[str(name)] = round(number, 2)
        elif key in {"meals", "training"}:
            raise HTTPException(422, "餐次与训练请用各自的增删接口")
        else:
            raise HTTPException(422, f"未知健康分区：{key}")
    _fill_sleep_duration(data["sections"].get("sleep") or {})
    return write_day(vault_dir, data, expected_revision)


def _fill_sleep_duration(sleep: dict[str, Any]) -> None:
    """有入睡/起床时间但没有时长时自动补；跨午夜按 +24h 处理。"""
    if sleep.get("duration_min") or not sleep.get("bed") or not sleep.get("wake"):
        return
    try:
        bed = datetime.strptime(sleep["bed"], "%H:%M")
        wake = datetime.strptime(sleep["wake"], "%H:%M")
    except ValueError:
        return
    minutes = int((wake - bed).total_seconds() // 60)
    if minutes <= 0:
        minutes += 24 * 60
    if 0 < minutes <= 1440:
        sleep["duration_min"] = minutes


# ── 餐次与训练 ───────────────────────────────────────────────────────

MEAL_TYPES = ("早餐", "午餐", "晚餐", "加餐")
MEAL_FIELDS = {"name", "amount_g", "calories", "protein_g", "carbs_g", "fat_g",
               "fiber_g", "sodium_mg"}


def _meal_clean(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(422, "餐次记录必须是对象")
    name = _clean_text(payload.get("name"), 60)
    if not name:
        raise HTTPException(422, "食物名称不能为空")
    meal_type = str(payload.get("meal_type") or payload.get("type") or "加餐").strip()
    if meal_type not in MEAL_TYPES:
        raise HTTPException(422, "餐次只能是早餐/午餐/晚餐/加餐")
    meal: dict[str, Any] = {"id": uuid4().hex[:8], "meal_type": meal_type, "name": name}
    for field in MEAL_FIELDS - {"name"}:
        number = _finite(payload.get(field))
        if number is not None:
            if number < 0 or number > 10000:
                raise HTTPException(422, "餐次数值超出范围")
            meal[field] = round(number, 1) if field not in {"calories", "sodium_mg"} else int(round(number))
    portion = _clean_text(payload.get("portion"), 30)
    if portion:
        meal["portion"] = portion
    source = str(payload.get("source") or "").strip().lower()
    if source in {"ai", "preset", "manual"}:
        meal["source"] = source
    return meal


def add_meal(vault_dir: Path | str, day: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = read_day(vault_dir, day)
    meal = _meal_clean(payload)
    data["meals"] = [*data["meals"], meal]
    return write_day(vault_dir, data, data["revision"])


def delete_meal(vault_dir: Path | str, day: str, meal_id: str) -> dict[str, Any]:
    data = read_day(vault_dir, day)
    remaining = [item for item in data["meals"] if str(item.get("id")) != str(meal_id)]
    if len(remaining) == len(data["meals"]):
        raise HTTPException(404, "这条餐次记录不存在")
    data["meals"] = remaining
    return write_day(vault_dir, data, data["revision"])


TRAINING_KINDS = ("力量", "跑步", "快走", "骑行", "游泳", "球类", "瑜伽", "柔韧",
                  "有氧", "其他")


def _training_clean(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(422, "运动记录必须是对象")
    kind = str(payload.get("kind") or "其他").strip()
    if kind not in TRAINING_KINDS:
        kind = "其他"
    session: dict[str, Any] = {"id": uuid4().hex[:8], "kind": kind, "exercises": []}
    for field in ("minutes", "rpe", "soreness", "mood_after", "calories", "steps"):
        number = _finite(payload.get(field))
        if number is not None:
            spec = health_catalog.metric(f"training.{field}")
            if spec and spec["min"] is not None and number < spec["min"]:
                raise HTTPException(422, f"{spec['label']}超出范围")
            if spec and spec["max"] is not None and number > spec["max"]:
                raise HTTPException(422, f"{spec['label']}超出范围")
            session[field] = int(round(number))
    distance = _finite(payload.get("distance_km"))
    if distance is not None:
        if not 0 <= distance <= 200:
            raise HTTPException(422, "运动距离超出范围")
        session["distance_km"] = round(distance, 1)
    session["note"] = _clean_text(payload.get("note") or payload.get("text"), 600)
    source = str(payload.get("source") or "").strip().lower()
    if source in {"ai", "preset", "manual", "device"}:
        session["source"] = source
    exercises = payload.get("exercises")
    total_volume = 0.0
    if isinstance(exercises, list):
        for item in exercises[:40]:
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"), 60)
            if not name:
                continue
            exercise_id = _clean_text(item.get("exercise_id"), 20)
            sets: list[dict[str, Any]] = []
            for raw_set in (item.get("sets") or [])[:20]:
                if not isinstance(raw_set, dict):
                    continue
                reps = _finite(raw_set.get("reps"))
                weight = _finite(raw_set.get("weight_kg"))
                if reps is None and weight is None:
                    continue
                entry = {}
                if reps is not None and 0 <= reps <= 1000:
                    entry["reps"] = int(round(reps))
                if weight is not None and 0 <= weight <= 1000:
                    entry["weight_kg"] = round(weight, 1)
                if entry:
                    sets.append(entry)
                    total_volume += (entry.get("reps") or 0) * (entry.get("weight_kg") or 0)
            if sets:
                session["exercises"].append({"exercise_id": exercise_id, "name": name,
                                             "sets": sets})
    session["volume_kg"] = round(total_volume, 1)
    if (not session["exercises"] and not session.get("minutes")
            and not session.get("calories") and not session.get("steps")
            and not session["note"]):
        raise HTTPException(422, "运动记录至少要写一句、填时长或消耗")
    return session


def add_training(vault_dir: Path | str, day: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = read_day(vault_dir, day)
    session = _training_clean(payload)
    data["training"] = [*data["training"], session]
    return write_day(vault_dir, data, data["revision"])


def delete_training(vault_dir: Path | str, day: str, session_id: str) -> dict[str, Any]:
    data = read_day(vault_dir, day)
    remaining = [item for item in data["training"] if str(item.get("id")) != str(session_id)]
    if len(remaining) == len(data["training"]):
        raise HTTPException(404, "这条训练记录不存在")
    data["training"] = remaining
    return write_day(vault_dir, data, data["revision"])


def replace_meals(vault_dir: Path | str, day: str, meals: list[dict[str, Any]],
                  expected_revision: str = "") -> dict[str, Any]:
    """整组替换当天的餐次；用于饮食弹窗一次保存。"""
    data = read_day(vault_dir, day)
    if expected_revision and data["exists"] and data["revision"] != expected_revision:
        raise HTTPException(409, "这一天的健康记录已在别处修改，请刷新后重试")
    cleaned = [_meal_clean(item) for item in (meals or [])[:60] if isinstance(item, dict)]
    data["meals"] = cleaned
    return write_day(vault_dir, data, expected_revision)


def replace_training(vault_dir: Path | str, day: str, sessions: list[dict[str, Any]],
                     expected_revision: str = "") -> dict[str, Any]:
    """整组替换当天的运动/训练；用于运动弹窗一次保存。"""
    data = read_day(vault_dir, day)
    if expected_revision and data["exists"] and data["revision"] != expected_revision:
        raise HTTPException(409, "这一天的健康记录已在别处修改，请刷新后重试")
    cleaned = [_training_clean(item) for item in (sessions or [])[:40] if isinstance(item, dict)]
    data["training"] = cleaned
    return write_day(vault_dir, data, expected_revision)


def replace_diet(vault_dir: Path | str, day: str, *, food_text: str = "",
                 water_ml: float | None = None, meals: list[dict[str, Any]] | None = None,
                 expected_revision: str = "") -> dict[str, Any]:
    """饮食弹窗的一次性保存：自由描述 + 饮水 + 餐次明细。"""
    data = read_day(vault_dir, day)
    if expected_revision and data["exists"] and data["revision"] != expected_revision:
        raise HTTPException(409, "这一天的健康记录已在别处修改，请刷新后重试")
    values = dict((data.get("sections") or {}).get("nutrition") or {})
    text = _clean_text(food_text, 2000)
    if text:
        values["food_text"] = text
    else:
        values.pop("food_text", None)
    if water_ml is None:
        pass
    else:
        water = _finite(water_ml)
        if water is None or water < 0 or water > 8000:
            raise HTTPException(422, "饮水量应在 0-8000 ml")
        values["water_ml"] = int(round(water))
    data["sections"]["nutrition"] = values
    data["meals"] = [_meal_clean(item) for item in (meals or [])[:60]
                     if isinstance(item, dict)]
    return write_day(vault_dir, data, expected_revision)


def replace_exercise(vault_dir: Path | str, day: str, *, exercise_text: str = "",
                     steps: float | None = None, sessions: list[dict[str, Any]] | None = None,
                     expected_revision: str = "") -> dict[str, Any]:
    """运动弹窗的一次性保存：自由描述 + 步数 + 运动明细。"""
    data = read_day(vault_dir, day)
    if expected_revision and data["exists"] and data["revision"] != expected_revision:
        raise HTTPException(409, "这一天的健康记录已在别处修改，请刷新后重试")
    values = dict((data.get("sections") or {}).get("activity") or {})
    text = _clean_text(exercise_text, 2000)
    if text:
        values["exercise_text"] = text
    else:
        values.pop("exercise_text", None)
    if steps is not None:
        number = _finite(steps)
        if number is None or number < 0 or number > 100000:
            raise HTTPException(422, "步数应在 0-100000")
        values["steps"] = int(round(number))
    data["sections"]["activity"] = values
    data["training"] = [_training_clean(item) for item in (sessions or [])[:40]
                        if isinstance(item, dict)]
    return write_day(vault_dir, data, expected_revision)


# ── 身体档案 ─────────────────────────────────────────────────────────

def empty_profile() -> dict[str, Any]:
    return {
        "exists": False, "path": PROFILE_REL, "revision": "",
        "name": "", "gender": "", "birth_date": "", "height_cm": None,
        "unit": "metric", "blood_type": "", "medical_history": "", "allergies": "",
        "medications": [], "goals": dict(DEFAULT_TARGETS),
        "custom_metrics": [], "score_weights": dict(DEFAULT_WEIGHTS), "note": "",
        "created": "", "updated": "",
    }


def _coerce_profile(raw: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    data = dict(base)
    data["name"] = _clean_text(raw.get("name"), 40)
    gender = str(raw.get("gender") or "").strip()
    if gender in {"男", "male"}:
        gender = "男"
    elif gender in {"女", "female"}:
        gender = "女"
    elif gender not in {"男", "女", "其他", ""}:
        gender = ""
    data["gender"] = gender
    birth = str(raw.get("birth_date") or "").strip()
    if birth:
        try:
            date.fromisoformat(birth)
        except ValueError as error:
            raise HTTPException(422, "出生日期格式应为 YYYY-MM-DD") from error
    data["birth_date"] = birth
    height = _finite(raw.get("height_cm"))
    if height is not None and not 50 <= height <= 260:
        raise HTTPException(422, "身高应在 50-260 cm")
    data["height_cm"] = round(height, 1) if height is not None else None
    data["blood_type"] = _clean_text(raw.get("blood_type"), 10)
    data["medical_history"] = _clean_text(raw.get("medical_history"), 4000)
    data["allergies"] = _clean_text(raw.get("allergies"), 2000)
    data["note"] = _clean_text(raw.get("note"), 2000)
    medications = []
    for item in (raw.get("medications") or [])[:30]:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"), 60)
        if not name:
            continue
        medications.append({
            "name": name,
            "dosage": _clean_text(item.get("dosage"), 40),
            "frequency": _clean_text(item.get("frequency"), 40),
            "start_date": _clean_text(item.get("start_date"), 10),
            "end_date": _clean_text(item.get("end_date"), 10),
            "active": bool(item.get("active", True)),
            "note": _clean_text(item.get("note"), 200),
        })
    data["medications"] = medications
    goals = dict(DEFAULT_TARGETS)
    raw_goals = raw.get("goals") if isinstance(raw.get("goals"), dict) else {}
    for key, value in raw_goals.items():
        if key not in DEFAULT_TARGETS:
            continue
        number = _finite(value)
        if number is not None:
            goals[key] = round(number, 1)
    data["goals"] = goals
    custom = []
    seen: set[str] = set()
    for item in (raw.get("custom_metrics") or [])[:30]:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"), 20)
        if not label or label in seen:
            continue
        seen.add(label)
        custom.append({"key": label, "label": label,
                       "unit": _clean_text(item.get("unit"), 10)[:10],
                       "min": _finite(item.get("min")), "max": _finite(item.get("max"))})
    data["custom_metrics"] = custom
    weights = dict(DEFAULT_WEIGHTS)
    raw_weights = raw.get("score_weights") if isinstance(raw.get("score_weights"), dict) else {}
    for key, value in raw_weights.items():
        if key in weights:
            number = _finite(value)
            if number is not None and 0 <= number <= 1:
                weights[key] = round(number, 3)
    data["score_weights"] = weights
    return data


def read_profile(vault_dir: Path | str) -> dict[str, Any]:
    base = empty_profile()
    path = profile_path(vault_dir)
    base["path"] = relative(vault_dir, path) if path.exists() else PROFILE_REL
    loaded = load_note(path)
    if not loaded["exists"]:
        return base
    try:
        meta, _body = parse_text((loaded["raw"] or b"").decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(409, f"身体档案格式坏了：{error}") from error
    if not isinstance(meta, dict):
        meta = {}
    for field in ("name", "gender", "birth_date", "height_cm", "blood_type",
                  "medical_history", "allergies", "note", "created", "updated"):
        if field in meta:
            base[field] = meta[field]
    for field in ("medications", "goals", "custom_metrics", "score_weights"):
        if isinstance(meta.get(field), dict | list):
            base[field] = meta[field]
    data = _coerce_profile(base, empty_profile())
    data.update({"exists": True, "revision": loaded["revision"], "path": base["path"],
                 "created": str(base.get("created") or ""),
                 "updated": str(base.get("updated") or "")})
    return data


def write_profile(vault_dir: Path | str, payload: dict[str, Any],
                  expected_revision: str = "") -> dict[str, Any]:
    current = read_profile(vault_dir)
    if expected_revision and current["exists"] and current["revision"] != expected_revision:
        raise HTTPException(409, "身体档案已在别处修改，请刷新后重试")
    merged = {**current, **(payload or {})}
    data = _coerce_profile(merged, empty_profile())
    meta = {
        "type": "health_profile", "title": "身体档案", "schema": PROFILE_SCHEMA,
        "pillar": "ops", "chapter": "health_profile",
        "created": current.get("created") or _now(), "updated": _now(),
        "name": data["name"], "gender": data["gender"],
        "birth_date": data["birth_date"], "height_cm": data["height_cm"],
        "unit": data["unit"], "blood_type": data["blood_type"],
        "medical_history": data["medical_history"], "allergies": data["allergies"],
        "medications": data["medications"], "goals": data["goals"],
        "custom_metrics": data["custom_metrics"], "score_weights": data["score_weights"],
        "note": data["note"],
    }
    lines = ["---", yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip("\n"),
             "---", "", f"# 身体档案", "",
             "> 由健康系统维护；身高、生日、病史、过敏与用药属于敏感数据，"
             "只在本机保存，不进入 AI 记忆候选。", "",
             "## 基础", "",
             f"- 称呼：{data['name'] or '未填写'}",
             f"- 性别：{data['gender'] or '未填写'}",
             f"- 出生日期：{data['birth_date'] or '未填写'}",
             f"- 身高：{data['height_cm'] or '未填写'}{' cm' if data['height_cm'] else ''}",
             f"- 血型：{data['blood_type'] or '未填写'}", "",
             "## 病史 / 过敏 / 用药", "",
             f"- 既往病史：{data['medical_history'] or '未填写'}",
             f"- 过敏：{data['allergies'] or '未填写'}",
             *[f"- 用药：{item['name']} {item.get('dosage', '')} {item.get('frequency', '')}".rstrip()
               for item in data["medications"]], "",
             "## 目标", "",
             *[f"- {health_goal_label(key)}：{value}" for key, value in data["goals"].items()
               if value not in (None, "")], ""]
    path = profile_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    loaded = load_note(path)
    if loaded["exists"]:
        guarded_write(loaded, lines)
    else:
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return read_profile(vault_dir)


GOAL_LABELS = {"weight_kg": "目标体重（kg）", "body_fat_pct": "目标体脂（%）",
               "sleep_minutes": "目标睡眠（分钟）", "steps": "目标步数",
               "water_ml": "目标饮水（ml）", "weekly_training": "每周训练次数",
               "calories": "目标热量（kcal）", "protein_g": "目标蛋白（g）"}


def health_goal_label(key: str) -> str:
    return GOAL_LABELS.get(key, key)


# ── 体检与检验 ───────────────────────────────────────────────────────

LAB_COLUMNS = ("id", "date", "category", "test", "value", "unit",
               "ref_min", "ref_max", "abnormal", "note")


def clean_labs(vault_dir: Path | str, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if rows is None:
        return []
    cleaned: list[dict[str, Any]] = []
    for item in rows[:500]:
        if not isinstance(item, dict):
            continue
        try:
            day = validate_day(item.get("date"))
        except HTTPException:
            continue
        test = _clean_text(item.get("test"), 60)
        value = _finite(item.get("value"))
        if not test or value is None:
            continue
        ref_min, ref_max = _finite(item.get("ref_min")), _finite(item.get("ref_max"))
        raw_abnormal = item.get("abnormal")
        if isinstance(raw_abnormal, str):
            abnormal = raw_abnormal.strip() in {"是", "true", "True", "1", "yes"}
        elif raw_abnormal is None:
            abnormal = bool((ref_min is not None and value < ref_min)
                            or (ref_max is not None and value > ref_max))
        else:
            abnormal = bool(raw_abnormal)
        row_id = str(item.get("id") or "").strip()
        if not _ROW_RE.fullmatch(row_id):
            row_id = uuid4().hex[:10]
        cleaned.append({
            "id": row_id, "date": day,
            "category": _clean_text(item.get("category"), 10) or "其他",
            "test": test, "value": round(value, 3),
            "unit": _clean_text(item.get("unit"), 12),
            "ref_min": ref_min, "ref_max": ref_max,
            "abnormal": abnormal, "note": _clean_text(item.get("note"), 200),
        })
    cleaned.sort(key=lambda row: (row["date"], row["test"]))
    return cleaned


def _lab_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).replace("|", "／").replace("\n", " ")


def read_labs(vault_dir: Path | str) -> dict[str, Any]:
    path = lab_path(vault_dir)
    result = {"exists": False, "path": relative(vault_dir, path) if path.exists() else LAB_REL,
              "revision": "", "rows": []}
    loaded = load_note(path)
    if not loaded["exists"]:
        return result
    try:
        _meta, body = parse_text((loaded["raw"] or b"").decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(409, f"检验记录格式坏了：{error}") from error
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < len(LAB_COLUMNS):
            cells.extend([""] * (len(LAB_COLUMNS) - len(cells)))
        if cells[0] in {"ID", ""} or all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        row = dict(zip(LAB_COLUMNS, cells))
        if not _ROW_RE.fullmatch(row["id"]):
            continue
        rows.append(row)
    result.update({"exists": True, "revision": loaded["revision"],
                   "rows": clean_labs(vault_dir, rows)})
    return result


def save_labs(vault_dir: Path | str, rows: list[dict[str, Any]],
              expected_revision: str = "") -> dict[str, Any]:
    path = lab_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    loaded = load_note(path)
    if expected_revision and loaded["exists"] and loaded["revision"] != expected_revision:
        raise HTTPException(409, "检验记录已在别处修改，请刷新后重试")
    cleaned = clean_labs(vault_dir, rows)
    header = "| " + " | ".join(["ID", "日期", "分类", "项目", "数值", "单位",
                                "参考下限", "参考上限", "异常", "备注"]) + " |"
    divider = "| " + " | ".join(["---"] * len(LAB_COLUMNS)) + " |"
    meta = {"type": "health_lab", "title": "检验记录", "schema": LAB_SCHEMA,
            "pillar": "ops", "chapter": "health_lab",
            "updated": _now()}
    lines = ["---", yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip("\n"),
             "---", "", "# 检验记录", "",
             "> 由健康系统维护；异常是按参考区间做的提示，不是诊断。"
             "有报告时以报告上的区间为准。", "", header, divider]
    for row in cleaned:
        lines.append("| " + " | ".join(_lab_cell(row[key]) for key in LAB_COLUMNS) + " |")
    lines.append("")
    if loaded["exists"]:
        guarded_write(loaded, lines)
    else:
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return read_labs(vault_dir)


# ── 区间读取与派生 ───────────────────────────────────────────────────

def list_days(vault_dir: Path | str, *, start: str = "", end: str = "",
              limit: int = 366) -> list[dict[str, Any]]:
    """按日期升序读取一段时间的每日健康记录（缺失的天跳过）。"""
    root = Path(vault_dir) / vault_layout.chapter_dir("health_daily")
    if not root.is_dir():
        return []
    end_day = validate_day(end or today())
    start_day = validate_day(start or (date.fromisoformat(end_day)
                                       - timedelta(days=max(1, min(limit, MAX_RANGE_DAYS)) - 1)
                                       ).isoformat())
    if start_day > end_day:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    days: list[dict[str, Any]] = []
    try:
        entries = sorted(root.glob("*.md"))
    except OSError:
        return []
    for path in entries:
        day = path.stem
        if not _DATE_RE.fullmatch(day) or day < start_day or day > end_day:
            continue
        try:
            days.append(read_day(vault_dir, day))
        except HTTPException:
            continue
        if len(days) >= limit:
            break
    return days


def recent_range(days: int, end: str = "") -> tuple[str, str]:
    count = max(1, min(int(days or 30), MAX_RANGE_DAYS))
    end_day = validate_day(end or today())
    start_day = (date.fromisoformat(end_day) - timedelta(days=count - 1)).isoformat()
    return start_day, end_day


def meal_totals(data: dict[str, Any]) -> dict[str, float]:
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0,
              "fiber_g": 0.0, "sodium_mg": 0.0}
    for meal in data.get("meals") or []:
        for key in totals:
            number = _finite(meal.get(key))
            if number is not None:
                totals[key] += number
    return {key: round(value, 1) for key, value in totals.items()}


def nutrition_totals(data: dict[str, Any]) -> dict[str, float]:
    """有餐次时以餐次合计为准；没有餐次时用营养分区里手填的总量。"""
    section = data.get("sections", {}).get("nutrition") or {}
    totals = meal_totals(data)
    if data.get("meals"):
        for key in ("water_ml", "caffeine_mg", "alcohol_units"):
            number = _finite(section.get(key))
            if number is not None:
                totals[key] = number
        return totals
    for key in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g"):
        number = _finite(section.get(key))
        if number is not None:
            totals[key] = number
    for key in ("water_ml", "caffeine_mg", "alcohol_units"):
        number = _finite(section.get(key))
        if number is not None:
            totals[key] = number
    return totals


def training_totals(data: dict[str, Any]) -> dict[str, float]:
    minutes = 0.0
    volume = 0.0
    calories = 0.0
    distance = 0.0
    steps = 0.0
    for session in data.get("training") or []:
        minutes += _finite(session.get("minutes")) or 0
        volume += _finite(session.get("volume_kg")) or 0
        calories += _finite(session.get("calories")) or 0
        distance += _finite(session.get("distance_km")) or 0
        steps += _finite(session.get("steps")) or 0
    return {"minutes": round(minutes, 1), "volume_kg": round(volume, 1),
            "calories": round(calories, 1), "distance_km": round(distance, 1),
            "steps": round(steps, 1), "sessions": float(len(data.get("training") or []))}


def _has_nutrition(data: dict[str, Any]) -> bool:
    section = (data.get("sections") or {}).get("nutrition") or {}
    if data.get("meals"):
        return True
    return any(section.get(key) is not None
               for key in ("calories", "protein_g", "carbs_g", "fat_g"))


def _has_activity(data: dict[str, Any]) -> bool:
    section = (data.get("sections") or {}).get("activity") or {}
    if data.get("training"):
        return True
    return any(section.get(key) is not None
               for key in ("steps", "exercise_text", "sedentary_min", "active_cal"))


def latest_payload(vault_dir: Path | str, end_day: str = "",
                   lookback: int = 90) -> dict[str, Any]:
    """在选定日期（含）之前，找每个分区最近一次非空记录。

    只在读取时使用：不把旧数据复制成今天的新记录。返回的 ``age`` 是距选定
    日期的天数，供前端显示「沿用 X 月 X 日」和判断是否过期；
    ``previous`` 是再往前一次，用来显示「比上次」。
    """
    end = validate_day(end_day or today())
    span = max(1, min(int(lookback or 90), MAX_RANGE_DAYS))
    start = shift_day(end, -(span - 1))
    records = list_days(vault_dir, start=start, end=end, limit=span)
    by_date = {record["date"]: record for record in records}

    def find(predicate, from_offset: int = 0):
        for offset in range(max(0, from_offset), span):
            day = shift_day(end, -offset)
            record = by_date.get(day)
            if record and predicate(record):
                return day, offset, record
        return None

    def build(section: str, day: str, offset: int, record: dict[str, Any]) -> dict[str, Any]:
        values = (record.get("sections") or {}).get(section) or {}
        return {"date": day, "age": offset, "values": values}

    def with_previous(section: str, predicate, extra=None, fallback=None):
        match = find(predicate)
        if match is None and fallback is not None:
            match = find(fallback)
        if match is None:
            return
        day, offset, record = match
        item = {**build(section, day, offset, record), **(extra(record) if extra else {})}
        previous = find(predicate, offset + 1)
        if previous is not None:
            prev_day, prev_offset, prev_record = previous
            item["previous"] = build(section, prev_day, prev_offset, prev_record)
        result[section] = item

    result: dict[str, Any] = {}
    for section in ("sleep", "body"):
        with_previous(section, lambda record, section=section:
                      bool((record.get("sections") or {}).get(section)))

    with_previous(
        "nutrition", _has_nutrition,
        extra=lambda record: {
            "meals": record.get("meals") or [],
            "totals": nutrition_totals(record),
        },
        fallback=lambda record: bool((record.get("sections") or {}).get("nutrition")),
    )
    with_previous(
        "activity", _has_activity,
        extra=lambda record: {
            "training": record.get("training") or [],
            "totals": training_totals(record),
        },
    )
    custom = find(lambda record: bool(record.get("custom")))
    if custom is not None:
        day, offset, record = custom
        result["custom"] = {"date": day, "age": offset,
                            "values": dict(record.get("custom") or {})}
    return result


def custom_metric_keys(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in (profile.get("custom_metrics") or [])
            if isinstance(item, dict) and item.get("label")]


# ── 正文（可读摘要）─────────────────────────────────────────────────

def _fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    number = _finite(value)
    if number is None:
        return "—"
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{number:.0f}"
    return f"{text}{unit}" if unit else text


def render_body(data: dict[str, Any]) -> list[str]:
    day = data["date"]
    sections = data.get("sections") or {}
    lines = [f"# {day} 健康记录", ""]

    sleep = sections.get("sleep") or {}
    if sleep:
        lines.append("## 睡眠")
        detail = [f"时长 {_fmt(sleep.get('duration_min'), ' 分钟', 0)}"]
        if sleep.get("bed") and sleep.get("wake"):
            detail.append(f"{sleep['bed']} → {sleep['wake']}")
        if sleep.get("quality"):
            detail.append(f"主观质量 {sleep['quality']}/5")
        if sleep.get("deep_min"):
            detail.append(f"深睡 {_fmt(sleep['deep_min'], ' 分钟', 0)}")
        if sleep.get("rem_min"):
            detail.append(f"REM {_fmt(sleep['rem_min'], ' 分钟', 0)}")
        lines += ["- " + "，".join(detail), ""]

    body = sections.get("body") or {}
    if body:
        lines.append("## 身体")
        parts = []
        if body.get("weight_kg") is not None:
            parts.append(f"体重 {_fmt(body['weight_kg'], ' kg')}")
        if body.get("body_fat_pct") is not None:
            parts.append(f"体脂 {_fmt(body['body_fat_pct'], '%')}")
        if body.get("muscle_kg") is not None:
            parts.append(f"肌肉量 {_fmt(body['muscle_kg'], ' kg')}")
        if body.get("waist_cm") is not None:
            parts.append(f"腰围 {_fmt(body['waist_cm'], ' cm')}")
        if body.get("bmr") is not None:
            parts.append(f"BMR {_fmt(body['bmr'], ' kcal', 0)}")
        lines += ["- " + "，".join(parts), ""]

    food_text = str((sections.get("nutrition") or {}).get("food_text") or "").strip()
    nutrition = nutrition_totals(data)
    if data.get("meals") or food_text or (sections.get("nutrition") or {}):
        lines.append("## 饮食")
        if food_text:
            lines += [f"- 吃了什么：{food_text}", ""]
        if data.get("meals"):
            lines += ["| 餐次 | 内容 | 份量 | 热量 | 蛋白 | 碳水 | 脂肪 |",
                      "| --- | --- | --- | --- | --- | --- | --- |"]
            for meal in data["meals"]:
                lines.append("| {type} | {name} | {portion} | {cal} | {protein} | {carbs} | {fat} |".format(
                    type=meal.get("meal_type", ""), name=meal.get("name", ""),
                    portion=meal.get("portion", ""),
                    cal=_fmt(meal.get("calories"), " kcal", 0),
                    protein=_fmt(meal.get("protein_g"), " g"),
                    carbs=_fmt(meal.get("carbs_g"), " g"),
                    fat=_fmt(meal.get("fat_g"), " g")))
            lines.append("")
        lines.append(f"- 合计：热量 {_fmt(nutrition.get('calories'), ' kcal', 0)}，"
                     f"蛋白 {_fmt(nutrition.get('protein_g'), ' g')}，"
                     f"碳水 {_fmt(nutrition.get('carbs_g'), ' g')}，"
                     f"脂肪 {_fmt(nutrition.get('fat_g'), ' g')}，"
                     f"饮水 {_fmt(nutrition.get('water_ml'), ' ml', 0)}")
        lines.append("")

    activity = sections.get("activity") or {}
    exercise_text = str(activity.get("exercise_text") or "").strip()
    if activity or data.get("training"):
        lines.append("## 运动")
        if exercise_text:
            lines.append(f"- 今天做了什么：{exercise_text}")
        if activity.get("steps") is not None:
            lines.append(f"- 步数：{_fmt(activity['steps'], '', 0)}")
        if activity.get("sedentary_min") is not None:
            lines.append(f"- 久坐：{_fmt(activity['sedentary_min'], ' 分钟', 0)}")
        for session in data.get("training") or []:
            meta = [session.get("kind") or "运动"]
            if session.get("minutes"):
                meta.append(f"{session['minutes']} 分钟")
            if session.get("calories"):
                meta.append(f"{_fmt(session['calories'], ' kcal', 0)}")
            if session.get("distance_km"):
                meta.append(f"{_fmt(session['distance_km'], ' km')}")
            if session.get("steps"):
                meta.append(f"{_fmt(session['steps'], ' 步', 0)}")
            if session.get("volume_kg"):
                meta.append(f"容量 {_fmt(session['volume_kg'], ' kg', 0)}")
            if session.get("rpe"):
                meta.append(f"RPE {session['rpe']}")
            lines.append("### " + " · ".join(meta))
            for exercise in session.get("exercises") or []:
                sets = "，".join(
                    f"{entry.get('reps', '?')}×{_fmt(entry.get('weight_kg'), 'kg', 1)}"
                    for entry in exercise.get("sets") or [])
                lines.append(f"- {exercise.get('name')}：{sets}")
            if session.get("note"):
                lines.append(f"- 备注：{session['note']}")
            lines.append("")

    custom = data.get("custom") or {}
    if custom:
        lines.append("## 自定义指标")
        lines += [f"- {name}：{_fmt(value)}" for name, value in custom.items()]
        lines.append("")

    if data.get("note"):
        lines += ["## 备注", "", data["note"], ""]

    # 旧版生命体征/情绪字段仍在 frontmatter 里，保留但不参与新版展示与评分。
    if sections.get("vitals") or sections.get("mind"):
        lines += ["> 生命体征与情绪是旧版字段：仍保留在 frontmatter，新版界面不再展示、"
                  "不参与评分。", ""]

    if (not any(sections.get(section) for section in ("sleep", "body"))
            and not data.get("meals") and not data.get("training")
            and not activity and not custom):
        lines += ["还没有记录。", ""]
    return lines


# ── 给 AI / 报告用的文本摘要 ─────────────────────────────────────────

def summary(vault_dir: Path | str, *, days: int = 30, end: str = "") -> tuple[str, dict[str, Any]]:
    profile = read_profile(vault_dir)
    start, end_day = recent_range(days, end)
    records = list_days(vault_dir, start=start, end=end_day, limit=MAX_RANGE_DAYS)
    labs = read_labs(vault_dir) if (Path(vault_dir) / vault_layout.chapter_dir("health_lab")).is_dir() else {"rows": []}
    latest = latest_payload(vault_dir, end_day, lookback=90)
    from . import health_score  # 避免循环导入
    lines: list[str] = []
    facts: dict[str, Any] = {"days": len(records), "range": [start, end_day]}

    if profile.get("exists"):
        basics = []
        if profile.get("gender"):
            basics.append(profile["gender"])
        if profile.get("birth_date"):
            basics.append(f"出生于 {profile['birth_date']}")
        if profile.get("height_cm"):
            basics.append(f"身高 {_fmt(profile['height_cm'], ' cm')}")
        if profile.get("blood_type"):
            basics.append(f"血型 {profile['blood_type']}")
        if basics:
            lines.append("- 档案：" + "，".join(basics))
        if profile.get("allergies"):
            lines.append(f"- 过敏：{profile['allergies']}")
        if profile.get("medical_history"):
            lines.append(f"- 既往病史：{profile['medical_history']}")
        active = [item for item in profile.get("medications") or [] if item.get("active")]
        if active:
            lines.append("- 在用药：" + "、".join(
                f"{item.get('name')}{(' ' + item['dosage']) if item.get('dosage') else ''}"
                for item in active))
        goals = {key: value for key, value in (profile.get("goals") or {}).items()
                 if value not in (None, "")}
        if goals:
            lines.append("- 目标：" + "、".join(
                f"{health_goal_label(key)} {value}" for key, value in goals.items()))

    # 最新一次（可能不是今天）：沿用规则的事实基础。
    latest_lines: list[str] = []
    sleep_latest = latest.get("sleep")
    if sleep_latest:
        values = sleep_latest["values"]
        detail = f"睡眠 {_fmt(values.get('duration_min'), ' 分钟', 0)}（{sleep_latest['date']}）"
        if values.get("bed") and values.get("wake"):
            detail += f"，{values['bed']} → {values['wake']}"
        if values.get("quality"):
            detail += f"，主观质量 {values['quality']}/5"
        latest_lines.append("- 最新" + detail)
    body_latest = latest.get("body")
    if body_latest:
        values = body_latest["values"]
        parts = [f"体重 {_fmt(values.get('weight_kg'), ' kg')}（{body_latest['date']}）"]
        if values.get("body_fat_pct") is not None:
            parts.append(f"体脂 {_fmt(values['body_fat_pct'], '%')}")
        if values.get("muscle_kg") is not None:
            parts.append(f"肌肉量 {_fmt(values['muscle_kg'], ' kg')}")
        if values.get("waist_cm") is not None:
            parts.append(f"腰围 {_fmt(values['waist_cm'], ' cm')}")
        latest_lines.append("- 最新" + "，".join(parts))
    nutrition_latest = latest.get("nutrition")
    if nutrition_latest:
        totals = nutrition_latest.get("totals") or {}
        latest_lines.append(
            f"- 最新饮食（{nutrition_latest['date']}）：热量 {_fmt(totals.get('calories'), ' kcal', 0)}，"
            f"蛋白 {_fmt(totals.get('protein_g'), ' g')}，饮水 {_fmt(totals.get('water_ml'), ' ml', 0)}；"
            f"{len(nutrition_latest.get('meals') or [])} 条餐次")
        text = str((nutrition_latest.get("values") or {}).get("food_text") or "").strip()
        if text:
            latest_lines.append(f"  描述：{text}")
    activity_latest = latest.get("activity")
    if activity_latest:
        totals = activity_latest.get("totals") or {}
        latest_lines.append(
            f"- 最新运动（{activity_latest['date']}）：{int(totals.get('sessions', 0))} 次，"
            f"{_fmt(totals.get('minutes'), ' 分钟', 0)}，"
            f"消耗 {_fmt(totals.get('calories'), ' kcal', 0)}，"
            f"步数 {_fmt((activity_latest.get('values') or {}).get('steps'), '', 0)}")
        text = str((activity_latest.get("values") or {}).get("exercise_text") or "").strip()
        if text:
            latest_lines.append(f"  描述：{text}")
        for session in (activity_latest.get("training") or [])[:5]:
            parts = [str(session.get("kind") or "运动")]
            if session.get("minutes"):
                parts.append(f"{session['minutes']} 分钟")
            if session.get("calories"):
                parts.append(f"{_fmt(session['calories'], ' kcal', 0)}")
            if session.get("note"):
                parts.append(str(session["note"])[:80])
            latest_lines.append("  · " + "，".join(parts))
    if latest_lines:
        lines.append("- 最新状态（未更新时沿用）：")
        lines += latest_lines

    weights = [(day["date"], (day["sections"].get("body") or {}).get("weight_kg"))
               for day in records]
    weights = [(day, value) for day, value in weights if value is not None]
    if weights:
        first_day, first_value = weights[0]
        last_day, last_value = weights[-1]
        lines.append(f"- 体重：{_fmt(last_value, ' kg')}（{last_day}）；"
                     f"区间首值 {_fmt(first_value, ' kg')}（{first_day}），"
                     f"变化 {_fmt(last_value - first_value, ' kg')}")

    sleeps = [((day["sections"].get("sleep") or {}).get("duration_min"), day["date"])
              for day in records]
    sleeps = [(value, day) for value, day in sleeps if value]
    if sleeps:
        average = sum(value for value, _day in sleeps) / len(sleeps)
        lines.append(f"- 睡眠：平均 {average / 60:.1f} 小时/天（{len(sleeps)} 天有记录）")

    activities = [((day["sections"].get("activity") or {}).get("steps")) for day in records]
    activities = [value for value in activities if value]
    if activities:
        lines.append(f"- 步数：平均 {sum(activities) / len(activities):.0f} 步/天"
                     f"（{len(activities)} 天有记录）")

    calories = [nutrition_totals(day).get("calories") for day in records if _has_nutrition(day)]
    calories = [value for value in calories if value]
    if calories:
        lines.append(f"- 饮食：平均 {sum(calories) / len(calories):.0f} kcal/天"
                     f"（{len(calories)} 天有记录）")

    food_texts = [str((day["sections"].get("nutrition") or {}).get("food_text") or "").strip()
                  for day in records if _has_nutrition(day)]
    food_texts = [text for text in food_texts if text][-7:]
    if food_texts:
        lines.append("- 最近饮食描述：" + "；".join(text[:80] for text in food_texts))

    training = [day for day in records if day.get("training")]
    if training:
        totals = [training_totals(day) for day in training]
        minutes = sum(item["minutes"] for item in totals)
        sessions = sum(int(item["sessions"]) for item in totals)
        training_calories = sum(item["calories"] for item in totals)
        lines.append(f"- 运动：{sessions} 次，共 {minutes:.0f} 分钟，"
                     f"消耗 {training_calories:.0f} kcal（{len(training)} 天有记录）")
        for day in training[-5:]:
            for session in (day.get("training") or [])[:3]:
                parts = [str(session.get("kind") or "运动")]
                if session.get("minutes"):
                    parts.append(f"{session['minutes']} 分钟")
                if session.get("calories"):
                    parts.append(f"{_fmt(session['calories'], ' kcal', 0)}")
                if session.get("note"):
                    parts.append(str(session["note"])[:80])
                lines.append(f"  · {day['date']} " + "，".join(parts))
    facts.update({"weights": len(weights), "sleeps": len(sleeps),
                  "activities": len(activities), "training_days": len(training),
                  "nutrition_days": len(calories)})

    abnormal = [row for row in labs.get("rows") or [] if row.get("abnormal")]
    if abnormal:
        recent = abnormal[-8:]
        lines.append("- 检验异常：" + "、".join(
            f"{row['test']} {row['value']}{row['unit']}（{row['date']}）" for row in recent))
        facts["abnormal_labs"] = len(abnormal)
    elif labs.get("rows"):
        lines.append(f"- 检验：共 {len(labs['rows'])} 项，最近一次没有超出参考区间的记录。")
    for row in (labs.get("rows") or [])[-12:]:
        lines.append(f"  · {row['date']} {row['test']} {row['value']}{row.get('unit', '')}"
                     f"{'（异常）' if row.get('abnormal') else ''}")

    current = health_score.current_score(profile, records, end_day, latest)
    if current.get("total") is not None:
        breakdown = current.get("breakdown") or {}
        lines.append(f"- 当前健康指数：{int(current['total'])}（睡眠 {breakdown.get('sleep', '—')}，"
                     f"身体 {breakdown.get('body', '—')}，饮食 {breakdown.get('nutrition', '—')}，"
                     f"运动 {breakdown.get('movement', '—')}）")
        facts["score"] = current["total"]

    for day in records[-10:]:
        parts = [day["date"]]
        sleep = (day["sections"].get("sleep") or {}).get("duration_min")
        if sleep:
            parts.append(f"睡眠 {sleep}min")
        weight = (day["sections"].get("body") or {}).get("weight_kg")
        if weight:
            parts.append(f"体重 {weight}kg")
        steps = (day["sections"].get("activity") or {}).get("steps")
        if steps:
            parts.append(f"步数 {steps}")
        training_totals_value = training_totals(day)
        if training_totals_value["sessions"]:
            parts.append(f"运动 {int(training_totals_value['sessions'])} 次/")
            parts.append(f"{training_totals_value['minutes']:.0f} 分钟")
        nutrition_value = nutrition_totals(day)
        if nutrition_value.get("calories"):
            parts.append(f"热量 {nutrition_value['calories']:.0f}kcal")
        lines.append("- " + " · ".join(str(part) for part in parts))

    if not records and not labs.get("rows"):
        lines.append("健康系统还没有记录。")
    facts["labs"] = len(labs.get("rows") or [])
    facts["latest"] = {key: value.get("date") for key, value in latest.items()
                       if isinstance(value, dict)}
    return "\n".join(lines) or "健康系统还没有记录。", facts


# ── 应用状态（洞察已读）────────────────────────────────────────────

def state_dir(database: Any) -> Path:
    return Path(database.path).parent / "health"


def read_insight_state(database: Any) -> dict[str, Any]:
    path = state_dir(database) / "insights.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"read": []}
    read = payload.get("read") if isinstance(payload, dict) else []
    return {"read": [str(item) for item in read if str(item).strip()][:500]}


def write_insight_state(database: Any, read: list[str]) -> dict[str, Any]:
    root = state_dir(database)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "insights.json"
    payload = {"schema": 1, "read": [str(item)[:80] for item in read][:500]}
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return payload


__all__ = [
    "HEALTH_ROOT", "DAILY_DIR", "PROFILE_DIR", "PROFILE_REL", "LAB_DIR", "LAB_REL",
    "TRAIN_DIR", "CONSULT_DIR", "REPORT_DIR", "SECTIONS", "MAX_RANGE_DAYS", "DEFAULT_WEIGHTS",
    "DEFAULT_TARGETS", "MEAL_TYPES", "TRAINING_KINDS", "LAB_COLUMNS",
    "validate_day", "today", "shift_day", "day_relpath", "day_path",
    "profile_path", "lab_path", "coerce_value", "clean_section", "empty_day",
    "read_day", "write_day", "patch_day", "add_meal", "delete_meal",
    "replace_meals", "replace_diet", "add_training", "delete_training",
    "replace_training", "replace_exercise", "empty_profile", "read_profile",
    "write_profile", "read_labs", "save_labs", "clean_labs", "list_days",
    "recent_range", "meal_totals", "nutrition_totals", "training_totals",
    "latest_payload", "custom_metric_keys", "render_body", "summary", "state_dir",
    "read_insight_state", "write_insight_state", "health_goal_label",
    "revision",
]
