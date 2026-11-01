"""个人八字系统（2026-11-01）：命盘 profile + AI 解读 + 历史。

数据全部在 ``01_自我档案/03_八字系统/``：

- ``命盘.md``       出生信息 + 手动四柱 + 口径（可在 Obsidian 编辑）；
- ``解读/*.md``     每次 AI 解读一篇 Markdown；
- ``历史/``         删除的解读移到这里，不直接 rm。

排盘由 ``bazi_engine`` 本地计算；AI 只写解读，不得改四柱/十神/藏干/大运。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from . import bazi_engine, vault_layout
from .ai_schemas import extract_json
from .markdown_store import revision as _revision
from .vault import parse_text, safe_note_path
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo("Asia/Shanghai")
PROFILE_TYPE = "bazi_profile"
READING_TYPE = "bazi_reading"
PROFILE_RELATIVE = f"{vault_layout.SELF_BAZI_ROOT}/命盘.md"
TOPICS = ("综合", "性格与天赋", "事业与学业", "财富", "感情", "健康", "大运流年")
MAX_READING_CHARS = 60_000
_SAFE_TOPIC = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f]')


class BaziError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False,
        dir=str(path.parent), prefix=".bazi-", suffix=".tmp",
    )
    try:
        handle.write(text)
        handle.close()
        os.replace(handle.name, path)
    except OSError:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _ensure_write(read_only: bool, action: str) -> None:
    try:
        ensure_vault_writable(read_only, action)
    except VaultReadOnlyError as error:
        raise BaziError(str(error), 409) from error


def _clean(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _profile_path(vault: Path) -> Path:
    return safe_note_path(vault, PROFILE_RELATIVE)


def _empty_profile() -> dict[str, Any]:
    return {
        "exists": False,
        "path": PROFILE_RELATIVE,
        "revision": "",
        "name": "",
        "sex": "",
        "birth_date": "",
        "birth_time": "",
        "birth_place": "",
        "timezone": "Asia/Shanghai",
        "pillars_manual": "",
        "body": "",
        "updated_at": "",
        "created": "",
    }


def read_profile(vault_dir: Path | str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    path = _profile_path(vault)
    if not path.is_file():
        return _empty_profile()
    try:
        raw = path.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError) as error:
        item = _empty_profile()
        item["error"] = str(error)
        return item
    return {
        "exists": True,
        "path": PROFILE_RELATIVE,
        "revision": _revision(raw),
        "name": str(metadata.get("name") or "").strip(),
        "sex": bazi_engine.normalize_sex(metadata.get("sex")),
        "birth_date": str(metadata.get("birth_date") or "").strip(),
        "birth_time": str(metadata.get("birth_time") or "").strip(),
        "birth_place": str(metadata.get("birth_place") or "").strip(),
        "timezone": str(metadata.get("timezone") or "Asia/Shanghai").strip(),
        "pillars_manual": str(metadata.get("pillars_manual") or "").strip(),
        "body": body.strip(),
        "updated_at": str(metadata.get("updated_at") or ""),
        "created": str(metadata.get("created") or ""),
    }


def _normalize_manual(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = re.split(r"[\s,，、]+", text)
    parts = [part for part in parts if part]
    if len(parts) == 1 and len(parts[0]) == 8:
        parts = [parts[0][0:2], parts[0][2:4], parts[0][4:6], parts[0][6:8]]
    if len(parts) != 4 or any(bazi_engine.parse_pillar(part) is None for part in parts):
        raise BaziError("四柱格式应为「癸未 辛酉 丙戌 戊子」这样的四个干支", 422)
    return " ".join(parts)


def chart_from_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    try:
        chart, time_note = bazi_engine.chart_from_birth(
            name=str(profile.get("name") or ""),
            role="命主", sex=str(profile.get("sex") or ""),
            birth_date=str(profile.get("birth_date") or ""),
            birth_time=str(profile.get("birth_time") or ""),
            place=str(profile.get("birth_place") or ""),
            pillars=str(profile.get("pillars_manual") or ""),
        )
    except ValueError as error:
        raise BaziError(str(error), 422) from error
    chart["time_note"] = time_note
    chart["elements"] = bazi_engine.element_counts(chart)
    chart["dayun_cycles"] = bazi_engine.dayun_cycles(chart)
    return chart


def chart_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        chart, time_note = bazi_engine.chart_from_birth(
            name=str(payload.get("name") or ""),
            role="命主", sex=str(payload.get("sex") or ""),
            birth_date=str(payload.get("birth_date") or ""),
            birth_time=str(payload.get("birth_time") or ""),
            place=str(payload.get("birth_place") or ""),
            pillars=str(payload.get("pillars_manual") or ""),
        )
    except ValueError as error:
        raise BaziError(str(error), 422) from error
    chart["time_note"] = time_note
    chart["elements"] = bazi_engine.element_counts(chart)
    chart["dayun_cycles"] = bazi_engine.dayun_cycles(chart)
    return chart


def profile_view(vault_dir: Path | str) -> dict[str, Any]:
    profile = read_profile(vault_dir)
    chart = None
    chart_error = ""
    if profile.get("exists"):
        try:
            chart = chart_from_profile(profile)
        except BaziError as error:
            chart_error = str(error)
    return {**profile, "chart": chart, "chart_error": chart_error}


def write_profile(vault_dir: Path | str, payload: Mapping[str, Any], *,
                  read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "保存个人命盘")
    vault = Path(vault_dir).resolve()
    path = _profile_path(vault)
    current = read_profile(vault)
    expected = str(payload.get("revision") or "").strip()
    if current.get("exists") and expected and expected != current.get("revision"):
        raise BaziError("命盘已在别处修改：请重新读取后再保存", 409)
    name = _clean(payload.get("name"), 60)
    sex = bazi_engine.normalize_sex(payload.get("sex"))
    birth_date = str(payload.get("birth_date") or "").strip()
    birth_time = str(payload.get("birth_time") or "").strip()
    if birth_date:
        try:
            datetime.strptime(birth_date, "%Y-%m-%d")
        except ValueError as error:
            raise BaziError("出生日期格式应为 YYYY-MM-DD", 422) from error
    if birth_time:
        try:
            datetime.strptime(birth_time, "%H:%M")
        except ValueError as error:
            raise BaziError("出生时间格式应为 HH:MM", 422) from error
    pillars_manual = _normalize_manual(payload.get("pillars_manual"))
    if not birth_date and not pillars_manual:
        raise BaziError("请填写出生日期，或直接填写完整四柱", 422)
    if not name:
        raise BaziError("先填写称呼", 422)
    body = str(current.get("body") or "").strip() or (
        "# 我的命盘\n\n> 这里可以写自己的理解；AI 不会修改正文。")
    metadata = {
        "type": PROFILE_TYPE,
        "name": name,
        "sex": sex,
        "birth_date": birth_date,
        "birth_time": birth_time,
        "birth_place": _clean(payload.get("birth_place"), 120),
        "timezone": str(payload.get("timezone") or "Asia/Shanghai").strip()[:64],
        "pillars_manual": pillars_manual,
        "created": current.get("created") or _now(),
        "updated": _now(),
        "updated_at": _now(),
    }
    text = ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
            + "---\n\n" + body.strip() + "\n")
    _atomic_write(path, text)
    return profile_view(vault)


def _reading_dir(vault: Path) -> Path:
    return safe_note_path(vault, vault_layout.SELF_BAZI_READINGS_DIR)


def _safe_reading_name(name: str) -> str:
    text = str(name or "").strip()
    if not text.endswith(".md") or Path(text).name != text or text in {".", ".."}:
        raise BaziError("解读文件名不合法", 422)
    return text


def _read_reading(vault: Path, path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
        metadata, body = parse_text(raw.decode("utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return {
        "name": path.name,
        "path": path.resolve().relative_to(vault.resolve()).as_posix(),
        "title": str(metadata.get("title") or path.stem),
        "topic": str(metadata.get("topic") or "综合"),
        "date": str(metadata.get("date") or metadata.get("created") or ""),
        "summary": str(metadata.get("summary") or ""),
        "sources": [str(item) for item in (metadata.get("sources") or [])][:20],
        "chart_revision": str(metadata.get("chart_revision") or ""),
        "body": body.strip(),
    }


def list_readings(vault_dir: Path | str) -> list[dict[str, Any]]:
    vault = Path(vault_dir).resolve()
    directory = _reading_dir(vault)
    if not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        item = _read_reading(vault, path)
        if item is not None:
            rows.append(item)
    rows.sort(key=lambda item: (item.get("date") or "", item.get("name") or ""), reverse=True)
    return rows


def read_reading(vault_dir: Path | str, name: str) -> dict[str, Any]:
    vault = Path(vault_dir).resolve()
    safe_name = _safe_reading_name(name)
    path = safe_note_path(vault, f"{vault_layout.SELF_BAZI_READINGS_DIR}/{safe_name}")
    if not path.is_file():
        raise BaziError("这篇解读不存在", 404)
    item = _read_reading(vault, path)
    if item is None:
        raise BaziError("这篇解读读不出来", 409)
    return item


def _topic_slug(topic: str) -> str:
    text = _SAFE_TOPIC.sub("-", str(topic or "综合")).strip(" .-_")
    return text[:40] or "综合"


def _reading_body(result: Mapping[str, Any]) -> str:
    lines = []
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.extend([f"> {summary}", ""])
    sections = result.get("sections") if isinstance(result.get("sections"), list) else []
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        body = str(section.get("body") or "").strip()
        if title:
            lines.extend([f"## {title}", "", body, ""])
    years = result.get("years") if isinstance(result.get("years"), list) else []
    if years:
        lines.extend(["## 流年", ""])
        for row in years:
            if isinstance(row, dict):
                lines.append(f"- **{row.get('year', '')} {row.get('gz', '')}**：{row.get('text', '')}")
        lines.append("")
    advice = result.get("advice") if isinstance(result.get("advice"), list) else []
    if advice:
        lines.extend(["## 建议", ""])
        for row in advice:
            if isinstance(row, dict):
                lines.append(f"- **{row.get('title', '')}**：{row.get('body', '')}")
        lines.append("")
    boundary = str(result.get("boundary") or "").strip()
    if boundary:
        lines.extend(["> " + boundary, ""])
    return "\n".join(lines).strip()


def write_reading(vault_dir: Path | str, *, topic: str, result: Mapping[str, Any],
                  chart: Mapping[str, Any], sources: Any = None,
                  read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "保存八字解读")
    vault = Path(vault_dir).resolve()
    topic = str(topic or "综合").strip() or "综合"
    if topic not in TOPICS:
        topic = "综合"
    title = _clean(result.get("title"), 120) or f"{topic} · 我的八字"
    summary = _clean(result.get("summary"), 1_200)
    body = _reading_body(result)
    if not body:
        raise BaziError("解读正文为空", 422)
    if len(body) > MAX_READING_CHARS:
        body = body[: MAX_READING_CHARS - 1].rstrip() + "…"
    metadata = {
        "type": READING_TYPE,
        "title": title,
        "topic": topic,
        "summary": summary,
        "date": _now()[:10],
        "created": _now(),
        "chart_revision": _revision(
            json.dumps(chart, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        "sources": [str(item) for item in (sources or [])][:20],
        "boundary": "八字是传统文化参考，不替代医疗、投资或婚姻决策。",
    }
    directory = _reading_dir(vault)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{_stamp()}-{_topic_slug(topic)}"
    path = directory / f"{stem}.md"
    index = 2
    while path.exists():
        path = directory / f"{stem}-{index}.md"
        index += 1
    text = ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
            + "---\n\n" + body + "\n")
    _atomic_write(path, text)
    item = _read_reading(vault, path)
    if item is None:
        raise BaziError("解读写入后无法读回", 500)
    return item


def delete_reading(vault_dir: Path | str, name: str, *, read_only: bool = False) -> dict[str, Any]:
    _ensure_write(read_only, "删除八字解读")
    vault = Path(vault_dir).resolve()
    safe_name = _safe_reading_name(name)
    path = safe_note_path(vault, f"{vault_layout.SELF_BAZI_READINGS_DIR}/{safe_name}")
    if not path.is_file():
        raise BaziError("这篇解读不存在", 404)
    target_dir = safe_note_path(vault, f"{vault_layout.SELF_BAZI_HISTORY_DIR}/已删除")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_stamp()}-{safe_name}"
    index = 2
    while target.exists():
        target = target_dir / f"{_stamp()}-{index}-{safe_name}"
        index += 1
    os.replace(path, target)
    return {"deleted": True, "name": safe_name,
            "archived_as": target.resolve().relative_to(vault.resolve()).as_posix()}


def chart_context(vault_dir: Path | str) -> str:
    profile = read_profile(vault_dir)
    if not profile.get("exists"):
        return "还没有命盘。"
    try:
        chart = chart_from_profile(profile)
    except BaziError as error:
        return f"命盘读不出来：{error}"
    lines = [
        f"姓名：{profile.get('name') or '未填'}",
        f"性别：{profile.get('sex') or '未填'}",
        f"出生：{chart.get('birth') or '未填'} {chart.get('place') or ''}".strip(),
        f"四柱：{' '.join(row['gan'] + row['zhi'] for row in chart.get('pillars') or [])}",
        f"日主：{chart.get('day_master') or ''}",
    ]
    if chart.get("dayun"):
        lines.append(f"大运：{chart.get('dayun')}")
    if chart.get("time_note"):
        lines.append(f"口径：{chart.get('time_note')}")
    return "\n".join(lines)


def generate_reading(role_service: Any, vault_dir: Path | str, *, topic: str = "综合",
                     read_only: bool = False) -> dict[str, Any]:
    if role_service is None:
        raise BaziError("AI 不可用：请先在 dsh 里配置模型", 503)
    vault = Path(vault_dir).resolve()
    profile = read_profile(vault)
    if not profile.get("exists"):
        raise BaziError("先保存你的命盘，再生成解读", 422)
    chart = chart_from_profile(profile)
    topic = str(topic or "综合").strip()
    if topic not in TOPICS:
        topic = "综合"
    prompt = json.dumps({
        "task": "个人八字解读",
        "topic": topic,
        "命盘": chart,
        "输出要求": (
            "只输出一个 JSON 对象，不要 Markdown 代码块。结构："
            "{title,summary,sections:[{title,body}],favor:[],avoid:[],"
            "years:[{year,gz,text,tone:'up'|'down'|'flat'}],"
            "advice:[{title,body}],boundary}。"
            "不要改四柱、日主、十神、藏干和大运的本地计算结果；"
            "结合档案与核心记忆做个性化解读，但明确区分命理推断与现实证据；"
            "不宿命论、不预测灾祸、不做医疗/投资/婚姻决策。"
        ),
    }, ensure_ascii=False)
    try:
        result = role_service.run_once(
            "mentor", "bazi", prompt, entry="self.bazi",
            selectors=[
                {"entry": "bazi_chart"},
                {"entry": "self_archive"},
                {"entry": "mentor_memory"},
            ],
            max_chars=40_000,
        )
    except Exception as error:  # noqa: BLE001
        raise BaziError(f"生成八字解读失败：{error}", 502) from error
    data = result.get("structured")
    if not isinstance(data, dict):
        data = extract_json(result.get("text") or "") or {}
    if not data:
        raise BaziError("模型没有返回可用的解读；没有写入任何文件", 502)
    # 只用 AI 的解读字段；命盘字段始终来自本地。
    if not data.get("favor"):
        data["favor"] = chart.get("favor") or []
    if not data.get("avoid"):
        data["avoid"] = chart.get("avoid") or []
    item = write_reading(
        vault, topic=topic, result=data, chart=chart,
        sources=result.get("sources") or [], read_only=read_only)
    return {**item, "chart": chart, "topic": topic,
            "sources": result.get("sources") or [],
            "truncated": bool(result.get("truncated"))}


def overview(vault_dir: Path | str) -> dict[str, Any]:
    profile = profile_view(vault_dir)
    readings = list_readings(vault_dir)
    return {
        "profile": profile,
        "chart": profile.get("chart"),
        "readings": readings,
        "topics": list(TOPICS),
        "readings_dir": vault_layout.SELF_BAZI_READINGS_DIR,
    }


__all__ = [
    "BaziError", "TOPICS", "read_profile", "profile_view", "write_profile",
    "chart_from_profile", "chart_from_payload", "list_readings", "read_reading", "write_reading",
    "delete_reading", "chart_context", "generate_reading", "overview",
]
