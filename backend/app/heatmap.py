"""工作台横幅与数据看板的热力统计（2026-10-31）。

规则（2026-10-31 用户定义）：

- 人生路 / 知识库 / 自我档案：当天新建的 Markdown 文档数；有 frontmatter
  ``created`` / ``创建`` 时以它为准，否则看文件创建时间。每 1 篇升一档，4 篇满格。
  人生路排除习惯追踪目录（习惯定义/打卡表有独立的习惯行，避免重复计数）。
- 待办事项任务创建：``02_生活运营/00_待办事项`` 下（已完成/回收站/_系统 以外）当天
  新建的任务文件数；同样 4 项满格。
- 待办事项已完成：``已完成/`` 下 ``完成于`` 为当天的任务文件数；4 项满格。
- 习惯完成度：当天计划内习惯的完成比例；``skipped`` 是主动休息，不进分母。
  当天没有计划内习惯时这一行没有颜色，也不参与总热度。
- 总热度：其余各行得分的等权平均（每行最高 1 分）。任何一行单独拉满都只能贡献
  自己的份额，不会因为某一类活动多就把总分顶满。

颜色档位固定为 0 / 25 / 50 / 75 / 100 五档。计数行的得分 = min(1, 数量 / 4)，
因此 1/2/3/4 篇分别落到 25/50/75/100。

数据存档（用户指定放在个人数据库的 .obsidian 下，便于直接翻看）：

``<个人数据库>/.obsidian/torch-os-heatmap/``
- ``README.md``                        规则与目录说明
- ``file-created.json``               每个笔记第一次被看到时的创建日（编辑/原子替换不重置）
- ``daily/YYYY/YYYY-MM-DD.json``      某一天的 7 行格子、计数与文档/习惯清单
- ``weekly/YYYY/YYYY-Www.json``       某一周的汇总与每天热度
- ``monthly/YYYY/YYYY-MM.json``       某一月的汇总与每天热度
- ``yearly/YYYY.json``                某一年的汇总与每天热度

存档是快照；源数据仍是四库 Markdown 与习惯打卡表，重新计算时以源数据为准。
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, HTTPException, Query

from . import vault_layout
from .db import Database, utc_now
from .habits import HabitService
from .identity import DEFAULT_HEAT_COLOR, read_identity

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_RANGE_DAYS = 366
MAX_FILES_SCANNED = 30_000
META_HEAD_BYTES = 16_384
COUNT_CAP = 4
MAX_LEVEL = 4

ROW_ORDER = ("life", "knowledge", "self", "todo_created", "todo_done", "habits")
COUNT_ROWS = ("life", "knowledge", "self", "todo_created", "todo_done")
ROW_LABELS = {
    "life": "人生路",
    "knowledge": "知识库",
    "self": "自我档案",
    "todo_created": "任务创建",
    "todo_done": "任务完成",
    "habits": "习惯完成",
    "total": "总热度",
}
ROW_UNITS = {
    "life": "篇", "knowledge": "篇", "self": "篇",
    "todo_created": "项", "todo_done": "项", "habits": "项",
}
TOTAL_ROW = "total"

HEATMAP_REL = Path(".obsidian") / "torch-os-heatmap"
README_TEXT = """# 薪火OS · 热力存档

这里保存工作台横幅「统计模式」与数据看板的每日 / 每周 / 每月 / 每年快照，方便直接打开 JSON 回看。

## 规则

- 人生路、知识库、自我档案：当天新建的 Markdown 文档数，越多颜色越深。
  人生路不包含习惯追踪目录（习惯定义、打卡表、生成索引有独立的习惯行，避免重复计数）。
- 待办事项任务创建：`02_生活运营/00_待办事项` 下当天新建的任务数。
- 待办事项已完成：`02_生活运营/00_待办事项/已完成` 下完成于当天的任务数。
- 习惯完成度：当天计划内习惯的完成比例；主动休息（skipped）不进分母。
- 总热度：以上各行的等权平均，每行最高 100%，不会让某一类活动单独把总分拉满。
- 颜色五档：0 / 25 / 50 / 75 / 100。计数行每 4 篇（项）满格；习惯按完成比例。
- 当天没有计划内习惯时，习惯行不参与总热度；没有待办任务时，两行任务也不参与。

## 目录

- `file-created.json`：每个笔记第一次被看到时的创建日。原子写入会替换文件、让系统创建时间
  变成编辑时间；这份清单保证“编辑”不会被误算成“新增”。
