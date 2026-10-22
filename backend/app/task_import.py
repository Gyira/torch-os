"""Explicit, local Markdown checkbox imports with versioned source evidence.

Previewing never writes tasks, edits Markdown, activates memories or calls a
model. Applying inserts a selected batch and its durable idempotency ledger in
one SQLite transaction. The application supplies its shared Vault data guard.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from .db import Database, utc_now
from .vault import MAX_NOTE_BYTES, parse_text, restricted_path, safe_note_path


SHANGHAI = ZoneInfo("Asia/Shanghai")


# Deprecated (2026-10-22): new imports dedupe by the task note's `导入键`
# frontmatter and no longer write this table. Historical rows are retained.
SCHEMA = """
CREATE TABLE IF NOT EXISTS task_import_ledger (
    document_id INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    item_key TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    source_line INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(document_id, source_hash, item_key)
);
CREATE INDEX IF NOT EXISTS idx_task_import_task ON task_import_ledger(task_id);
"""

# No cascading foreign keys: a deleted task or source must not erase evidence
# that a prior selection was imported. A retry must never resurrect a task.
HexDigest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-fA-F]{64}$")]
ItemKey = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
CHECKBOX = re.compile(r"^[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+\[([ xX])\](?:[ \t]+(.*))?$")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
LIST_FENCE = re.compile(r"^[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
LIST_LINE = re.compile(r"^[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+")
LIST_PREFIX = re.compile(r"^([ \t]*)([-*+]|[0-9]+[.)])([ \t]+)")
COMMENT_OR_CODE = re.compile(r"<!--|`+")
ALLOWED_SCOPES = {"personal", "private", "public", "publishable", ""}


class TaskImportApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: StrictInt = Field(ge=1)
    sha256: HexDigest
    selected_keys: list[ItemKey] = Field(min_length=1, max_length=20)
    goal_path: str = Field(default="", max_length=500)
    confirm: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_selection_confirmation(self):
        if self.confirm is not True:
            raise ValueError("请明确确认所选待办的导入")
        if len(set(self.selected_keys)) != len(self.selected_keys):
            raise ValueError("所选待办不能重复")
        return self


def _mask_comments(line: str, inside: bool) -> tuple[str, bool, bool]:
    """Mask comments outside complete same-line backtick spans only.

    Precomputing the next delimiter of each length keeps long lines linear;
    shorter/longer runs inside a code span cannot prematurely close it.
    """
    next_code_end: dict[int, int] = {}
    following_run: dict[int, Any] = {}
    for run in reversed(list(re.finditer(r"`+", line))):
        size = len(run.group())
        if size in following_run:
            next_code_end[run.start()] = following_run[size].end()
        following_run[size] = run
    result: list[str] = []
    offset = 0
    encountered = inside
    while offset < len(line):
        if inside:
            end = line.find("-->", offset)
            if end < 0:
                result.append(" " * (len(line) - offset))
                return "".join(result), True, True
            result.append(" " * (end + 3 - offset))
            offset = end + 3
            inside = False
        else:
            token = COMMENT_OR_CODE.search(line, offset)
            if token is None:
                result.append(line[offset:])
                break
            start = token.start()
            result.append(line[offset:start])
            # An odd preceding backslash escapes the opener outside code.
            backslash = start
            while backslash > 0 and line[backslash - 1] == "\\":
                backslash -= 1
            if (start - backslash) % 2:
                result.append(token.group())
                offset = token.end()
                continue
            if token.group().startswith("`"):
                end = next_code_end.get(start, token.end())
                result.append(line[start:end])
                offset = end
                continue
            result.append("    ")
            offset = start + 4
            inside = encountered = True
    return "".join(result), inside, encountered


def parse_checkboxes(body: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Read checkbox lines as inert text and keep their original source line.

    ``body`` is the stored, frontmatter-free document body, making line numbers
    directly usable in the existing source viewer. Continuation prose is not
    silently merged: ambiguous multi-line items are explicitly warned about.
    """
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    headings: dict[int, str] = {}
    fence_character: str | None = None
    fence_length = 0
    inside_comment = saw_comment = saw_fence = False
    pending_line: int | None = None
    continuations: set[int] = set()
    truncated: list[int] = []
    empty: list[int] = []
    # Content indentation for enclosing list items; blank lines retain context.
    # Four extra columns are code (or ambiguous continuation), not a nested task.
    list_context: list[tuple[int, int]] = []
    skipped_indentation = 0

    for line_number, original in enumerate(body.split("\n"), start=1):
        if fence_character:
            fence = FENCE.match(original)
            if (fence and fence.group(1)[0] == fence_character
                    and len(fence.group(1)) >= fence_length and not fence.group(2).strip()):
                fence_character = None
            continue
        if original.strip() and not inside_comment:
            expanded = original.expandtabs(4)
            indentation = len(expanded) - len(expanded.lstrip(" "))
            while list_context and indentation < list_context[-1][1]:
                list_context.pop()
            content_indent = list_context[-1][1] if list_context else 0
            if indentation >= content_indent + 4:
                skipped_indentation += 1
                if pending_line is not None:
                    continuations.add(pending_line)
                pending_line = None
                continue
        visible, inside_comment, encountered_comment = _mask_comments(original, inside_comment)
        saw_comment |= encountered_comment
        prefix = LIST_PREFIX.match(visible)
        if prefix:
            marker_end = len(visible[:prefix.end(2)].expandtabs(4))
            content_indent = len(visible[:prefix.end()].expandtabs(4))
            if content_indent - marker_end > 4:
                skipped_indentation += 1
                pending_line = None
                continue
            list_context.append((len(prefix.group(1).expandtabs(4)), content_indent))
        fence = FENCE.match(visible) or LIST_FENCE.match(visible)
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            if pending_line is not None:
                continuations.add(pending_line)
            fence_character, fence_length = fence.group(1)[0], len(fence.group(1))
            saw_fence = True
            pending_line = None
            continue
        if not visible.strip():
            pending_line = None
            continue
        heading = HEADING.match(visible)
        if heading:
            level = len(heading.group(1))
            headings = {depth: title for depth, title in headings.items() if depth < level}
            headings[level] = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2) or "").strip()
            pending_line = None
            continue
        match = CHECKBOX.match(visible)
        if match:
            title = (match.group(2) or "").strip()
            if not title:
                empty.append(line_number)
                pending_line = None
                continue
            if len(title) > 200:
                truncated.append(line_number)
            # The body line distinguishes identical checkbox text in one source.
            key = hashlib.sha256(f"{line_number}\0{original}".encode("utf-8")).hexdigest()
            items.append({
                "key": key, "line": line_number, "title": title[:200],
                "description": original, "checked": match.group(1).lower() == "x",
                "heading": " / ".join(title for _, title in sorted(headings.items()) if title),
            })
            pending_line = line_number
        elif LIST_LINE.match(visible):
            pending_line = None
        elif pending_line is not None:
            continuations.add(pending_line)

    if saw_fence or saw_comment:
        warnings.append("围栏代码块和 HTML 注释中的内容不参与待办识别。")
    if skipped_indentation:
        warnings.append(f"已忽略 {skipped_indentation} 行缩进代码或无法明确归属的缩进续行；仅识别可确定列表层级的嵌套待办，请核对原文。")
    if fence_character or inside_comment:
        warnings.append("正文含未闭合的代码围栏或 HTML 注释，其后相关内容已忽略；请核对来源。")
    if truncated:
        warnings.append("第 " + "、".join(map(str, truncated)) + " 行的标题仅保留前 200 字符；完整复选框原始行保留在描述中。")
    if empty:
        warnings.append("第 " + "、".join(map(str, empty)) + " 行的复选框没有可见标题，未列入可导入待办。")
    if continuations:
        warnings.append("第 " + "、".join(map(str, sorted(continuations))) + " 行待办后可能有续行；本次仅导入完整复选框原始行，续行请核对来源后手动补充。")
    if any(item["checked"] for item in items):
        warnings.append("已勾选项导入为已完成，完成日期未知；完成时间留空，不计入任何具体一天的完成记录。")
    warnings.append("行号从去除 frontmatter 和首尾空白后的正文第 1 行计起；不推断截止日期。")
    return items, warnings


