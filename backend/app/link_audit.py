"""Read-only, bounded Markdown link inventory against a Wolai ZIP directory.

Only the explicitly selected ordinary notes are decompressed. Targets are never
opened, rewritten, downloaded, imported, or passed to a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import string
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from .vault import MAX_NOTE_BYTES
from .wolai_import import (
    MAX_IMPORT_NOTES,
    WolaiImportError,
    _categories,
    _hash_file,
    _local_path,
    _open_archive,
)


MAX_LINKS_PER_NOTE = 5_000
MAX_SYNTAX_LENGTH = 8_192
MAX_REPORT_ATTACHMENTS = 200
MAX_MARKDOWN_ROWS = 50
CLASSIFICATIONS = (
    "resolved_note", "resolved_attachment", "anchor_only", "external",
    "manual_review", "missing", "unsafe", "restricted_target",
)
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_CLASSIFICATION_LABELS = {
    "resolved_note": "笔记路径存在", "resolved_attachment": "附件路径存在", "anchor_only": "页内锚点",
    "external": "外部链接（未访问）", "manual_review": "需人工核对", "missing": "目标缺失",
    "unsafe": "不安全路径", "restricted_target": "受限目标",
}
_REVIEW_KINDS = ("missing", "unsafe", "manual_review", "restricted_target")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ESCAPED = re.compile(r"\\([" + re.escape(string.punctuation) + r"])")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WOLAI_PAGE_PATH = re.compile(r"/[A-Za-z0-9]{22}")
_WARNINGS = [
    "只核对所选 Markdown 的链接目标与 ZIP 清单存在性；不验证目标内容、锚点、附件完整性或全部文件 CRC。",
    "目录敏感词沿用导入规则，仅是隐私提示；普通目录也可能含敏感资料。",
    "不读取链接目标正文或附件，不递归、不联网、不调用模型、不重写原文。",
    "有限解析器支持同一行的 Markdown 链接/图片、引用式链接及定义；不保证完整 CommonMark、HTML、自动链接、跨行或嵌套容器语法。",
    "报告保留来源路径和链接地址，可能包含私密文件名、查询参数或签名；应仅保存在本地。",
]


def _escaped_at(text: str, index: int) -> bool:
    count = 0
    while index and text[index - 1] == "\\":
        count += 1
        index -= 1
    return bool(count % 2)


def _mask_code(text: str) -> list[str]:
    """Preserve original line positions while blanking code and HTML comments."""
    chars = list(text)

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    offset = 0
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line.rstrip("\r\n"))
        if fence:
            blank(offset, offset + len(line))
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
        elif match:
            fence = match[1][0], len(match[1])
            blank(offset, offset + len(line))
        elif line.startswith(("    ", "\t")):
            blank(offset, offset + len(line))
        offset += len(line)

    masked = "".join(chars)
    ticks = list(re.finditer(r"`+", masked))
    next_run: dict[int, int] = {}
    last_by_width: dict[int, int] = {}
    for index in range(len(ticks) - 1, -1, -1):
        width = len(ticks[index][0])
        if width in last_by_width:
            next_run[index] = last_by_width[width]
        last_by_width[width] = index
    index = 0
    while index < len(ticks):
        if index in next_run and not _escaped_at(masked, ticks[index].start()):
            end_index = next_run[index]
            blank(ticks[index].start(), ticks[end_index].end())
            index = end_index + 1
        else:
            index += 1

    # Comments cannot introduce links or reference definitions. Code is masked
    # first so an HTML-comment literal inside code cannot hide later links.
    masked = "".join(chars)
    start = masked.find("<!--")
    while start >= 0:
        end = masked.find("-->", start + 4)
        end = len(masked) if end < 0 else end + 3
        blank(start, end)
        start = masked.find("<!--", end)
    return "".join(chars).splitlines()


def _label_end(text: str, start: int) -> int | None:
    depth = 1
    index = start + 1
    while index < min(len(text), start + MAX_SYNTAX_LENGTH):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if not depth:
                return index
        index += 1
    return None


def _destination(text: str, start: int, inline: bool) -> tuple[str | None, int, bool]:
    """Scan a destination and optional title without regex balancing tricks."""
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    begin = index
    if index < len(text) and text[index] == "<":
        index += 1
        begin = index
        while index < len(text) and index - start < MAX_SYNTAX_LENGTH:
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == ">":
                break
            if text[index] == "<":
                return None, index + 1, False
            index += 1
        if index >= len(text) or text[index] != ">":
            return None, len(text), False
        target = text[begin:index]
        index += 1
    else:
        depth = 0
        while index < len(text) and index - start < MAX_SYNTAX_LENGTH:
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if char.isspace():
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if not depth:
                    break
                depth -= 1
            index += 1
        target = text[begin:index]
        if depth or index - start >= MAX_SYNTAX_LENGTH:
            return target[:MAX_SYNTAX_LENGTH], index, False

    target = _ESCAPED.sub(r"\1", target)
    after_target = index
    while index < len(text) and text[index].isspace():
        index += 1
    if index > after_target and index < len(text) and text[index] in "\"'(":
        closer = ")" if text[index] == "(" else text[index]
        index += 1
        while index < len(text) and index - start < MAX_SYNTAX_LENGTH:
            if text[index] == "\\":
                index += 2
                continue
            if text[index] == closer:
                break
            index += 1
        if index >= len(text) or text[index] != closer:
            return target, len(text), False
        index += 1
        while index < len(text) and text[index].isspace():
            index += 1
    if inline:
        valid = index < len(text) and text[index] == ")"
        return target, index + 1 if valid else index, valid
    return target, index, index == len(text) and bool(target)


def _reference_id(text: str) -> str:
    return " ".join(_ESCAPED.sub(r"\1", text).split()).casefold()


def _links(text: str) -> tuple[list[dict[str, Any]], bool]:
    lines = _mask_code(text)
    definitions: dict[str, str] = {}
    definition_lines: set[int] = set()
    for number, line in enumerate(lines, start=1):
        start = len(line) - len(line.lstrip(" "))
        if start > 3 or start >= len(line) or line[start] != "[":
            continue
        end = _label_end(line, start)
        if end is None or line[end + 1:end + 2] != ":":
            continue
        definition_lines.add(number)
        target, _, valid = _destination(line, end + 2, False)
        key = _reference_id(line[start + 1:end])
        if valid and key and key not in definitions:
            definitions[key] = target or ""

    items: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if number in definition_lines:
            continue
        index = 0
        while index < len(line):
            if line[index] == "\\":
                index += 2
                continue
            if line[index] != "[":
                index += 1
                continue
            end = _label_end(line, index)
            if end is None:
                # Bound work for malformed notes containing many unmatched '['.
                # Recovery within that unsupported syntax window is incomplete.
                index += MAX_SYNTAX_LENGTH
                continue
            label = line[index + 1:end]
            following = end + 1
            target: str | None = None
            warnings: list[str] = []
            valid = True
            if following < len(line) and line[following] == "(":
                target, following, valid = _destination(line, following + 1, True)
                if not valid:
                    warnings.append("链接语法不完整或超出有限解析范围，请人工核对")
            elif following < len(line) and line[following] == "[":
                reference_end = _label_end(line, following)
                if reference_end is None:
                    index = following + 1
                    continue
                key = _reference_id(line[following + 1:reference_end] or label)
                target = definitions.get(key)
                following = reference_end + 1
                if target is None:
                    valid = False
                    warnings.append("引用式链接缺少可解析定义")
            else:
                target = definitions.get(_reference_id(label))
                if target is None:
                    index = following
                    continue
            if len(items) == MAX_LINKS_PER_NOTE:
                return items, True
            items.append({"line": number, "target": target, "warnings": warnings, "parsed": valid})
            index = max(following, end + 1)
    return items, False


def _resolve(source: str, target: str | None, records: dict[str, dict[str, Any]]) -> tuple[str, str | None, list[str]]:
    if target is None:
        return "manual_review", None, []
    if any(ord(char) < 32 or ord(char) == 127 for char in target) or "\\" in target:
        return "unsafe", None, ["目标含控制字符或 Windows 路径分隔符"]
    if target.startswith("//"):
        return "external", None, ["协议相对外链；未访问"]
    if _SCHEME.match(target):
        scheme = target.split(":", 1)[0].casefold()
        if scheme in {"http", "https", "mailto", "tel"}:
            return "external", None, ["外部地址；未访问或猜测为包内笔记"]
        return "unsafe", None, ["不支持或不允许的 URI 协议 / Windows 盘符"]
    try:
        parsed = urlsplit(target)
        if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
            return "manual_review", None, ["路径包含无效的百分号编码"]
        path = unquote(parsed.path, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeDecodeError):
        return "manual_review", None, ["目标地址或 UTF-8 百分号编码无效"]
    if any(ord(char) < 32 or ord(char) == 127 for char in path) or "\\" in path or ":" in path:
        return "unsafe", None, ["解码后路径含控制字符、Windows 分隔符或盘符 / 协议"]
    if _WOLAI_PAGE_PATH.fullmatch(parsed.path):
        # A Wolai page ID is not an archive-member mapping. Keep it unresolved;
        # do not classify it as a local filesystem path or guess a note name.
        return "manual_review", None, ["平台相对页面链接，需 ID 到导出路径映射；未访问或猜测目标"]
    if path.startswith("/"):
        return "unsafe", None, ["绝对路径不能解析为 ZIP 内相对成员"]
    if not path:
        return "anchor_only", source, ["当前文档内的地址；未检查锚点或查询参数"]
    parts = list(PurePosixPath(source).parent.parts)
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return "unsafe", None, ["相对路径逃出 ZIP 导出根目录"]
            parts.pop()
        else:
            if part.endswith((".", " ")):
                return "unsafe", None, ["目标含不安全的末尾点或空格路径"]
            parts.append(part)
    resolved = "/".join(parts)
    warnings = ["只核对路径存在性，未验证 fragment / query"] if parsed.fragment or parsed.query else []
    record = records.get(resolved)
    if _categories(resolved) or (record and record["sensitive"]):
        return "restricted_target", resolved, [*warnings, "目标路径命中敏感词提示；未读取目标"]
    if not record:
        return "missing", resolved, warnings
    if record["type"] == "directory":
        return "manual_review", resolved, [*warnings, "目标为目录，未猜测默认文档"]
    kind = "resolved_note" if record["type"] == "markdown" else "resolved_attachment"
    if record["instruction_like"]:
        warnings.append("目标是指令类资料；仅按不可信文档核对存在性")
    return kind, resolved, warnings


def _attachment_like(path: str | None) -> bool:
    if not isinstance(path, str) or not path:
        return False
    suffix = PurePosixPath(path).suffix.casefold()
    return bool(suffix) and suffix not in _MARKDOWN_SUFFIXES


def _md_text(value: Any, limit: int = 300) -> str:
    text = str(value if value is not None else "").replace("`", "'").replace("\n", " ").replace("\r", " ")
    return text[:limit]


def _publish_report(raw: bytes, output_dir: Path, filename: str) -> str:
    output_dir = _local_path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _local_path(output_dir)
        report_path = _local_path(output_dir / filename)
        if report_path.exists():
            if not report_path.is_file() or report_path.read_bytes() != raw:
                raise WolaiImportError("同名链接核对报告已存在且内容不同，未覆盖")
        else:
            with tempfile.TemporaryDirectory(prefix=".wolai-link-report-", dir=output_dir) as temporary:
                staged = Path(temporary) / "report.json"
                staged.write_bytes(raw)
                try:
                    os.link(staged, report_path)
                except FileExistsError:
                    # Concurrent identical requests may publish the same hash.
                    _local_path(report_path)
                    if not report_path.is_file() or report_path.read_bytes() != raw:
                        raise WolaiImportError("并发写入的报告内容不同，未覆盖")
        return str(report_path)
    except WolaiImportError:
        raise
    except OSError as exc:
        raise WolaiImportError("无法保存 Wolai 链接核对报告") from exc


def _markdown_report(result: dict[str, Any]) -> bytes:
    """Render a bounded, human-readable companion to the JSON report."""
    counts = result["counts"]
    attachments = result["attachments"]
    sensitive = result["sensitive_hints"]
    lines = [
        "# Wolai 链接核对报告（只读）", "",
        f"- 导出包：`{_md_text(result['archive_filename'])}`",
        f"- 包 SHA-256：`{result['archive_sha256']}`",
        f"- 核对范围：所选 {len(result['selected_paths'])} 篇 Markdown",
        f"- 链接总数：{counts['total']}",
        "", "## 分类计数", "", "| 分类 | 数量 |", "| --- | --- |",
    ]
    lines += [f"| {label} | {counts[key]} |" for key, label in _CLASSIFICATION_LABELS.items()]
    lines += [
        "", "## 附件提示", "",
        f"- 引用的附件路径：{attachments['referenced']} 个（存在 {attachments['found']} · 缺失 {attachments['missing']} · 受限 {attachments['restricted']}）",
        f"- ZIP 内附件总数：{attachments['archive_count']} 个",
        f"- {attachments['note']}",
    ]
    missing = [item for item in attachments["items"] if item["classification"] == "missing"]
    lines += ["", "## 缺失的附件", ""]
    if missing:
        for item in missing[:MAX_MARKDOWN_ROWS]:
            lines.append(f"- `{_md_text(item['source'])}` 第 {item['line']} 行 → `{_md_text(item['target'])}`（解析为 `{_md_text(item['resolved_path'])}`）")
        if len(missing) > MAX_MARKDOWN_ROWS:
            lines.append(f"- 另有 {len(missing) - MAX_MARKDOWN_ROWS} 条未列出，完整清单见 JSON 报告。")
    else:
        lines.append("- 没有发现缺失的附件路径。")
    review = [item for item in result["items"] if item["classification"] in _REVIEW_KINDS]
    lines += ["", "## 需要人工核对", ""]
    if review:
        for item in review[:MAX_MARKDOWN_ROWS]:
            target = _md_text(item["target"]) if item["target"] is not None else "（无法解析目标文本）"
            lines.append(f"- {_CLASSIFICATION_LABELS[item['classification']]}：`{_md_text(item['source'])}` 第 {item['line']} 行 → `{target}`")
        if len(review) > MAX_MARKDOWN_ROWS:
            lines.append(f"- 另有 {len(review) - MAX_MARKDOWN_ROWS} 条未列出，完整清单见 JSON 报告。")
    else:
        lines.append("- 没有需要人工核对的条目。")
    lines += [
        "", "## 敏感提示", "",
        f"- 导出清单中命中敏感目录名的条目：{sensitive['archive_entries']} 条（仅计数）。",
        f"- 链接目标命中敏感目录名：{sensitive['restricted_targets']} 个。",
        f"- {sensitive['note']}",
        "", "## 口径与边界", "",
    ]
    lines += [f"- {warning}" for warning in result["warnings"]]
    lines += ["", "报告由 薪火OS 在本机生成；未访问外部链接、未打开附件、未调用模型。", ""]
    return "\n".join(lines).encode("utf-8")


def audit_wolai_links(archive_path: Path, selected_paths: list[str], output_dir: Path | None = None) -> dict[str, Any]:
    """Audit 1–20 explicitly selected ordinary notes without following links.

    ``output_dir`` is a trusted server-selected local directory, never a client
    supplied path. Omitting it performs no writes at all.
    """
    if not isinstance(selected_paths, list) or not 1 <= len(selected_paths) <= MAX_IMPORT_NOTES or any(
        not isinstance(path, str) or path.casefold() in {"all", "*"} for path in selected_paths
    ):
        raise WolaiImportError("必须明确选择 1 至 20 篇 Markdown，不支持全部核对")
    if len(set(selected_paths)) != len(selected_paths):
        raise WolaiImportError("选择列表包含重复路径")
    items: list[dict[str, Any]] = []
    attachment_items: list[dict[str, Any]] = []
    sources = []
    with _open_archive(archive_path) as (path, archive, records):
        by_path = {record["path"].rstrip("/"): record for record in records}
        for member in selected_paths:
            record = by_path.get(member)
            if not record or record["type"] != "markdown":
                raise WolaiImportError("选中路径不存在或不是 Markdown")
            if record["sensitive"]:
                raise WolaiImportError("敏感目录的 Markdown 不允许读取正文进行核对")
            if not record["import_allowed"]:
                raise WolaiImportError("选中文档超过大小限制")
        for member in selected_paths:
            with archive.open(member) as stream:
                raw = stream.read(MAX_NOTE_BYTES + 1)
            if len(raw) > MAX_NOTE_BYTES:
                raise WolaiImportError("选中文档超过大小限制")
            try:
                body = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise WolaiImportError("仅支持 UTF-8 Markdown，请先在本地确认编码") from exc
            if "\x00" in body:
                raise WolaiImportError("Markdown 包含二进制字符")
            links, limited = _links(body)
            for link in links:
                classification, resolved, warnings = _resolve(member, link["target"], by_path)
                # Never claim existence was successfully checked for malformed
                # syntax, even if its partial destination happens to exist.
                if not link["parsed"] and classification != "unsafe":
                    classification, resolved = "manual_review", None
                items.append({
                    "source": member, "line": link["line"], "target": link["target"],
                    "classification": classification, "resolved_path": resolved,
                    "warnings": [*link["warnings"], *warnings],
                })
                if classification == "resolved_attachment" or (
                    classification in {"missing", "restricted_target"} and _attachment_like(resolved)
                ):
                    attachment_items.append({
                        "source": member, "line": link["line"], "target": link["target"],
                        "classification": classification, "resolved_path": resolved,
                    })
            sources.append({
                "source": member, "links_found": len(links), "limit_reached": limited,
                "warnings": [f"每篇最多核对 {MAX_LINKS_PER_NOTE} 个链接，后续链接未处理"] if limited else [],
            })
        counts = {**{kind: sum(item["classification"] == kind for item in items) for kind in CLASSIFICATIONS},
                  "total": len(items)}
        attachments = {
            "items": attachment_items[:MAX_REPORT_ATTACHMENTS],
            "truncated": len(attachment_items) > MAX_REPORT_ATTACHMENTS,
            "referenced": len(attachment_items),
            "found": sum(item["classification"] == "resolved_attachment" for item in attachment_items),
            "missing": sum(item["classification"] == "missing" for item in attachment_items),
            "restricted": sum(item["classification"] == "restricted_target" for item in attachment_items),
            "archive_count": sum(record["type"] == "attachment" for record in records),
            "note": "附件只做路径存在性提示：不导入、不复制、不本地化、不打开；缺失仅表示导出清单中没有这个目标。",
        }
        result = {
            "format": "torch-os-wolai-link-audit", "version": 3,
            "archive_filename": path.name, "archive_sha256": _hash_file(path),
            "selected_paths": list(selected_paths), "sources": sources,
            "items": items,
            "counts": counts,
            "summary": {
                "notes": len(selected_paths), "links": len(items),
                "needs_review": sum(counts[kind] for kind in _REVIEW_KINDS),
                "unresolved": counts["missing"] + counts["manual_review"],
            },
            "attachments": attachments,
            "sensitive_hints": {
                "archive_entries": sum(record["sensitive"] for record in records),
                "restricted_targets": counts["restricted_target"],
                "note": "仅按导出清单中的目录名与链接目标路径做提示；不读取敏感目录正文，不导入敏感资料。",
            },
            "content_verified": False, "attachments_verified": False,
            "parser_scope": "bounded-single-line-markdown-v1",
            "warnings": list(_WARNINGS),
        }
    if output_dir is not None:
        raw = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        result["report_path"] = _publish_report(raw, output_dir, f"link-audit-{hashlib.sha256(raw).hexdigest()}.json")
        markdown = _markdown_report(result)
        result["markdown_path"] = _publish_report(markdown, output_dir, f"link-audit-{hashlib.sha256(markdown).hexdigest()}.md")
    return result