- `daily/YYYY/YYYY-MM-DD.json`：当天的 7 行格子、计数、文档清单与习惯清单。
- `weekly/YYYY/YYYY-Www.json`：某一周的汇总与每天热度。
- `monthly/YYYY/YYYY-MM.json`：某一月的汇总与每天热度。
- `yearly/YYYY.json`：某一年的汇总与每天热度。

## 说明

- 源数据仍然是四库 Markdown 与习惯打卡表；这里只是快照，重新计算时以源数据为准。
- 每日文件在打开当天视图或使用横幅统计时写入；周/月/年视图写入对应的周期快照。
- 未来日期不会写入。
- `.obsidian` 不会进入保险库扫描与备份；需要长期备份时请单独复制这个目录。
"""


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


# ===== small date / metadata helpers =====


def _meta_day(value: Any) -> date | None:
    """frontmatter 里的日期：支持 date、datetime、ISO 字符串；带时区的先转上海。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(SHANGHAI).date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "T" in text or " " in text:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                return moment.date()
            return moment.astimezone(SHANGHAI).date()
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _birth_day(stat: os.stat_result) -> date:
    """文件创建日：Windows/macOS 用 birthtime，其它系统退回 mtime。"""
    birth = getattr(stat, "st_birthtime", None)
    if birth is not None:
        try:
            return datetime.fromtimestamp(birth, SHANGHAI).date()
        except (OSError, OverflowError, ValueError):
            pass
    if os.name == "nt":
        try:
            return datetime.fromtimestamp(stat.st_ctime, SHANGHAI).date()
        except (OSError, OverflowError, ValueError):
            pass
    try:
        return datetime.fromtimestamp(stat.st_mtime, SHANGHAI).date()
    except (OSError, OverflowError, ValueError):
        return date.min


def _mtime_day(stat: os.stat_result) -> date:
    try:
        return datetime.fromtimestamp(stat.st_mtime, SHANGHAI).date()
    except (OSError, OverflowError, ValueError):
        return date.min


def _read_metadata(path: Path) -> dict[str, Any]:
    """只读文件头，解析 frontmatter；读不到或不是 YAML 对象就当没有。"""
    try:
        with path.open("rb") as handle:
            head = handle.read(META_HEAD_BYTES)
    except OSError:
        return {}
    text = head.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}
    match = re.match(r"\A---[^\S\n]*\n(.*?)\n---[^\S\n]*(?:\n|$)", text, re.S)
    if not match:
        return {}
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是", "完成"}
    return bool(value)


def _classify(relative: str) -> str:
    text = relative
    # 习惯有自己的一行；习惯定义/打卡表/生成索引不再算进“人生路新增文档”，
    # 否则定义 3 个习惯就会凭空把写作热力图的当天档位拉高。
    habit_dir = vault_layout.chapter_dir("habit")
    if habit_dir and text.startswith(habit_dir + "/"):
        return ""
    # 旧版任务根仍在 00_人生路 下时，任务文件不该算成“人生路新增文档”；
    # 启动迁移会把它们搬到 02_生活运营/00_待办事项，这里只做只读兼容。
    for legacy_task_root in ("00_人生路/07_任务与待办", "00_人生路/08_任务与待办"):
        if text.startswith(legacy_task_root + "/"):
            return ""
    if text.startswith(vault_layout.PILLAR_DIRS["life"] + "/"):
        return "life"
    if text.startswith(vault_layout.PILLAR_DIRS["knowledge"] + "/"):
        return "knowledge"
    if text.startswith(vault_layout.PILLAR_DIRS["self"] + "/"):
        return "self"
    if text.startswith(vault_layout.TASK_ROOT + "/"):
        if text.startswith(vault_layout.TASK_DONE_DIR + "/"):
            return "todo_done"
        if (text.startswith(vault_layout.TASK_TRASH_DIR + "/")
                or text.startswith(vault_layout.TASK_SYSTEM_DIR + "/")):
            return ""
        return "todo_created"
    return ""


def _journal_filename_day(relative: str) -> date | None:
    """日记文件名 `YYYY-MM-DD.md` 是比系统创建时间更稳的写作日。"""
    prefix = vault_layout.chapter_dir("journal")
    if not prefix:
        return None
    match = re.fullmatch(re.escape(prefix) + r"/(\d{4}-\d{2}-\d{2})\.md", relative)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def level_from_score(score: float | None) -> int:
    """0 / 25 / 50 / 75 / 100 五档；None 与会不参与的行为 0。"""
    if score is None:
        return 0
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return max(1, min(MAX_LEVEL, int(value * MAX_LEVEL + 1e-9)))


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


