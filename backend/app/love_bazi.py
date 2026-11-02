"""八字合婚（2026-11-01）：本地排盘 + dsh 生成合婚分析 + 历史页面。

- 本地用 ``lunar_python`` 计算四柱、十神、藏干、大运；用户也可以直接填四柱。
- AI 只负责格局/喜忌/合婚分析，避免模型编造命盘。
- 每次生成存 ``06_八字合婚/bazi.db``；历史页面只读、可切换、删除、导出 HTML。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import bazi_engine
from .bazi_engine import (
    GAN, GAN_ELEM, GAN_YANG, GEN, KE, SLOTS, ZHI, ZHI_ELEM, ZHI_HIDDEN,
    chart_from_birth, default_pillars as _default_pillars,
    normalize_pillars as _normalize_pillars, parse_pillar as _parse_pillar,
    pillar as _pillar, ten_god as _ten_god,
)
from .love_layout import LovePaths
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

DAY = ZoneInfo("Asia/Shanghai")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    input_json TEXT NOT NULL,
    chart_json TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    engine TEXT NOT NULL DEFAULT 'lunar_python+dsh',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(DAY).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def ensure_bazi_schema(paths: LovePaths) -> None:
    with _connect(paths.bazi_db) as connection:
        connection.executescript(SCHEMA_SQL)


def _person_from_birth(payload: "PersonPayload", role: str) -> tuple[dict[str, Any], str]:
    """返回 (人员 chart, 提示)。优先用用户填的四柱；缺失时用 lunar_python 排盘。"""
    try:
        return chart_from_birth(
            name=payload.name, role=role, sex="male" if role == "男命" else "female",
            birth_date=payload.birth_date, birth_time=payload.birth_time,
            place=payload.place, pillars=payload.pillars,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


class PersonPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)
    birth_date: str = Field(default="", max_length=10)
    birth_time: str = Field(default="", max_length=5)
    place: str = Field(default="", max_length=120)
    pillars: Any = Field(default_factory=_default_pillars)

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, value: str) -> str:
        if not value:
            return ""
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("出生日期格式应为 YYYY-MM-DD") from error
        return value

    @field_validator("birth_time")
    @classmethod
    def validate_birth_time(cls, value: str) -> str:
        if not value:
            return ""
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as error:
            raise ValueError("出生时间格式应为 HH:MM") from error
        return value


class GeneratePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    him: PersonPayload
    her: PersonPayload
    title: str = Field(default="", max_length=120)


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _texts(value: Any, *, limit: int = 8, chars: int = 200) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = _text(item, chars)
        if text:
            result.append(text)
    return result


def _points(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, str]] = []
    for item in value[:6]:
        if not isinstance(item, dict):
            continue
        result.append({
            "glyph": _text(item.get("glyph"), 8) or "☯",
            "title": _text(item.get("title"), 80),
            "body": _text(item.get("body"), 1200),
        })
    return result


def _years(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, str]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        tone = _text(item.get("tone"), 20).lower()
        result.append({
            "year": _text(item.get("year"), 20),
            "gz": _text(item.get("gz"), 20),
            "text": _text(item.get("text"), 800),
            "tone": tone if tone in {"up", "down", "flat"} else "flat",
        })
    return result


def _advice(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, str]] = []
    for item in value[:5]:
        if isinstance(item, dict):
            result.append({
                "title": _text(item.get("title"), 80),
                "body": _text(item.get("body"), 1000),
            })
        elif isinstance(item, str) and item.strip():
            result.append({"title": "建议", "body": _text(item, 1000)})
    return result


def _merge_person(base: dict[str, Any], extra: Any) -> dict[str, Any]:
    person = dict(base)
    if not isinstance(extra, dict):
        return person
    for key in ("pattern",):
        if extra.get(key):
            person[key] = _text(extra.get(key), 200)
    for key in ("favor", "avoid"):
        values = _texts(extra.get(key), limit=8, chars=40)
        if values:
            person[key] = values
    if extra.get("dayun"):
        person["dayun"] = _text(extra.get("dayun"), 800)
    if extra.get("time_note"):
        person["time_note"] = _text(extra.get("time_note"), 300)
    sections = extra.get("sections")
    if isinstance(sections, list):
        person["sections"] = [
            {"title": _text(item.get("title"), 80), "body": _text(item.get("body"), 2000)}
            for item in sections[:8] if isinstance(item, dict)
        ]
    return person


def _normalize_ai(ai: Any, chart: dict[str, Any]) -> dict[str, Any]:
    data = ai if isinstance(ai, dict) else {}
    hehun = data.get("hehun") if isinstance(data.get("hehun"), dict) else data
    him = _merge_person(chart["him"], data.get("him"))
    her = _merge_person(chart["her"], data.get("her"))
    result = {
        "version": 1,
        "him": him,
        "her": her,
        "hehun": {
            "kicker": _text(hehun.get("kicker"), 40) or "合婚参考",
            "summary": _text(hehun.get("summary"), 1200),
            "verdict": _text(hehun.get("verdict"), 40) or "参考",
            "score": _text(hehun.get("score"), 40),
            "points": _points(hehun.get("points")),
            "review": _text(hehun.get("review"), 1200),
            "years": _years(hehun.get("years")),
            "advice": _advice(hehun.get("advice")),
            "boundary": _text(hehun.get("boundary"), 600)
            or "八字合婚是传统文化参考，不替代现实沟通，也不构成任何关系决策。",
        },
    }
    return result


def _chart_payload(him: PersonPayload, her: PersonPayload) -> tuple[dict[str, Any], dict[str, Any]]:
    him_chart, him_note = _person_from_birth(him, "男命")
    her_chart, her_note = _person_from_birth(her, "女命")
    return {"him": him_chart, "her": her_chart}, {"him": him_note, "her": her_note}


def _report_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        result = json.loads(row["result_json"] or "{}")
    except ValueError:
        result = {}
    try:
        chart = json.loads(row["chart_json"] or "{}")
    except ValueError:
        chart = {}
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "status": row["status"],
        "engine": row["engine"],
        "error": row["error"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "chart": chart,
        "result": result,
    }


def _history_item(row: sqlite3.Row) -> dict[str, Any]:
    try:
        chart = json.loads(row["chart_json"] or "{}")
    except ValueError:
        chart = {}
    try:
        result = json.loads(row["result_json"] or "{}")
    except ValueError:
        result = {}
    hehun = result.get("hehun") if isinstance(result.get("hehun"), dict) else {}
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "status": row["status"],
        "created_at": row["created_at"],
        "him": (chart.get("him") or {}).get("name", ""),
        "her": (chart.get("her") or {}).get("name", ""),
        "verdict": hehun.get("verdict", ""),
        "score": hehun.get("score", ""),
        "error": row["error"],
    }


def _report_html(report: dict[str, Any]) -> str:
    from html import escape
    data = report.get("result") or {}
    hehun = data.get("hehun") or {}
    him = data.get("him") or {}
    her = data.get("her") or {}

    def person_html(person: dict[str, Any], role: str) -> str:
        pillars = "".join(
            f"<div class='p'><small>{escape(str(item.get('slot') or ''))}</small>"
            f"<b>{escape(str(item.get('gan') or ''))}{escape(str(item.get('zhi') or ''))}</b>"
            f"<span>{escape(str(item.get('gan_ss') or ''))} / {escape(str(item.get('zhi_ss') or ''))}</span></div>"
            for item in (person.get("pillars") or []) if isinstance(item, dict)
        )
        return (
            f"<section class='person'><h2>{escape(str(person.get('name') or role))}</h2>"
            f"<p>{escape(str(person.get('birth') or ''))} {escape(str(person.get('place') or ''))}</p>"
            f"<div class='pillars'>{pillars}</div>"
            f"<p>日主：{escape(str(person.get('day_master') or ''))} · 格局：{escape(str(person.get('pattern') or ''))}</p>"
            f"<p>喜：{escape('、'.join(person.get('favor') or []))} 忌：{escape('、'.join(person.get('avoid') or []))}</p>"
            f"<p>{escape(str(person.get('dayun') or ''))}</p></section>"
        )

    points = "".join(
        f"<li><b>{escape(str(item.get('title') or ''))}</b><p>{escape(str(item.get('body') or ''))}</p></li>"
        for item in (hehun.get("points") or []) if isinstance(item, dict)
    )
    advice = "".join(
        f"<li><b>{escape(str(item.get('title') or ''))}</b><p>{escape(str(item.get('body') or ''))}</p></li>"
        for item in (hehun.get("advice") or []) if isinstance(item, dict)
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{escape(report.get('title') or '八字合婚')}</title>
<style>
body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.75;color:#2c2621;background:#fbf8f3}}
h1{{font-size:26px}}h2{{font-size:18px;margin-top:28px}}small{{color:#8a7f73}}
.hero{{background:#f3e7d8;border:1px solid #e2cfb8;border-radius:16px;padding:22px}}
.pillars{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}}
.p{{border:1px solid #ddd0c0;border-radius:10px;padding:10px 12px;min-width:84px;background:#fff}}
.p b{{display:block;font-size:22px;letter-spacing:2px}}.p span{{font-size:11px;color:#8a7f73}}
.person{{border-top:1px solid #e4d8c8;padding-top:18px;margin-top:24px}}
ul{{padding-left:20px}}li{{margin:10px 0}}p{{margin:6px 0}}
</style></head><body>
<h1>{escape(report.get('title') or '八字合婚')}</h1>
<div class="hero"><h2>{escape(str(hehun.get('kicker') or '合婚'))}</h2>
<p>{escape(str(hehun.get('summary') or ''))}</p>
<p><b>{escape(str(hehun.get('verdict') or ''))}</b> {escape(str(hehun.get('score') or ''))}</p></div>
{person_html(him, '男命')}{person_html(her, '女命')}
<h2>合婚依据</h2><ul>{points}</ul>
<h2>相处建议</h2><ul>{advice}</ul>
<p><small>{escape(str(hehun.get('review') or ''))}</small></p>
<p><small>{escape(str(hehun.get('boundary') or ''))}</small></p>
</body></html>"""