def _permitted_metadata(metadata: dict[str, Any]) -> bool:
    return (metadata.get("type") != "constitution" and all(
        str(metadata.get(key, "personal")).lower() in ALLOWED_SCOPES
        for key in ("scope", "memory_scope", "visibility", "sensitivity")
    ))


def _audit(connection: sqlite3.Connection, event_type: str, details: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit_events(event_type,detail_json,created_at) VALUES (?,?,?)",
        (event_type, json.dumps(details, ensure_ascii=False), utc_now()),
    )


class TaskImportService:
    def __init__(self, database: Database, vault_dir: Path):
        self.database = database
        self.vault_dir = Path(vault_dir)
        self.database.initialize()
        with self.database.connect() as connection:
            connection.executescript(SCHEMA)

    def _current_source(self, connection: sqlite3.Connection, document_id: int) -> sqlite3.Row:
        document = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if document is None:
            raise HTTPException(404, "来源不存在")
        if not document["available"] or document["privacy"] != "personal" or document["last_known_path"]:
            raise HTTPException(409, "来源已失效或不可用于待办导入，请重新扫描并预览")
        relative = document["path"]
        try:
            if (Path(relative).suffix.lower() != ".md" or restricted_path(Path(relative))
                    or not relative or "\x00" in relative):
                raise ValueError("restricted source")
            path = safe_note_path(self.vault_dir, relative)
            # A bounded read still enforces the limit if the file grows after stat.
            with path.open("rb") as handle:
                raw = handle.read(MAX_NOTE_BYTES + 1)
            if len(raw) > MAX_NOTE_BYTES:
                raise ValueError("source too large")
            # Check again after reading to catch an obvious directory/link swap.
            if safe_note_path(self.vault_dir, relative) != path:
                raise ValueError("source path changed")
            metadata, body = parse_text(raw.decode("utf-8-sig"))
            indexed_metadata = json.loads(document["metadata_json"])
            if not isinstance(indexed_metadata, dict) or not _permitted_metadata(metadata) or not _permitted_metadata(indexed_metadata):
                raise ValueError("restricted metadata")
            if (hashlib.sha256(raw).hexdigest() != document["sha256"]
                    or body != document["content"]
                    or json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
                    != json.dumps(indexed_metadata, ensure_ascii=False, sort_keys=True, default=str)
                    or (str(metadata.get("id") or "") or None) != document["source_key"]):
                raise ValueError("source changed")
            if document["source_key"] and connection.execute(
                "SELECT COUNT(*) FROM documents WHERE source_key=?", (document["source_key"],)
            ).fetchone()[0] != 1:
                raise ValueError("ambiguous source identity")
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            raise HTTPException(409, "来源内容、位置或权限已变化，请重新扫描资料并预览；本次未导入任务") from exc
        return document

    def preview(self, document_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            document = self._current_source(connection, document_id)
            items, warnings = parse_checkboxes(document["content"])
            return {"document_id": document_id, "revision": document["revision"],
                    "sha256": document["sha256"], "items": items, "warnings": warnings}

    @staticmethod
    def _public_task(note: dict[str, Any]) -> dict[str, Any]:
        from . import task_notes
        return {
            "key": note["key"], "path": note["relpath"], "legacy": False,
            "title": note["title"], "list_name": note["list_name"], "done": note["done"],
            "done_on": note["done_on"], "date": note["date"], "time": note["time"],
            "priority": note["priority"], "tags": list(note["tags"]),
            "pinned": note["pinned"], "created": note["created"],
            "goal": note.get("goal", ""),
            # Compatibility for the import result card.
            "status": "completed" if note["done"] else "todo",
            "completed_at": note["done_on"] or None,
            "description": task_notes.note_body(note),
        }

    @staticmethod
    def _import_key(document_id: int, item_key: str) -> str:
        # Stable across source revisions: the item key already identifies the
        # exact checkbox line; the document id keeps two sources separate.
        return hashlib.sha256(
            f"{document_id}\0{item_key}".encode("utf-8")).hexdigest()

    def apply(self, document_id: int, payload: TaskImportApply) -> dict[str, Any]:
        # Imported lazily: markdown_store imports this module for parsing helpers,
        # so a top-level goal/task-note import would create a cycle.
        from . import goal_notes, task_notes
        with self.database.connect() as connection:
            # Serialize imports in this process/database. Note writes happen
            # outside SQLite, but the scan-then-create critical section must not
            # run twice concurrently for the same source key.
            connection.execute("BEGIN IMMEDIATE")
            document = self._current_source(connection, document_id)
            if document["revision"] != payload.revision or document["sha256"] != payload.sha256.lower():
                raise HTTPException(409, "来源版本已变化，请重新预览并选择待办")
            goal = None
            if payload.goal_path:
                goal = goal_notes.resolve_goal(self.vault_dir, payload.goal_path)
                if goal is None:
                    raise HTTPException(404, "关联目标不存在，请重新选择")
            items, warnings = parse_checkboxes(document["content"])
            by_key = {item["key"]: item for item in items}
            if any(key not in by_key for key in payload.selected_keys):
                raise HTTPException(422, "所选待办不在当前来源预览中，请重新选择")

            notes, _scan_warnings = task_notes.scan_task_notes(self.vault_dir)
            by_import = {note.get("import_key"): note for note in notes if note.get("import_key")}
            tasks: list[dict[str, Any]] = []
            created_paths: list[str] = []
            existing_count = 0
            for key in payload.selected_keys:
                item = by_key[key]
                import_key = self._import_key(document_id, key)
                previous = by_import.get(import_key)
                if previous:
                    tasks.append(self._public_task(previous))
                    existing_count += 1
                    continue
                source = f"来源：{document['path']} 第 {item['line']} 行"
                body = "\n\n".join(part for part in (str(item.get("description") or "").strip(), source) if part)
                note = task_notes.create_task_note(self.vault_dir, "", item["title"], {
                    "priority": "none",
                    "tags": ["markdown-import"],
                    "done": bool(item["checked"]),
                    "done_on": "",
                    "created": datetime.now(SHANGHAI).date().isoformat(),
                    "goal": goal["path"] if goal else "",
                    "import_key": import_key,
                }, body=body)
                by_import[import_key] = note
                tasks.append(self._public_task(note))
                created_paths.append(note["relpath"])

            # Recheck the source after writing notes. Files are not part of the
            # SQL transaction, but each created note carries its stable import
            # key, so a retry finds it instead of creating a duplicate.
            self._current_source(connection, document_id)
            if created_paths:
                # Paths contain task titles; the audit stays count-only.
                _audit(connection, "markdown_tasks_imported", {
                    "document_id": document_id,
                    "created": len(created_paths),
                    "existing": existing_count,
                })
            if existing_count:
                warnings.append("重复选择返回已有任务，保留其当前标题、目标、完成状态及其他编辑。")
            return {"created_count": len(created_paths), "existing_count": existing_count,
                    "tasks": tasks, "warnings": warnings}


def create_task_import_router(database: Database, vault_dir: Path) -> APIRouter:
    """Mount with the app's shared data-lock dependency, like the journal."""
    service = TaskImportService(database, vault_dir)
    router = APIRouter(prefix="/api", tags=["task-import"])

    @router.get("/documents/{document_id}/tasks/preview")
    def preview_tasks(document_id: int):
        return service.preview(document_id)

    @router.post("/documents/{document_id}/tasks/apply")
    def apply_tasks(document_id: int, payload: TaskImportApply):
        return service.apply(document_id, payload)

    return router