# ===== document scan =====


def _ledger_day(value: Any) -> date | None:
    return _meta_day(value) if value else None


def load_ledger(vault_dir: Path | str) -> dict[str, str]:
    """读取笔记创建日清单；损坏/缺失时按空清单处理。"""
    path = _heatmap_path(vault_dir, "file-created.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return {}
    ledger: dict[str, str] = {}
    for key, value in files.items():
        day = _ledger_day(value)
        if day and isinstance(key, str) and key:
            ledger[key] = day.isoformat()
    return ledger


def save_ledger(vault_dir: Path | str, ledger: dict[str, str], *,
                read_only: bool = False) -> dict[str, str]:
    path = _heatmap_path(vault_dir, "file-created.json")
    payload = {
        "schema": 1,
        "generated_at": utc_now(),
        "files": {key: ledger[key] for key in sorted(ledger)},
    }
    return _archive(vault_dir, path, payload, read_only=read_only)


def _collect_document_days(vault_dir: Path | str, first: date, last: date,
                           *, include_documents: bool = False,
                           ledger: dict[str, str] | None = None
                           ) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, list[str]]],
                                      bool, bool, bool]:
    """一次 walk 把新建文档按「天 × 行」分桶。

    返回 ``(counts, documents, task_present, truncated, ledger_changed)``。
    ``ledger`` 会原地补上第一次见到的文件创建日；调用方负责把它写回
    ``.obsidian/torch-os-heatmap/file-created.json``。原子写入会替换文件、让
    Windows 的创建时间变成编辑时间，所以创建日一旦记录就不再随编辑漂移。
    """
    root = Path(vault_dir)
    cases = ledger if ledger is not None else {}
    ledger_changed = False
    counts: dict[str, dict[str, int]] = {row: {} for row in COUNT_ROWS}
    documents: dict[str, dict[str, list[str]]] = {
        row: {} for row in COUNT_ROWS} if include_documents else {}
    task_present = False
    truncated = False
    if not root.is_dir():
        return counts, documents, task_present, truncated, ledger_changed
    scanned = 0
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        try:
            base_rel = base.relative_to(root).as_posix()
        except ValueError:
            continue
        if base_rel == ".":
            base_rel = ""
        kept: list[str] = []
        for name in sorted(subdirs):
            relative = f"{base_rel}/{name}" if base_rel else name
            child = base / name
            if child.is_symlink():
                continue
            # is_skipped 也检查父级，所以给目录补一个占位文件名即可。
            if vault_layout.is_skipped(f"{relative}/_"):
                continue
            kept.append(name)
        subdirs[:] = kept
        for filename in sorted(filenames):
            if not filename.lower().endswith(".md"):
                continue
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                truncated = True
                break
            path = base / filename
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if vault_layout.is_skipped(relative) or path.is_symlink():
                continue
            category = _classify(relative)
            if not category:
                continue
            try:
                stat = path.stat()
                if not path.is_file():
                    continue
            except OSError:
                continue
            known = _ledger_day(cases.get(relative))
            if category in ("todo_created", "todo_done"):
                metadata = _read_metadata(path)
                task_present = True
                done = category == "todo_done" or _truthy(metadata.get("完成"))
                created = _meta_day(metadata.get("创建")) or known or _birth_day(stat)
                if created and cases.get(relative) != created.isoformat():
                    cases[relative] = created.isoformat()
                    ledger_changed = True
                # 今天创建、今天完成的任务应该在「创建」和「完成」两行都出现：
                # 完成只是任务文件被移进 已完成/，创建日仍然写在 frontmatter 里。
                pairs = [("todo_created", created)]
                if done:
                    pairs.append(("todo_done",
                                  _meta_day(metadata.get("完成于")) or _mtime_day(stat)))
                for bucket, day in pairs:
                    if not day or day < first or day > last:
                        continue
                    key = day.isoformat()
                    counts[bucket][key] = counts[bucket].get(key, 0) + 1
                    if include_documents:
                        documents[bucket].setdefault(key, []).append(relative)
                continue
            birth = _birth_day(stat)
            filename_day = _journal_filename_day(relative)
            # 有 birthtime 且不在窗口内时，已知创建日足够；只有第一次见到
            # 或文件落在窗口里才读 frontmatter，避免每次刷新都读全部历史笔记。
            metadata = {}
            if filename_day is None and (known is None or first <= birth <= last):
                metadata = _read_metadata(path)
            day = (filename_day
                   or _meta_day(metadata.get("created"))
                   or _meta_day(metadata.get("创建"))
                   or known
                   or birth)
            if day and cases.get(relative) != day.isoformat():
                cases[relative] = day.isoformat()
                ledger_changed = True
            if not day or day < first or day > last:
                continue
            key = day.isoformat()
            counts[category][key] = counts[category].get(key, 0) + 1
            if include_documents:
                documents[category].setdefault(key, []).append(relative)
        if truncated:
            break
    return counts, documents, task_present, truncated, ledger_changed


