"""生活运营的结构化月度表（2026-10-21）。

体重/账目以「一个月一篇 Markdown 表格」为权威源；每行有稳定 row_id。
读取时直接解析 Markdown，SQLite 投影可以后续重建，不参与事实判断。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException

from . import vault_layout
from .db import utc_now
from .markdown_store import guarded_write, load_note
from .vault import parse_text, safe_note_path

DATASETS = {
    "health": {
        "chapter": "health",
        "type": "health_month",
        "title": "健康与体重",
        "columns": [("row_id", "ID"), ("date", "日期"), ("weight_kg", "体重kg"),
                    ("sleep_minutes", "睡眠min"), ("exercise_minutes", "运动min"),
                    ("note", "备注")],
    },
    "finance": {
        "chapter": "finance",
        "type": "finance_month",
        "title": "财富与记账",
        "columns": [("row_id", "ID"), ("date", "日期"), ("kind", "类型"),
                    ("amount", "金额"), ("category", "分类"), ("account", "账户"),
                    ("note", "备注")],
    },
}

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
ROW_RE = re.compile(r"^[0-9a-f]{8,24}$")


def _dataset(dataset: str) -> dict[str, Any]:
    item = DATASETS.get(str(dataset or ""))
    if item is None:
        raise HTTPException(404, "未知的数据集")
    return item


def _month(value: str) -> str:
    text = str(value or "").strip()
    if not MONTH_RE.fullmatch(text):
        raise HTTPException(422, "月份格式应为 YYYY-MM")
    return text


def month_path(vault_dir: Path | str, dataset: str, month: str) -> str:
    item = _dataset(dataset)
    return f"{vault_layout.chapter_dir(item['chapter'])}/{_month(month)}.md"


def _clean(value: Any, limit: int = 500) -> str:
    return str(value or "").replace("|", "／").replace("\n", " ").strip()[:limit]


def _frontmatter(dataset: str, month: str) -> dict[str, Any]:
    item = _dataset(dataset)
    now = utc_now()
    return {
        "type": item["type"], "title": f"{item['title']} {month}", "month": month,
        "schema": f"{dataset}-v1", "pillar": "ops", "chapter": item["chapter"],
        "status": "active", "created": now, "updated": now,
    }


def _render(metadata: dict[str, Any], columns: list[tuple[str, str]],
            rows: list[dict[str, str]]) -> list[str]:
    header = "| " + " | ".join(label for _key, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [header, divider]
    for row in rows:
        body.append("| " + " | ".join(_clean(row.get(key, "")) for key, _label in columns) + " |")
    return ["---", *yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip("\n").split("\n"),
            "---", "", *body, ""]


def _parse_table(text: str, columns: list[tuple[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < len(columns):
            cells.extend([""] * (len(columns) - len(cells)))
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells[:len(columns)]):
            continue
        row = dict(zip((key for key, _label in columns), cells))
        if row.get("row_id") and ROW_RE.fullmatch(row["row_id"]):
            rows.append(row)
    return rows


def read_month(vault_dir: Path | str, dataset: str, month: str) -> dict[str, Any]:
    item = _dataset(dataset)
    month = _month(month)
    relative = month_path(vault_dir, dataset, month)
    path = safe_note_path(Path(vault_dir), relative)
    if not path.is_file():
        return {"dataset": dataset, "month": month, "path": relative, "exists": False,
                "rows": [], "revision": "", "columns": item["columns"]}
    loaded = load_note(path)
    metadata, body = parse_text(loaded["raw"].decode("utf-8-sig"))
    return {
        "dataset": dataset, "month": month, "path": relative, "exists": True,
        "metadata": metadata, "rows": _parse_table(body, item["columns"]),
        "revision": loaded["revision"], "columns": item["columns"],
    }


def upsert_row(vault_dir: Path | str, dataset: str, month: str, values: dict[str, Any],
               row_id: str = "") -> dict[str, Any]:
    item = _dataset(dataset)
    month = _month(month)
    current = read_month(vault_dir, dataset, month)
    rows = [dict(row) for row in current["rows"]]
    if row_id:
        if not ROW_RE.fullmatch(row_id):
            raise HTTPException(422, "row_id 格式不正确")
        match = next((row for row in rows if row["row_id"] == row_id), None)
        if match is None:
            raise HTTPException(404, "记录不存在")
        target = match
    else:
        target = {"row_id": uuid4().hex[:12]}
        rows.append(target)
    for key, _label in item["columns"]:
        if key == "row_id":
            continue
        if key in values and values[key] is not None:
            target[key] = _clean(values[key])
    if not target.get("date"):
        raise HTTPException(422, "日期不能为空")
    metadata = current.get("metadata") or _frontmatter(dataset, month)
    metadata["updated"] = utc_now()
    relative = month_path(vault_dir, dataset, month)
    path = safe_note_path(Path(vault_dir), relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = _render(metadata, item["columns"], rows)
    if path.exists():
        loaded = load_note(path)
        if loaded.get("revision") and current.get("revision") and loaded["revision"] != current["revision"]:
            raise HTTPException(409, "月度表已在别处修改，请刷新后重试")
        guarded_write(loaded, lines)
    else:
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return read_month(vault_dir, dataset, month)


def delete_row(vault_dir: Path | str, dataset: str, month: str, row_id: str) -> dict[str, Any]:
    item = _dataset(dataset)
    current = read_month(vault_dir, dataset, month)
    if not current["exists"]:
        raise HTTPException(404, "月度表不存在")
    rows = [dict(row) for row in current["rows"] if row["row_id"] != row_id]
    if len(rows) == len(current["rows"]):
        raise HTTPException(404, "记录不存在")
    metadata = current["metadata"]
    metadata["updated"] = utc_now()
    relative = month_path(vault_dir, dataset, month)
    path = safe_note_path(Path(vault_dir), relative)
    loaded = load_note(path)
    guarded_write(loaded, _render(metadata, item["columns"], rows))
    return read_month(vault_dir, dataset, month)


def summary(vault_dir: Path | str, dataset: str, months: int = 12) -> dict[str, Any]:
    item = _dataset(dataset)
    root = Path(vault_dir) / vault_layout.chapter_dir(item["chapter"])
    found: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.glob("*.md"), reverse=True)[:max(1, min(60, months))]:
            if not MONTH_RE.fullmatch(path.stem):
                continue
            result = read_month(vault_dir, dataset, path.stem)
            if result["exists"]:
                found.append(result)
    return {"dataset": dataset, "months": found, "columns": item["columns"]}


__all__ = ["DATASETS", "month_path", "read_month", "upsert_row", "delete_row", "summary"]