def create_bazi_router(paths: LovePaths, *, role_service: Any = None,
                       read_only: bool = False,
                       prefix: str = "/api/love/bazi") -> APIRouter:
    if not read_only:
        ensure_bazi_schema(paths)
    router = APIRouter(prefix=prefix, tags=["love-bazi"])

    def connection() -> sqlite3.Connection:
        if not paths.bazi_db.is_file():
            if read_only:
                raise HTTPException(404, "还没有八字合婚数据")
            ensure_bazi_schema(paths)
        return _connect(paths.bazi_db)

    @router.post("/chart")
    def chart(payload: GeneratePayload):
        chart_data, notes = _chart_payload(payload.him, payload.her)
        return {"chart": chart_data, "notes": notes}

    @router.post("/generate")
    def generate(payload: GeneratePayload):
        try:
            ensure_vault_writable(read_only, "生成八字合婚")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        if role_service is None:
            raise HTTPException(503, "AI 不可用：dsh 角色服务没有启用")
        from .role_policy import RolePolicy
        RolePolicy.for_role("love_master", paths.root.parent.parent).check_write_path(
            paths.bazi_db.relative_to(paths.root.parent.parent).as_posix())
        chart_data, notes = _chart_payload(payload.him, payload.her)
        title = payload.title.strip() or f"{chart_data['him']['name']} × {chart_data['her']['name']} · 八字合婚"
        now = _now()
        with connection() as con:
            cursor = con.execute(
                """INSERT INTO reports
                (title, input_json, chart_json, result_json, status, engine, error, created_at)
                VALUES (?, ?, ?, '', 'running', 'lunar_python+dsh', '', ?)""",
                (title, json.dumps(payload.model_dump(), ensure_ascii=False),
                 json.dumps(chart_data, ensure_ascii=False), now),
            )
            report_id = int(cursor.lastrowid)
            con.commit()
        prompt = json.dumps({
            "task": "八字合婚分析",
            "notes": notes,
            "男命": payload.him.model_dump(),
            "女命": payload.her.model_dump(),
            "本地命盘": chart_data,
            "输出要求": (
                "只输出一个 JSON 对象，不要 Markdown 代码块。结构："
                "{him:{pattern,favor,avoid,dayun,time_note},"
                "her:{pattern,favor,avoid,dayun,time_note,sections:[{title,body}]},"
                "hehun:{kicker,summary,verdict,score,points:[{glyph,title,body}],"
                "review,years:[{year,gz,text,tone:'up'|'down'|'flat'}],"
                "advice:[{title,body}],boundary}}。"
                "不要改动四柱、日主、十神、藏干和大运的本地计算结果；只写解读。"
            ),
        }, ensure_ascii=False)
        try:
            result = role_service.run_once(
                "love_master", "bazi", prompt, entry="love.overview", selectors=[])
        except Exception as error:  # noqa: BLE001
            with connection() as con:
                con.execute(
                    "UPDATE reports SET status='failed', error=?, completed_at=? WHERE id=?",
                    (str(error)[:1000], _now(), report_id))
                con.commit()
            raise HTTPException(502, f"AI 合婚分析失败：{error}") from error
        RolePolicy.for_role("love_master", paths.root.parent.parent).check_write_path(
            paths.bazi_db.relative_to(paths.root.parent.parent).as_posix())
        normalized = _normalize_ai(result.get("structured") or {}, chart_data)
        if not normalized["hehun"].get("summary"):
            normalized["hehun"]["summary"] = _text(result.get("text"), 1200)
        with connection() as con:
            con.execute(
                "UPDATE reports SET result_json=?, status='completed', completed_at=?, error='' WHERE id=?",
                (json.dumps(normalized, ensure_ascii=False), _now(), report_id))
            con.commit()
            row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return _report_row(row)

    @router.get("/history")
    def history(limit: int = Query(default=30, ge=1, le=100)):
        with connection() as con:
            rows = con.execute(
                "SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"items": [_history_item(row) for row in rows]}

    @router.get("/reports/{report_id}")
    def report(report_id: int):
        with connection() as con:
            row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "八字合婚记录不存在")
            return _report_row(row)

    @router.delete("/reports/{report_id}")
    def delete(report_id: int):
        try:
            ensure_vault_writable(read_only, "删除八字合婚记录")
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        with connection() as con:
            cursor = con.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            con.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "八字合婚记录不存在")
        return {"deleted": True}

    @router.get("/reports/{report_id}/html", response_class=HTMLResponse)
    def report_html(report_id: int):
        with connection() as con:
            row = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "八字合婚记录不存在")
            report_data = _report_row(row)
        return HTMLResponse(_report_html(report_data))

    return router


__all__ = ["ensure_bazi_schema", "create_bazi_router", "SCHEMA_SQL"]