# ===== aggregate =====


def _habit_score(row: dict[str, Any]) -> float | None:
    scheduled = max(0, int(row.get("scheduled") or 0))
    skipped = max(0, int(row.get("skipped") or 0))
    done = max(0, int(row.get("done") or 0))
    effective = max(0, scheduled - skipped)
    if effective <= 0:
        return None
    return _clamp_score(done / effective)


def build_heatmap(database: Database, vault_dir: Path | str, first: date, last: date,
                  *, include_documents: bool = False,
                  ledger: dict[str, str] | None = None) -> dict[str, Any]:
    """返回 ``first..last`` 的每日七行格子（含未来日期占位）。

    ``ledger`` 是笔记首次创建日清单，原地更新；调用方据此写回 .obsidian。
    """
    today = shanghai_today()
    counts, documents, task_present, truncated, ledger_changed = _collect_document_days(
        vault_dir, first, last, include_documents=include_documents, ledger=ledger)
    habit_days: dict[str, dict[str, Any]] = {}
    habit_end = min(last, today)
    if first <= habit_end:
        try:
            habit_days = HabitService(database, vault_dir).daily_progress(first, habit_end)
        except Exception:
            # 习惯表读不了不应拖垮整个热力图；这一天按“没有计划”处理。
            habit_days = {}
    days: list[dict[str, Any]] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        key = day.isoformat()
        future = day > today
        rows: dict[str, Any] = {}
        for row in COUNT_ROWS:
            count = 0 if future else int(counts[row].get(key, 0))
            score = _clamp_score(min(1.0, count / COUNT_CAP))
            payload: dict[str, Any] = {
                "count": count,
                "score": score,
                "level": level_from_score(score),
            }
            if include_documents:
                payload["documents"] = ([] if future
                                        else list(documents.get(row, {}).get(key, [])))
            rows[row] = payload
        habit = habit_days.get(key) or {
            "scheduled": 0, "done": 0, "skipped": 0, "missed": 0,
            "done_titles": [], "skipped_titles": [], "missed_titles": [],
        }
        habit_row = {
            "scheduled": int(habit.get("scheduled") or 0),
            "done": int(habit.get("done") or 0),
            "skipped": int(habit.get("skipped") or 0),
            "missed": int(habit.get("missed") or 0),
        }
        if include_documents:
            habit_row.update({
                "done_titles": list(habit.get("done_titles") or []),
                "skipped_titles": list(habit.get("skipped_titles") or []),
                "missed_titles": list(habit.get("missed_titles") or []),
            })
        habit_score = None if future else _habit_score(habit_row)
        habit_row.update({
            "score": habit_score,
            "level": level_from_score(habit_score),
        })
        rows["habits"] = habit_row

        components = [rows[row]["score"] for row in ("life", "knowledge", "self")]
        if task_present:
            components.extend([rows["todo_created"]["score"], rows["todo_done"]["score"]])
        if habit_score is not None:
            components.append(habit_score)
        total_score = _clamp_score(sum(components) / len(components)) if components else 0.0
        rows[TOTAL_ROW] = {
            "score": total_score,
            "level": level_from_score(total_score),
            "components": len(components),
        }
        days.append({
            "date": key,
            "weekday": day.isoweekday(),
            "future": future,
            "rows": rows,
        })
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "today": today.isoformat(),
        "task_present": task_present,
        "truncated": truncated,
        "ledger_changed": ledger_changed,
        "ledger_entries": len(ledger or {}),
        "caps": {"count": COUNT_CAP, "max_level": MAX_LEVEL},
        "days": days,
    }


