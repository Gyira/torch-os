"""滴答习惯 ↔ 本地习惯的纯映射函数。

本模块不读写文件、不访问数据库、不发网络请求；只负责：

- RRULE 与本地 frequency 的互转；
- 滴答习惯/打卡字段的清洗与映射；
- 只比较“映射字段”的基线哈希；
- 构造回写滴答的 payload（保留未映射字段）。

冲突判定不能用本地 revision：本地改备注、图标、分组、优先级同样会让
revision 增加，但那些字段不会上传滴答。这里统一用映射字段快照。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from . import habit_schedule
from .dida_client import RemoteHabit, RemoteHabitCheckin

MATERIAL_KEYS = (
    "title", "color", "frequency", "start_date", "target_days", "reminder", "status",
)
REMOTE_CHECKIN_STATUS = {"done": 2, "failed": 1}
LOCAL_CHECKIN_STATUS = {2: "done", 1: "failed"}
ABSENT_CHECKIN_HASH = hashlib.sha256(b"habit-checkin-absent").hexdigest()
DEFAULT_COLOR = "#97E38B"
DEFAULT_SORT_ORDER = -1099511627776
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_RRULE_EXTRA_KEYS = {"COUNT", "UNTIL", "TT_TIMES", "BYMONTH", "BYSETPOS", "BYHOUR", "BYMINUTE"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def clean_title(value: Any) -> str:
    text = unicodedata.normalize("NFC", _text(value))
    text = _CONTROL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def canonical_title(value: Any) -> str:
    return clean_title(value).casefold()


def normalize_color(value: Any) -> str:
    text = _text(value).strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return f"#{text.lower()}"
    if re.fullmatch(r"#[0-9a-fA-F]{8}", text):
        return text[:7].lower()
    return ""


def normalize_reminder(value: Any) -> str:
    text = _text(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return ""


def parse_reminders(values: Iterable[Any]) -> tuple[str, tuple[str, ...]]:
    """只映射第一个合法提醒；多余的保留在远端 raw 里，不删除。"""
    valid = [item for item in (normalize_reminder(value) for value in values or ()) if item]
    if not valid:
        return "", ()
    warnings = ("habit_reminder_extra",) if len(valid) > 1 else ()
    return valid[0], warnings


def parse_date(value: Any) -> str:
    """接受 YYYYMMDD、ISO date 或 ISO datetime，返回 YYYY-MM-DD。"""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        stamp = int(value)
        text = str(stamp)
    else:
        text = _text(value).strip()
        if not text:
            return ""
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?", text):
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return ""
    return ""


def parse_checkin_stamp(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed or None


def checkin_stamp(day: str) -> int:
    parsed = parse_date(day)
    if not parsed:
        return 0
    return int(parsed.replace("-", ""))


def _start_weekday(start_date: str) -> int | None:
    parsed = parse_date(start_date)
    if not parsed:
        return None
    return date.fromisoformat(parsed).weekday()


def parse_repeat_rule(rule: Any, *, start_date: str = "") -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """把滴答 RRULE 转成本地 frequency。

    返回 ``(frequency, warnings)``；``frequency`` 为 ``None`` 表示无法安全
    映射，调用方应保留本地频率，不要覆盖。空规则同样返回 ``None``（视为
    远端未提供）。
    """
    text = _text(rule).strip()
    if not text:
        return None, ()
    text = re.sub(r"^RRULE:", "", text, flags=re.IGNORECASE)
    params: dict[str, str] = {}
    warnings: list[str] = []
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        params[key.strip().upper()] = value.strip()
    freq = params.get("FREQ", "").upper()
    if not freq:
        return None, ("habit_rrule_unsupported",)
    extras = sorted(key for key in params if key in _RRULE_EXTRA_KEYS)
    if extras:
        warnings.append("habit_rrule_extras_dropped")
    if "TT_TIMES" in params:
        # 本地没有“每周 N 次”的表达，按开始日 weekday 会改变语义。
        return None, tuple(warnings + ["habit_rrule_unsupported"])
    try:
        interval = int(params.get("INTERVAL", "1"))
    except ValueError:
        interval = 1
    interval = max(1, min(interval, 365))
    if freq == "DAILY":
        if interval > 1:
            return {"kind": "interval", "interval_days": interval}, tuple(warnings)
        return {"kind": "daily"}, tuple(warnings)
    if freq == "WEEKLY":
        byday = params.get("BYDAY", "")
        weekdays: list[int] = []
        if byday:
            for token in byday.upper().split(","):
                token = re.sub(r"^[+-]?\d+", "", token.strip())
                if token in _WEEKDAYS:
                    weekdays.append(_WEEKDAYS.index(token))
        if not weekdays:
            weekday = _start_weekday(start_date)
            if weekday is None:
                return None, tuple(warnings + ["habit_rrule_unsupported"])
            weekdays = [weekday]
        weekdays = sorted(set(weekdays))
        if len(weekdays) == 7:
            return {"kind": "daily"}, tuple(warnings)
        if interval > 1:
            # 本地 weekly 没有“每 N 周”的表达；基础规则仍可展示，但语义会变。
            warnings.append("habit_rrule_interval_dropped")
        return {"kind": "weekly", "weekdays": weekdays}, tuple(warnings)
    if freq == "MONTHLY":
        bymonthday = params.get("BYMONTHDAY", "")
        month_days: list[int] = []
        if bymonthday:
            for token in bymonthday.split(","):
                token = token.strip()
                if not re.fullmatch(r"\d{1,2}", token):
                    continue
                value = int(token)
                if 1 <= value <= 31:
                    month_days.append(value)
        if not month_days:
            weekday = _start_weekday(start_date)
            parsed = parse_date(start_date)
            if parsed:
                month_days = [date.fromisoformat(parsed).day]
            del weekday
        if not month_days:
            return None, tuple(warnings + ["habit_rrule_unsupported"])
        if interval > 1:
            warnings.append("habit_rrule_interval_dropped")
        return {"kind": "monthly", "month_days": sorted(set(month_days))}, tuple(warnings)
    return None, tuple(warnings + ["habit_rrule_unsupported"])


def build_repeat_rule(frequency: Any) -> str:
    try:
        normalized = habit_schedule.normalize_frequency(frequency)
    except (TypeError, ValueError):
        normalized = {"kind": "daily"}
    kind = str(normalized.get("kind") or "daily")
    if kind == "weekly":
        days = normalized.get("weekdays") or list(range(7))
        byday = ",".join(_WEEKDAYS[int(day)] for day in days if 0 <= int(day) <= 6)
        return f"RRULE:FREQ=WEEKLY;BYDAY={byday or 'MO'}"
    if kind == "monthly":
        days = normalized.get("month_days") or [1]
        return "RRULE:FREQ=MONTHLY;BYMONTHDAY=" + ",".join(str(int(day)) for day in days)
    if kind == "interval":
        interval = max(1, min(int(normalized.get("interval_days") or 1), 365))
        return f"RRULE:FREQ=DAILY;INTERVAL={interval}"
    return "RRULE:FREQ=DAILY;INTERVAL=1"


def _target_days(value: Any) -> tuple[int | None, tuple[str, ...]]:
    if value is None or value == "":
        return None, ()
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, ("habit_target_days_invalid",)
    if number <= 0:
        return None, ()
    if number > 3650:
        return 3650, ("habit_target_days_clamped",)
    return number, ()


def _status(value: Any) -> str:
    try:
        return "archived" if int(value) == 2 else "active"
    except (TypeError, ValueError):
        return "active"


def local_frequency(habit: dict[str, Any]) -> dict[str, Any]:
    try:
        return habit_schedule.normalize_frequency(habit.get("frequency"))
    except (TypeError, ValueError):
        return {"kind": "daily"}


def local_habit_material(habit: dict[str, Any]) -> dict[str, Any]:
    target, _warnings = _target_days(habit.get("target_days"))
    return {
        "title": clean_title(habit.get("title")),
        "color": normalize_color(habit.get("color")),
        "frequency": local_frequency(habit),
        "start_date": parse_date(habit.get("start_date")),
        "target_days": target,
        "reminder": normalize_reminder(habit.get("reminder")),
        "status": "archived" if str(habit.get("status") or "") == "archived" else "active",
    }


def first_link_matches(local: dict[str, Any], remote: dict[str, Any],
                       present_keys: Iterable[str]) -> bool:
    """首次配对时判断本地和远端是否可视为同一个习惯。

    本地为空/默认的字段（颜色、提醒、坚持天数、开始日期）不参与冲突：
    滴答习惯总会带颜色和开始日期，本地习惯默认没有颜色/提醒/目标。若这些
    也算冲突，几乎所有首次配对都会进冲突列表。
    """
    for key in present_keys:
        if key == "start_date":
            continue
        local_value = local.get(key)
        if key == "color" and not local_value:
            continue
        if key == "reminder" and not local_value:
            continue
        if key == "target_days" and local_value is None:
            continue
        if local_value != remote.get(key):
            return False
    return True


def material_hash(material: dict[str, Any]) -> str:
    text = json.dumps(material, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def local_material_json(habit: dict[str, Any]) -> str:
    return json.dumps(local_habit_material(habit), ensure_ascii=False, sort_keys=True)


def material_from_json(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return dict(text)
    raw = _text(text).strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def material_diff(current: dict[str, Any], baseline: dict[str, Any],
                  keys: Iterable[str] | None = None) -> dict[str, Any]:
    """返回 current 与 baseline 在指定键上不同的值。

    baseline 为空（首次建立链接、旧数据）时，把当前所有键都视为待同步。
    """
    selected = list(keys) if keys is not None else list(MATERIAL_KEYS)
    if not baseline:
        return {key: current.get(key) for key in selected if key in current}
    changed: dict[str, Any] = {}
    for key in selected:
        if key not in current:
            continue
        if key not in baseline or current.get(key) != baseline.get(key):
            changed[key] = current.get(key)
    return changed


@dataclass(frozen=True)
class MappedHabit:
    dida_id: str
    title: str
    values: dict[str, Any]
    present_keys: frozenset[str]
    remote_material: dict[str, Any]
    remote_hash: str
    etag: str
    modified: str
    remote_raw: dict[str, Any]
    warnings: tuple[str, ...]

    def material(self) -> dict[str, Any]:
        return dict(self.remote_material)


def map_remote_habit(remote: RemoteHabit, *, fallback_start: str = "") -> MappedHabit:
    warnings: list[str] = []
    title = clean_title(remote.name)
    if not title:
        title = "未命名习惯"
        warnings.append("habit_invalid_title")
    start_date = parse_date(remote.target_start_date) or parse_date(fallback_start)
    frequency, frequency_warnings = parse_repeat_rule(
        remote.repeat_rule, start_date=start_date)
    warnings.extend(frequency_warnings)
    if frequency is None:
        frequency = {"kind": "daily"}
        if _text(remote.repeat_rule).strip():
            warnings.append("habit_rrule_unsupported")
    if not start_date:
        start_date = parse_date(fallback_start)
    reminder, reminder_warnings = parse_reminders(remote.reminders)
    warnings.extend(reminder_warnings)
    target_days, target_warnings = _target_days(remote.target_days)
    warnings.extend(target_warnings)
    if remote.habit_type and remote.habit_type != "Boolean":
        warnings.append("habit_real_flattened")
    color = normalize_color(remote.color)
    status = _status(remote.status)
    present: dict[str, Any] = {"title": title, "status": status}
    if color:
        present["color"] = color
    if start_date:
        present["start_date"] = start_date
    if _text(remote.repeat_rule).strip() and frequency is not None:
        present["frequency"] = frequency
    if remote.target_days is not None:
        present["target_days"] = target_days
    if reminder:
        present["reminder"] = reminder
    values = {
        "title": title,
        "color": color or DEFAULT_COLOR,
        "frequency": frequency,
        "start_date": start_date,
        "target_days": target_days,
        "reminder": reminder,
        "status": status,
    }
    return MappedHabit(
        dida_id=remote.id,
        title=title,
        values=values,
        present_keys=frozenset(present),
        remote_material=present,
        remote_hash=material_hash(present),
        etag=remote.etag,
        modified=remote.modified_time,
        remote_raw=dict(remote.raw or {}),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def remote_habit_hash(remote_or_material: RemoteHabit | MappedHabit | dict[str, Any]) -> str:
    if isinstance(remote_or_material, MappedHabit):
        return remote_or_material.remote_hash
    if isinstance(remote_or_material, RemoteHabit):
        return map_remote_habit(remote_or_material).remote_hash
    return material_hash(remote_or_material)


def remote_checkin_hash(checkin: RemoteHabitCheckin | dict[str, Any] | None) -> str:
    if checkin is None:
        return ABSENT_CHECKIN_HASH
    if isinstance(checkin, dict):
        status = checkin.get("status")
    else:
        status = checkin.status
    try:
        value = int(status)
    except (TypeError, ValueError):
        value = 0
    return hashlib.sha256(f"habit-checkin:{value}".encode("utf-8")).hexdigest()


def _now_text(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _stamp_or_zero(day: Any) -> int:
    parsed = parse_date(day)
    return int(parsed.replace("-", "")) if parsed else 0


def build_remote_habit_payload(habit: dict[str, Any], *, habit_id: str,
                               base: dict[str, Any] | None = None,
                               changed_keys: Iterable[str] | None = None,
                               now: datetime | None = None) -> dict[str, Any]:
    """构造 habits/batch 的 add/update payload。

    - ``base`` 为 None 时是新建，写全部映射字段并用二值默认值；
    - 传入 base 时只覆盖 ``changed_keys``（None 表示全部），其余远端字段
      （type/goal/step/unit/iconRes/sectionId/统计等）原样保留；
    - 本地专属字段永远不会出现在 payload 里。
    """
    material = local_habit_material(habit)
    now_text = _now_text(now)
    keys = set(changed_keys) if changed_keys is not None else set(MATERIAL_KEYS)
    if base is None:
        payload: dict[str, Any] = {
            "id": habit_id,
            "name": material["title"] or "未命名习惯",
            "color": material["color"] or DEFAULT_COLOR,
            "iconRes": "habit_daily_check_in",
            "type": "Boolean",
            "goal": 1,
            "step": 0,
            "unit": "Count",
            "status": 2 if material["status"] == "archived" else 0,
            "totalCheckIns": 0,
            "currentStreak": 0,
            "completedCycles": 0,
            "createdTime": now_text,
            "modifiedTime": now_text,
            "encouragement": "",
            "recordEnable": False,
            "exDates": [],
            "style": 1,
            "etag": "",
            "repeatRule": build_repeat_rule(material["frequency"]),
            "reminders": [material["reminder"]] if material["reminder"] else [],
            "targetDays": material["target_days"] or 0,
            "sortOrder": DEFAULT_SORT_ORDER,
        }
        if material["start_date"]:
            payload["targetStartDate"] = _stamp_or_zero(material["start_date"])
        return payload
    payload = dict(base)
    payload["id"] = habit_id
    if "title" in keys:
        payload["name"] = material["title"] or "未命名习惯"
    if "color" in keys:
        payload["color"] = material["color"] or normalize_color(payload.get("color")) or DEFAULT_COLOR
    if "frequency" in keys:
        payload["repeatRule"] = build_repeat_rule(material["frequency"])
    if "reminders" in keys or "reminder" in keys:
        payload["reminders"] = [material["reminder"]] if material["reminder"] else []
    if "target_days" in keys:
        payload["targetDays"] = material["target_days"] or 0
    if "start_date" in keys and material["start_date"]:
        payload["targetStartDate"] = _stamp_or_zero(material["start_date"])
    if "status" in keys:
        payload["status"] = 2 if material["status"] == "archived" else 0
    payload["modifiedTime"] = now_text
    return payload


def build_remote_checkin_payload(entry: dict[str, Any], *, checkin_id: str,
                                 habit_id: str, date_text: str,
                                 base: dict[str, Any] | None = None,
                                 now: datetime | None = None) -> dict[str, Any]:
    """构造 habitCheckins/batch 的 add/update payload。

    本地是二值习惯：完成写 status=2/value=goal，失败写 status=1/value=0。
    ``skipped`` 是本地专属的主动休息，不映射到滴答，调用方不应传入。
    """
    now_text = _now_text(now)
    status = str(entry.get("status") or "")
    if status not in REMOTE_CHECKIN_STATUS:
        raise ValueError("只有 done/failed 能写入滴答打卡")
    try:
        goal = int((base or {}).get("goal") or 1)
    except (TypeError, ValueError):
        goal = 1
    payload = dict(base or {})
    payload.update({
        "id": checkin_id,
        "habitId": habit_id,
        "checkinStamp": checkin_stamp(date_text),
        "goal": goal,
        "value": goal if status == "done" else 0,
        "status": REMOTE_CHECKIN_STATUS.get(status, 1),
        "opTime": now_text,
    })
    if status == "done":
        payload["checkinTime"] = now_text
    else:
        payload.pop("checkinTime", None)
    return payload


__all__ = [
    "ABSENT_CHECKIN_HASH", "LOCAL_CHECKIN_STATUS", "MATERIAL_KEYS", "MappedHabit",
    "REMOTE_CHECKIN_STATUS", "build_remote_checkin_payload", "build_remote_habit_payload",
    "build_repeat_rule", "canonical_title", "checkin_stamp", "clean_title",
    "first_link_matches", "local_habit_material", "local_material_json",
    "map_remote_habit", "material_diff",
    "material_from_json", "material_hash", "normalize_color", "normalize_reminder",
    "parse_checkin_stamp", "parse_date", "parse_reminders", "parse_repeat_rule",
    "remote_checkin_hash", "remote_habit_hash",
]
