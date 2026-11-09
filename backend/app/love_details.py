"""Chronological, evidence-linked detail ledger using the existing dsh role gateway."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from contextlib import closing
from datetime import date
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .love_privacy import desensitize

BATCH_CHARS = 20_000
LEDGER_CHARS = 33_000
FIELDS = ("category", "detail", "followup", "action")


class DetailEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=100)
    category: str = Field(max_length=40)
    detail: str = Field(min_length=1, max_length=600)
    followup: str = Field(max_length=600)
    action: str = Field(max_length=600)


class DetailsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(min_length=64, max_length=64)
    items: list[DetailEdit] = Field(max_length=500)
    reflection: str = Field(max_length=5000)


def revision(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_messages(paths, after_id: int = 0) -> list[dict]:
    """Read a bounded snapshot; insertion ID catches backfilled older messages too."""
    if not paths.chat_db.is_file():
        return []
    with closing(sqlite3.connect(paths.chat_db.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        names = {}
        contact = con.execute("SELECT * FROM contact WHERE id=1").fetchone()
        if contact:
            names = {str(contact[k]): alias for k, alias in (
                ("display_name", "对象"), ("partner_sender", "对象"), ("me_sender", "本人"))
                if contact[k]}
        rows = con.execute(
            "SELECT id,day,ts,is_send,content FROM messages WHERE id>? ORDER BY ts,id",
            (after_id,)).fetchall()
    return [{"id": r["id"], "day": r["day"],
             "who": "本人" if r["is_send"] else "对象",
             "content": desensitize(str(r["content"] or ""), names)} for r in rows]


def batches(messages: list[dict]):
    batch, size = [], 0
    for row in messages:
        content = row["content"].strip()
        if not content or re.fullmatch(r"\[[^\]\n]{1,60}\]", content):
            continue  # Media placeholders carry no readable evidence.
        cost = len(json.dumps(row, ensure_ascii=False)) + 2
        if cost > BATCH_CHARS:
            raise ValueError(f"聊天 #{row['id']} 过长，未截断；请先单独整理这条记录")
        if batch and size + cost > BATCH_CHARS:
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += cost
    if batch:
        yield batch


def normalize(raw: Any, previous: list[dict], sources: dict[int, dict]) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 500:
        raise ValueError("小细节结果格式错误：items 必须是最多500项的列表")
    old = {r["topic"]: r for r in previous}
    result, keys = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("小细节条目格式错误")
        topic = re.sub(r"\s+", "", str(item.get("topic") or "")).strip().casefold()
        if not topic or len(topic) > 100 or topic in keys:
            raise ValueError("小细节主题为空或重复，请重试")
        keys.add(topic)
        prior = old.get(topic)
        if prior and prior.get("manual"):
            result.append(prior)  # Model never overwrites a user-edited row.
            continue
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in sources for i in ids):
            raise ValueError(f"主题 {topic} 引用了不存在或未提供的聊天")
        ids = sorted(set(ids) | set((prior or {}).get("evidence_ids", [])))
        row = {"topic": topic, "evidence_ids": ids, "manual": False,
               "date": min(sources[i]["day"] for i in ids)}
        for field in FIELDS:
            value = item.get(field, "")
            if not isinstance(value, str) or len(value) > (40 if field == "category" else 600):
                raise ValueError(f"主题 {topic} 的文字过长或格式错误")
            row[field] = value.strip()
        if not row["detail"]:
            raise ValueError(f"主题 {topic} 缺少具体事情")
        result.append(row)
    if not set(old).issubset(keys):
        raise ValueError("模型遗漏了已有条目，已停止，原记录保留")
    return sorted(result, key=lambda r: (r["date"], r["topic"]))


def generate(paths, role_service, base: dict, progress, checkpoint) -> dict:
    # All reads happen within the same role-policy scope as the other love skills.
    after = int(base.get("watermark") or 0)
    messages = role_service.prepare_context(
        "love_master", "love", lambda: load_messages(paths, after), love_settings=paths).value
    items = base.get("items") or []
    reflection = base.get("reflection") or ""
    reflection_manual = bool(base.get("reflection_manual"))
    sources = {int(k): v for k, v in (base.get("sources") or {}).items()}
    chunks = list(batches(messages))
    for index, chunk in enumerate(chunks, 1):
        progress(f"正在整理第 {index}/{len(chunks)} 批，从 {chunk[0]['day']} 开始")
        sources.update({r["id"]: r for r in chunk})
        ledger = json.dumps(items, ensure_ascii=False)
        if len(ledger) > LEDGER_CHARS:
            raise ValueError("现有细节已超过单次上下文容量，未截断；已整理的批次保留在本次记录中")
        payload = json.dumps({"已有条目": items, "本批聊天": chunk,
                              "最后一批": index == len(chunks)}, ensure_ascii=False)
        context = role_service.prepare_context(
            "love_master", "love", lambda: payload, love_settings=paths)
        response = role_service.run_once(
            "love_master", "details", "合并本批聊天，返回完整条目。", context=context,
            entry="love.overview", selectors=[])
        structured = response.get("structured")
        if not isinstance(structured, dict):
            raise ValueError("DSH 没有返回有效的小细节 JSON")
        items = normalize(structured.get("items"), items, sources)
        if index == len(chunks) and not reflection_manual:
            value = structured.get("reflection", "")
            if not isinstance(value, str) or len(value) > 5000:
                raise ValueError("反思草稿格式错误")
            reflection = value.strip()
        referenced = {i for r in items for i in r["evidence_ids"]}
        sources = {i: r for i, r in sources.items() if i in referenced}
        checkpoint({"items": items, "reflection": reflection,
                    "reflection_manual": reflection_manual,
                    "sources": sources, "watermark": after, "partial": True})
    return {"items": items, "reflection": reflection, "sources": sources,
            "reflection_manual": reflection_manual,
            "watermark": max([after] + [r["id"] for r in messages]), "partial": False,
            "new_messages": len(messages), "batches": len(chunks),
            "text": f"已整理 {len(items)} 项，本次处理 {len(messages)} 条新增记录。"}


def edit_result(raw: str, payload: DetailsEdit) -> dict:
    if revision(raw) != payload.revision:
        raise HTTPException(409, "记录已经更新，请刷新后再修改")
    result = json.loads(raw)
    data = result["structured"]
    old = {r["topic"]: r for r in data["items"]}
    keys = [r.topic for r in payload.items]
    if len(keys) != len(set(keys)) or set(keys) != set(old):
        raise HTTPException(422, "只能修改现有条目，主题和聊天依据不能改")
    for row in payload.items:
        target = old[row.topic]
        values = {k: getattr(row, k).strip() for k in FIELDS}
        if not values["detail"]:
            raise HTTPException(422, "具体事情不能为空")
        if any(target.get(k) != v for k, v in values.items()):
            target.update(values, manual=True)
    if data.get("reflection", "") != payload.reflection.strip():
        data["reflection_manual"] = True
    data["reflection"] = payload.reflection.strip()
    return result


def export_xlsx(data: dict) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    sheet = wb.active
    sheet.title = "小细节记录"
    sheet.append(["最早引用日期", "类别", "说过的事", "后续情况", "接下来怎么做"])
    for row in data.get("items", []):
        sheet.append([date.fromisoformat(row["date"]), *[row.get(k, "") for k in FIELDS]])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [18, 18, 48, 52, 48]
    for i, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if isinstance(cell.value, str):
                cell.data_type = "s"  # User/model prose must never become an Excel formula.
        if row[0].row > 1:
            row[0].number_format = "yyyy-mm-dd"
            sheet.row_dimensions[row[0].row].height = 80
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="493348")
        cell.font = Font(color="FFFFFF", bold=True)
    evidence = wb.create_sheet("原聊天依据")
    evidence.append(["聊天ID", "日期", "说话人", "原话（已脱敏）"])
    for row in sorted(data.get("sources", {}).values(), key=lambda r: (r["day"], r["id"])):
        evidence.append([row["id"], row["day"], row["who"], row["content"]])
    evidence.column_dimensions["D"].width = 100
    evidence.freeze_panes = "A2"
    draft = wb.create_sheet("反思草稿")
    draft["A1"] = data.get("reflection") or ""
    draft.column_dimensions["A"].width = 100
    draft["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    draft.row_dimensions[1].height = 300
    for sh in (evidence, draft):
        for row in sh:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    stream = io.BytesIO()
    wb.save(stream)
    return Response(stream.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=love-details.xlsx"})