def _slim_day(day: dict[str, Any]) -> dict[str, Any]:
    """周期视图只需要计数和档位；去掉文档/习惯清单，控制 JSON 体积。"""
    rows: dict[str, Any] = {}
    for key, row in (day.get("rows") or {}).items():
        if not isinstance(row, dict):
            continue
        slim = {name: value for name, value in row.items()
                if name not in ("documents", "done_titles", "skipped_titles",
                                "missed_titles")}
        rows[key] = slim
    return {**day, "rows": rows}


def summarize(days: list[dict[str, Any]], *, task_present: bool) -> dict[str, Any]:
    """周期汇总：计数行的平均分把空白天算进去；习惯只统计有计划的日期。"""
    usable = [day for day in days if not day.get("future")]
    row_summary: dict[str, Any] = {}
    for row_key in ROW_ORDER:
        if row_key == "habits":
            scheduled = done = skipped = missed = 0
            active_days = 0
            scores: list[float] = []
            for day in usable:
                row = (day.get("rows") or {}).get("habits") or {}
                scheduled += int(row.get("scheduled") or 0)
                done += int(row.get("done") or 0)
                skipped += int(row.get("skipped") or 0)
                missed += int(row.get("missed") or 0)
                if int(row.get("scheduled") or 0) > 0:
                    active_days += 1
                if row.get("score") is not None:
                    scores.append(float(row["score"]))
            average = round(sum(scores) / len(scores), 4) if scores else None
            row_summary[row_key] = {
                "label": ROW_LABELS[row_key],
                "scheduled": scheduled, "done": done, "skipped": skipped,
                "missed": missed, "active_days": active_days,
                "score": average, "level": level_from_score(average),
            }
            continue
        total = 0
        active_days = 0
        scores: list[float] = []
        for day in usable:
            row = (day.get("rows") or {}).get(row_key) or {}
            count = int(row.get("count") or 0)
            total += count
            if count > 0:
                active_days += 1
            scores.append(float(row.get("score") or 0))
        average = round(sum(scores) / len(scores), 4) if scores else 0.0
        row_summary[row_key] = {
            "label": ROW_LABELS[row_key],
            "count": total, "active_days": active_days,
            "score": average, "level": level_from_score(average),
        }
    total_scores = [float((day.get("rows") or {}).get("total", {}).get("score") or 0)
                    for day in usable]
    average = round(sum(total_scores) / len(total_scores), 4) if total_scores else 0.0
    active_days = sum(1 for day in usable
                      if int((day.get("rows") or {}).get("total", {}).get("level") or 0) > 0)
    best = None
    for day in usable:
        row = (day.get("rows") or {}).get("total") or {}
        level = int(row.get("level") or 0)
        score = float(row.get("score") or 0)
        if best is None or (level, score, day["date"]) > (best["level"], best["score"], best["date"]):
            best = {"date": day["date"], "level": level, "score": score}
    row_summary[TOTAL_ROW] = {
        "label": ROW_LABELS[TOTAL_ROW],
        "active_days": active_days,
        "score": average,
        "level": level_from_score(average),
        "best": best,
        "task_present": task_present,
    }
    return {
        "days": len(usable),
        "active_days": active_days,
        "rows": row_summary,
    }


# ===== banner payload =====


def _banner_row_cells(days: list[dict[str, Any]], row_key: str) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for day in days:
        key = str(day["date"])
        row = (day.get("rows") or {}).get(row_key) or {}
        level = 0 if day.get("future") else int(row.get("level") or 0)
        if day.get("future"):
            title = f"{key} · 未来"
            value = ""
        elif row_key == "habits":
            scheduled = int(row.get("scheduled") or 0)
            done = int(row.get("done") or 0)
            skipped = int(row.get("skipped") or 0)
            if scheduled <= 0:
                title = f"{key} · 没有安排习惯"
                value = "—"
            else:
                effective = max(0, scheduled - skipped)
                value = f"{done}/{effective}" if effective else "休"
                title = f"{key} · 习惯 {done}/{effective}" + (f"（跳过 {skipped}）" if skipped else "")
        elif row_key == TOTAL_ROW:
            percent = int(round(float(row.get("score") or 0) * 100))
            value = f"{percent}%"
            title = f"{key} · 总热度 {percent}%"
        else:
            count = int(row.get("count") or 0)
            value = str(count)
            title = f"{key} · {ROW_LABELS[row_key]}新增 {count} {ROW_UNITS[row_key]}"
        cells.append({
            "date": key,
            "level": level,
            "today": key == day.get("today_key", ""),
            "value": value,
            "title": title,
        })
    return cells


