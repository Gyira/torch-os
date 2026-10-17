"""标准 Markdown 存储层：所有「个人数据 = Vault 里的 md」模块共用这一套读写协议。

与前端 `frontend/core.js` 的 `api()` 契约配套：

- **路径**：`vault.managed_note_path`（现行「中文（english）.md」优先，旧文件名兜底）。
- **读取**：`load_note` → `{exists, path, raw, revision, newline, lines, body_start}`；
  文件不存在是正常状态，不是错误。
- **修订**：`revision(raw) = sha256(raw)[:16]`；前端原样回传 `revision`，写入前比对，
  别处改过就 409，让用户刷新后重来。
- **写入**：`guarded_write` 先比对磁盘字节（不是只信前端传来的 revision），再写临时文件 +
  `os.replace`；保留原文件的 LF/CRLF，不留 `.tmp`。
- **行级手术**：`heading_line` / `section_bounds`，围栏代码块与 HTML 注释里的 `##` 不算标题。
- **错误约定**：413 过大、409 编码/冲突/只读、422 语义校验；响应仍是各域自己的原始 JSON
  （`path` / `exists` / `revision` + 域数据），不套统一信封——前端已经按这个形状消费。

`identity` 是整篇重写、`journal_note` 是追加式写入，二者保留自己的写语义；它们与任务/待办一样
复用这里的路径解析、修订值与错误码，不再各写一份。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .task_import import FENCE, HEADING, LIST_FENCE, _mask_comments
from .vault import MAX_NOTE_BYTES


def revision(raw: bytes) -> str:
    """文件内容的短修订号；前端把它当作乐观锁的版本号。"""
    return hashlib.sha256(raw).hexdigest()[:16]


def load_note(path: Path, *, too_large: str = "文件过大，已停止编辑",
              bad_encoding: str = "文件不是 UTF-8 编码，暂不能编辑") -> dict[str, Any]:
    """读取一份受管 Markdown；缺失文件返回 exists=False，不抛错。

    返回的形状就是各域共用的「loaded」字典：
    exists / path / raw / revision / newline / lines / body_start。
    """
    if not path.is_file():
        return {"exists": False, "path": path, "raw": None, "revision": "",
                "newline": "\n", "lines": [], "body_start": 0}
    raw = path.read_bytes()
    if len(raw) > MAX_NOTE_BYTES:
        raise HTTPException(413, too_large)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(409, bad_encoding) from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                body_start = index + 1
                break
    return {"exists": True, "path": path, "raw": raw, "revision": revision(raw),
            "newline": newline, "lines": lines, "body_start": body_start}


def guarded_write(loaded: dict[str, Any], new_lines: list[str]) -> bytes:
    """对比后再替换：别人在读取之后改过文件，就以 409 失败，绝不覆盖。"""
    path: Path = loaded["path"]
    text = loaded["newline"].join(new_lines)
    if loaded["exists"]:
        current = path.read_bytes()
        if current != loaded["raw"]:
            raise HTTPException(409, "文件已在别处修改，请刷新后重试")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)
    return text.encode("utf-8")


def heading_line(lines: list[str], start: int, title: str, level: int = 2) -> int:
    """找 ``## {title}``（0 基行号），跳过围栏代码块与 HTML 注释。"""
    inside_comment = False
    fence_character: str | None = None
    fence_length = 0
    for index in range(start, len(lines)):
        line = lines[index]
        if fence_character:
            fence = FENCE.match(line)
            if (fence and fence.group(1)[0] == fence_character
                    and len(fence.group(1)) >= fence_length and not fence.group(2).strip()):
                fence_character = None
            continue
        visible, inside_comment, _ = _mask_comments(line, inside_comment)
        fence = FENCE.match(visible) or LIST_FENCE.match(visible)
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            fence_character, fence_length = fence.group(1)[0], len(fence.group(1))
            continue
        heading = HEADING.match(visible)
        if heading and len(heading.group(1)) == level:
            text = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2) or "").strip()
            if text == title:
                return index
    return -1


def section_bounds(lines: list[str], start: int, heading_index: int) -> tuple[int, int]:
    """返回 ``[content_start, insert_at)``：``heading_index`` 所在小节的内容范围。"""
    inside_comment = False
    fence_character: str | None = None
    fence_length = 0
    content_start = heading_index + 1
    insert_at = len(lines)
    for index in range(content_start, len(lines)):
        line = lines[index]
        if fence_character:
            fence = FENCE.match(line)
            if (fence and fence.group(1)[0] == fence_character
                    and len(fence.group(1)) >= fence_length and not fence.group(2).strip()):
                fence_character = None
            continue
        visible, inside_comment, _ = _mask_comments(line, inside_comment)
        fence = FENCE.match(visible) or LIST_FENCE.match(visible)
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            fence_character, fence_length = fence.group(1)[0], len(fence.group(1))
            continue
        heading = HEADING.match(visible)
        if heading and len(heading.group(1)) <= 2:
            insert_at = index
            break
    while insert_at > content_start and not lines[insert_at - 1].strip():
        insert_at -= 1
    return content_start, insert_at