def banner_heatmap(raw: dict[str, Any]) -> dict[str, Any]:
    """横幅统计模式的紧凑热力图：7 行 × 最近 N 天。"""
    today = raw.get("today", "")
    days = []
    for day in raw.get("days") or []:
        days.append({**day, "today_key": today})
    rows = []
    for row_key in (*ROW_ORDER, TOTAL_ROW):
        rows.append({
            "key": row_key,
            "label": ROW_LABELS[row_key],
            "cells": _banner_row_cells(days, row_key),
        })
    return {
        "start": raw.get("start", ""),
        "end": raw.get("end", ""),
        "today": today,
        "days": len(days),
        "caps": raw.get("caps") or {"count": COUNT_CAP, "max_level": MAX_LEVEL},
        "task_present": bool(raw.get("task_present")),
        "truncated": bool(raw.get("truncated")),
        "rows": rows,
    }


# ===== archive =====


def _heatmap_path(vault_dir: Path | str, *parts: str) -> Path:
    return Path(vault_dir) / HEATMAP_REL / Path(*parts)


def _write_text_atomic(path: Path, text: str) -> str:
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    if existing == text:
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return "written"


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        existing_text = path.read_text(encoding="utf-8")
        existing = json.loads(existing_text)
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict):
        old = dict(existing)
        new = dict(payload)
        old.pop("generated_at", None)
        new.pop("generated_at", None)
        if old == new:
            return "unchanged"
    return _write_text_atomic(path, text)


def _ensure_readme(vault_dir: Path | str) -> str:
    path = _heatmap_path(vault_dir, "README.md")
    try:
        return _write_text_atomic(path, README_TEXT)
    except OSError:
        return "error"


def _archive(vault_dir: Path | str, path: Path, payload: dict[str, Any],
             *, read_only: bool) -> dict[str, str]:
    relative = path.relative_to(Path(vault_dir)).as_posix()
    if read_only:
        return {"root": HEATMAP_REL.as_posix(), "path": relative,
                "status": "skipped", "reason": "read-only"}
    try:
        _ensure_readme(vault_dir)
        status = _write_json(path, payload)
        return {"root": HEATMAP_REL.as_posix(), "path": relative, "status": status}
    except OSError as error:
        return {"root": HEATMAP_REL.as_posix(), "path": relative,
                "status": "error", "error": str(error)}


def record_daily(vault_dir: Path | str, record: dict[str, Any], *,
                 read_only: bool = False,
                 task_present: bool | None = None) -> dict[str, str]:
    stamp = str(record.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
        return {"status": "skipped", "reason": "invalid-date"}
    path = _heatmap_path(vault_dir, "daily", stamp[:4], f"{stamp}.json")
    payload = {
        "schema": 1, "kind": "daily", "generated_at": utc_now(),
        "caps": {"count": COUNT_CAP, "max_level": MAX_LEVEL},
        **record,
    }
    if task_present is not None:
        payload["task_present"] = bool(task_present)
    return _archive(vault_dir, path, payload, read_only=read_only)


def record_period(vault_dir: Path | str, kind: str, key: str,
                  payload: dict[str, Any], *, read_only: bool = False) -> dict[str, str]:
    year = key[:4]
    folder = {"weekly": "weekly", "monthly": "monthly", "yearly": "yearly"}.get(kind)
    if not folder or not re.fullmatch(r"\d{4}", year):
        return {"status": "skipped", "reason": "invalid-period"}
    # 年度只有一个文件，按 README 的结构平铺；周/月按年分文件夹。
    path = (_heatmap_path(vault_dir, folder, f"{key}.json") if kind == "yearly"
            else _heatmap_path(vault_dir, folder, year, f"{key}.json"))
    body = {"schema": 1, "kind": kind, "period": key, "generated_at": utc_now(), **payload}
    return _archive(vault_dir, path, body, read_only=read_only)


# ===== board =====


def _parse_day(value: str) -> date | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _month_end(day: date) -> date:
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def _board_range(view: str, anchor: date) -> tuple[date, date]:
    if view == "day":
        return anchor.replace(day=1), _month_end(anchor)
    if view == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if view == "month":
        return anchor.replace(day=1), _month_end(anchor)
    return date(anchor.year, 1, 1), date(anchor.year, 12, 31)


def _period_info(view: str, anchor: date, first: date, last: date) -> dict[str, str]:
    if view == "day":
        return {"kind": "day", "key": anchor.isoformat(),
                "label": f"{anchor.year}年{anchor.month}月{anchor.day}日"}
    if view == "week":
        iso = anchor.isocalendar()
        return {"kind": "week", "key": f"{iso[0]}-W{iso[1]:02d}",
                "label": f"{iso[0]}年第{iso[1]}周 · {first.month}月{first.day}日–{last.month}月{last.day}日"}
    if view == "month":
        return {"kind": "month", "key": f"{anchor.year}-{anchor.month:02d}",
                "label": f"{anchor.year}年{anchor.month}月"}
    return {"kind": "year", "key": str(anchor.year), "label": f"{anchor.year}年"}


def _period_payload(kind: str, key: str, label: str, first: date, last: date,
                    summary: dict[str, Any], days: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "period": key,
        "label": label,
        "range": {"from": first.isoformat(), "to": last.isoformat()},
        "summary": summary,
        "days": days,
    }


def create_heatmap_router(database: Database, vault_dir: Path | str, *,
                          read_only: bool = False, prefix: str = "/api") -> APIRouter:
    """热力数据看板：日 / 周 / 月 / 年；读取时顺带写 .obsidian 快照。"""
    router = APIRouter(prefix=prefix, tags=["heatmap"])

    @router.get("/heatmap/board")
    def heatmap_board(view: str = Query(default="day"),
                      day: str = Query(default="")):
        clean_view = str(view or "day").strip().lower()
        if clean_view not in {"day", "week", "month", "year"}:
            clean_view = "day"
        today = shanghai_today()
        anchor = _parse_day(day) or today
        if anchor > today:
            anchor = today
        first, last = _board_range(clean_view, anchor)
        if (last - first).days > MAX_RANGE_DAYS:
            raise HTTPException(422, "看板范围过大")
        ledger = load_ledger(vault_dir)
        raw = build_heatmap(database, vault_dir, first, last,
                            include_documents=(clean_view == "day"),
                            ledger=ledger)
        if raw.get("ledger_changed"):
            save_ledger(vault_dir, ledger, read_only=read_only)
        days = [_slim_day(item) for item in raw["days"]]
        detail = None
        if clean_view == "day":
            found = next((item for item in raw["days"]
                          if item["date"] == anchor.isoformat()), None)
            if found and not found.get("future"):
                detail = found
        summary = summarize(raw["days"], task_present=bool(raw.get("task_present")))
        period = _period_info(clean_view, anchor, first, last)
        storage: dict[str, str]
        if clean_view == "day":
            storage = (record_daily(
                vault_dir, detail, read_only=read_only,
                task_present=bool(raw.get("task_present")))
                if detail else {"status": "skipped", "reason": "future"})
        else:
            kind = {"week": "weekly", "month": "monthly", "year": "yearly"}[clean_view]
            payload = _period_payload(
                kind, period["key"], period["label"], first, last, summary, days)
            storage = record_period(vault_dir, kind, period["key"], payload,
                                    read_only=read_only)
        return {
            "view": clean_view,
            "anchor": anchor.isoformat(),
            "today": today.isoformat(),
            "range": {"from": first.isoformat(), "to": last.isoformat()},
            "period": period,
            "caps": raw["caps"],
            "heat_color": (read_identity(vault_dir).get("banner_heat_color")
                           or DEFAULT_HEAT_COLOR),
            "task_present": bool(raw.get("task_present")),
            "truncated": bool(raw.get("truncated")),
            "rows": [
                {"key": key, "label": ROW_LABELS[key], "unit": ROW_UNITS[key]}
                for key in ROW_ORDER
            ] + [{"key": TOTAL_ROW, "label": ROW_LABELS[TOTAL_ROW], "unit": ""}],
            "days": days,
            "detail": detail,
            "summary": summary,
            "storage": storage,
        }

    return router


__all__ = [
    "build_heatmap", "banner_heatmap", "summarize", "level_from_score",
    "record_daily", "record_period", "create_heatmap_router",
    "ROW_ORDER", "ROW_LABELS", "COUNT_ROWS", "COUNT_CAP", "HEATMAP_REL",
    "shanghai_today",
]
