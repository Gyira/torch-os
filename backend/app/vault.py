from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import yaml

from uuid import uuid4
from .db import Database, utc_now

MAX_NOTE_BYTES = 1_048_576
EXTRACTION_VERSION = 'sentences-v3'
MAX_CANDIDATES_PER_DOCUMENT = 20
MAX_CANDIDATE_CHARACTERS = 400
MIN_CANDIDATE_CHARACTERS = 3
MEMORY_KINDS = {'fact', 'experience', 'belief', 'goal', 'preference', 'note', 'project', 'idea', 'memo'}
EXCLUDED_DIRECTORIES = {'.obsidian', '.git', '.trash', '00-system', '99-templates', 'templates',
                        'relationship', 'relationships', 'relationship-vault', 'relationship vault', 'generated'}


def restricted_path(relative: Path) -> bool:
    return any(part.casefold() in EXCLUDED_DIRECTORIES or part.startswith('.') for part in relative.parts)


def safe_note_path(vault_dir: Path, relative: str) -> Path:
    root = vault_dir.resolve()
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(root):
        raise ValueError('资料路径超出 Vault 范围')
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('不读取链接目录或符号链接')
    return path


def managed_note_path(vault_dir: Path | str, relative: str, legacy: str | None = None) -> Path:
    """定位产品托管的笔记：优先现行文件名；旧文件名还在时继续用它（只读兜底，不自动改名）。

    2026-10-17 起个人数据改用「中文（english）.md」命名。升级前已经存在的 `任务.md` 这类文件
    必须继续能读写——否则一次改名就等于让用户以为数据丢了。新库只会创建新名字的文件。
    """
    current = safe_note_path(Path(vault_dir), relative)
    if legacy and not current.exists():
        previous = safe_note_path(Path(vault_dir), legacy)
        if previous.exists():
            return previous
    return current


def note_relative(path: Path, vault_dir: Path | str) -> str:
    """给界面/接口返回的相对路径（用实际命中的文件，兼容旧命名）。"""
    return path.relative_to(Path(vault_dir).resolve()).as_posix()


def parse_text(text: str) -> tuple[dict[str, Any], str]:
    text = text.removeprefix('\ufeff').replace('\r\n', '\n')
    metadata: dict[str, Any] = {}
    body = text
    if text.split('\n', 1)[0].strip() == '---':
        match = re.match(r'\A---[^\S\n]*\n(.*?)\n---[^\S\n]*(?:\n|$)', text, re.S)
        if not match:
            raise ValueError('frontmatter 缺少结束分隔行')
        metadata = yaml.safe_load(match.group(1)) or {}
        if not isinstance(metadata, dict):
            raise ValueError('frontmatter 必须是键值对象')
        body = text[match.end():]
    return metadata, body.strip()


def parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError('单篇 Markdown 超出 1 MiB 限制')
    return parse_text(path.read_text(encoding='utf-8-sig'))


class MemoryCandidates(list):
    """A list-compatible result so ordinary and transactional imports share coverage."""

    def __init__(self, memories: list[dict[str, Any]], coverage_summary: dict[str, Any]):
        super().__init__(memories)
        self.coverage_summary = coverage_summary


_MARKDOWN_LINK = re.compile(r'!?\[([^\]]*)\]\([^)]*\)')
_HTML_TAG = re.compile(r'<[^>\n]{1,200}>')
_INLINE_CODE = re.compile(r'`([^`]*)`')
_HEADING_PREFIX = re.compile(r'^\s{0,3}#{1,6}\s+')
_QUOTE_PREFIX = re.compile(r'^\s{0,3}>\s?')
_LIST_PREFIX = re.compile(r'^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+')
_TASK_PREFIX = re.compile(r'^\[[ xX]\]\s*')
_TABLE_SEPARATOR = re.compile(r'^\s*\|?[\s:|-]+\|?\s*$')
_SENTENCE_ENDERS = '。！？!?'


def clean_fragment(text: str) -> str:
    """Turn one raw Markdown fragment into a readable single line (no markup noise)."""
    value = str(text or '').replace('\u200b', ' ')
    value = _MARKDOWN_LINK.sub(lambda match: match.group(1), value)
    value = _HTML_TAG.sub('', value)
    value = _INLINE_CODE.sub(lambda match: match.group(1), value)
    lines = []
    for line in value.splitlines():
        line = _HEADING_PREFIX.sub('', line)
        line = _QUOTE_PREFIX.sub('', line)
        line = _LIST_PREFIX.sub('', line)
        line = _TASK_PREFIX.sub('', line)
        if '|' in line:
            line = re.sub(r'\s*\|\s*', ' ', line).strip(' |')
        if _TABLE_SEPARATOR.match(line):
            continue
        for marker in ('**', '__', '~~', '*', '_'):
            line = line.replace(marker, '')
        lines.append(line.strip())
    return re.sub(r'\s+', ' ', ' '.join(part for part in lines if part)).strip()


def _is_memory_like(text: str) -> bool:
    if len(text) < MIN_CANDIDATE_CHARACTERS:
        return False
    if not re.search(r'[\u4e00-\u9fffA-Za-z]', text):
        return False
    if re.fullmatch(r'https?://\S+', text):
        return False
    return True


def readable_statement(raw: str) -> str:
    """Pick the first human-readable sentence from a possibly multi-line fragment."""
    lines = [clean_fragment(line) for line in str(raw or '').splitlines()]
    for minimum in (6, 3):
        for line in lines:
            if not _is_memory_like(line) or len(line) < minimum:
                continue
            for ender in ('。', '！', '？', '!', '?'):
                index = line.find(ender)
                if 0 <= index < 200:
                    line = line[:index + 1]
                    break
            return line[:MAX_CANDIDATE_CHARACTERS]
    return clean_fragment(raw)[:MAX_CANDIDATE_CHARACTERS]


def _sentence_spans(body: str, paragraphs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Split prose paragraphs into sentence-sized spans (hard cap per span)."""
    spans: list[tuple[int, int]] = []
    for start, end in paragraphs:
        cursor = start
        index = start
        while index < end:
            character = body[index]
            if character == '\n':
                if cursor < index:
                    spans.append((cursor, index))
                cursor = index + 1
            elif character in _SENTENCE_ENDERS:
                spans.append((cursor, index + 1))
                cursor = index + 1
            elif index - cursor + 1 >= MAX_CANDIDATE_CHARACTERS:
                spans.append((cursor, index + 1))
                cursor = index + 1
            index += 1
        if cursor < end:
            spans.append((cursor, end))
    return [(start, end) for start, end in spans if start < end]


def _candidate_spans(body: str) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """Return exact prose spans in the stored body, excluding headings and fences.

    Positions deliberately refer to the normalized document body, not the source
    file: parse_text removes frontmatter and strips outer whitespace first.
    """
    spans: list[tuple[int, int]] = []
    excluded = {'heading_lines': 0, 'code_blocks': 0, 'code_lines': 0}
    start: int | None = None
    offset = 0
    fence_character: str | None = None
    fence_length = 0

    def finish(end: int) -> None:
        nonlocal start
        if start is not None:
            while start < end and body[start].isspace():
                start += 1
            while end > start and body[end - 1].isspace():
                end -= 1
            if start < end:
                spans.append((start, end))
            start = None

    for line in body.splitlines(keepends=True):
        line_text = line.rstrip('\r\n')
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line_text)
        if fence_character:
            excluded['code_lines'] += 1
            if (fence and fence.group(1)[0] == fence_character
                    and len(fence.group(1)) >= fence_length and not fence.group(2).strip()):
                fence_character = None
        elif fence and not (fence.group(1)[0] == '`' and '`' in fence.group(2)):
            finish(offset)
            fence_character = fence.group(1)[0]
            fence_length = len(fence.group(1))
            excluded['code_blocks'] += 1
            excluded['code_lines'] += 1
        elif re.match(r'^ {0,3}#{1,6}(?:\s|$)', line_text):
            finish(offset)
            excluded['heading_lines'] += 1
        elif not line_text.strip():
            finish(offset)
        elif start is None:
            start = offset
        offset += len(line)
    finish(len(body))
    return spans, excluded


def _memory_from_document(metadata: dict[str, Any], body: str, path: Path) -> MemoryCandidates:
    kind = str(metadata.get('type') or 'note')
    if kind not in MEMORY_KINDS:
        kind = 'note'
    document_title = str(metadata.get('title') or path.stem)[:200]
    paragraphs, excluded = _candidate_spans(body)
    memories: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_segments = 0
    not_memory_like = 0
    duplicated = 0
    for start, end in _sentence_spans(body, paragraphs):
        total_segments += 1
        quote = body[start:end].strip()
        readable = clean_fragment(quote)
        if not _is_memory_like(readable):
            not_memory_like += 1
            continue
        key = readable.casefold()
        if key in seen:
            duplicated += 1
            continue
        seen.add(key)
        if len(memories) >= MAX_CANDIDATES_PER_DOCUMENT:
            continue
        readable = readable[:MAX_CANDIDATE_CHARACTERS]
        # The candidate title is the statement itself, so the review list reads
        # like memories instead of document titles repeated over raw chunks.
        candidate_title = readable if len(readable) <= 24 else readable[:24] + '…'
        memories.append({
            'kind': kind, 'title': candidate_title, 'content': readable,
            'source_quote': quote, 'document_title': document_title,
            'confidence': 'explicit' if metadata.get('source_kind', 'user-authored') == 'user-authored' else 'inferred',
            'source_start_line': body.count('\n', 0, start) + 1,
            'source_end_line': body.count('\n', 0, end - 1) + 1,
            'source_start_offset': start, 'source_end_offset': end,
            'source_location_basis': 'document_body',
        })
    extractable = total_segments - not_memory_like - duplicated
    omitted = extractable - len(memories)
    warnings = []
    if omitted:
        warnings.append(f'本篇共有 {extractable} 个像完整语句的片段，仅提取前 {MAX_CANDIDATES_PER_DOCUMENT} 个，仍有 {omitted} 个未提取；完整原文仍保留。')
    if not_memory_like or duplicated:
        warnings.append(f'另有 {not_memory_like} 个片段更像标题、表格或格式碎片，{duplicated} 个与前文重复，未作为候选（完整原文保留）。')
    if excluded['heading_lines'] or excluded['code_blocks']:
        warnings.append(f"提取范围不包含 {excluded['heading_lines']} 行标题和 {excluded['code_blocks']} 个围栏代码块。")
    return MemoryCandidates(memories, {
        'extraction_version': EXTRACTION_VERSION,
        'candidate_count': len(memories), 'total_candidate_segments': total_segments,
        'extractable_segments': extractable, 'not_memory_like_segments': not_memory_like,
        'duplicated_segments': duplicated,
        'omitted_segments': omitted, 'truncated': bool(omitted), 'coverage_complete': omitted == 0,
        'candidate_limit': MAX_CANDIDATES_PER_DOCUMENT, 'segment_char_limit': MAX_CANDIDATE_CHARACTERS,
        'excluded_heading_lines': excluded['heading_lines'],
        'excluded_code_blocks': excluded['code_blocks'], 'excluded_code_lines': excluded['code_lines'],
        'source_location_basis': 'document_body',
        'location_note': '行号从已移除 YAML 元数据及首尾空白的正文第 1 行计起；字符位置从 0 开始，结束位置不含在内。',
        'scope_note': '候选提取范围仅表示正文片段是否进入待确认列表，不代表语义事实覆盖率或记忆已确认。',
        'warnings': warnings,
    })


def scan_vault(vault_dir: Path, database: Database) -> dict[str, Any]:
    vault_dir.mkdir(parents=True, exist_ok=True)
    root = vault_dir.resolve()
    scanned = changed = skipped = excluded = 0
    errors: list[str] = []
    notes = []
    id_paths: dict[str, list[str]] = {}
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = [name for name in sorted(subdirs)
                      if not restricted_path((Path(directory) / name).relative_to(root))
                      and not (Path(directory) / name).is_symlink()
                      and not (hasattr(Path(directory) / name, 'is_junction') and (Path(directory) / name).is_junction())]
        for filename in sorted(filenames):
            if not filename.lower().endswith('.md'):
                continue
            scanned += 1
            relative = (Path(directory) / filename).relative_to(root).as_posix()
            try:
                if restricted_path(Path(relative)):
                    excluded += 1
                    continue
                path = safe_note_path(root, relative)
                if path.stat().st_size > MAX_NOTE_BYTES:
                    raise ValueError('超过 1 MiB')
                raw = path.read_bytes()
                metadata, body = parse_text(raw.decode('utf-8-sig'))
                if any(str(metadata.get(key, 'personal')).lower() not in {'personal','private','public','publishable',''}
                       for key in ('scope', 'memory_scope', 'visibility', 'sensitivity')) or metadata.get('type') == 'constitution':
                    excluded += 1
                    continue
                source_id = str(metadata.get('id') or '')
                if source_id:
                    id_paths.setdefault(source_id, []).append(relative)
                notes.append((path, relative, metadata, body, hashlib.sha256(raw).hexdigest(), source_id))
            except (OSError, ValueError, yaml.YAMLError):
                errors.append(f'{relative}: 读取或格式校验失败，旧索引已停止使用')
    duplicates = {key for key, paths in id_paths.items() if len(paths) > 1}
    ambiguous_paths = database.prepare_document_paths({
        relative: source_id or None
        for _, relative, _, _, _, source_id in notes if source_id not in duplicates
    })
    valid_paths: set[str] = set()
    for path, relative, metadata, body, digest, source_id in notes:
        if source_id in duplicates:
            errors.append(f'{relative}: 记忆 ID 重复，请给每篇笔记分配唯一 ID')
            continue
        if relative in ambiguous_paths:
            errors.append(f'{relative}: 记忆 ID 在旧索引中存在歧义，请分配新的唯一 ID')
            continue
        document_id, was_changed = database.upsert_document(relative, str(metadata.get('title') or path.stem), body, digest, metadata)
        valid_paths.add(relative)
        if was_changed:
            database.replace_document_memories(document_id, _memory_from_document(metadata, body, path))
            changed += 1
        else:
            skipped += 1
    invalidated = database.invalidate_documents(valid_paths)
    result = {'scanned': scanned, 'changed': changed, 'skipped': skipped, 'excluded': excluded,
              'invalidated': invalidated, 'errors': errors}
    database.audit('vault_scan', {**result, 'errors': len(errors)})
    return result


def create_note(vault_dir: Path, database: Database, title: str, content: str, kind: str = 'note') -> dict[str, Any]:
    if kind not in MEMORY_KINDS or not title.strip() or not content.strip():
        raise ValueError('记录标题、正文和类型不能为空或无效')
    note_id = uuid4().hex
    path = safe_note_path(vault_dir, f'90-Inbox/{note_id}.md')
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {'id': note_id, 'type': kind, 'title': title.strip(), 'created_at': utc_now(),
                'source_kind': 'user-authored', 'visibility': 'private'}
    raw = ('---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n\n' + content.strip() + '\n').encode('utf-8')
    if len(raw) > MAX_NOTE_BYTES:
        raise ValueError('记录超出大小限制')
    with path.open('xb') as handle:
        handle.write(raw)
    document_id, _ = database.upsert_document(path.relative_to(vault_dir.resolve()).as_posix(), title.strip(), content.strip(), hashlib.sha256(raw).hexdigest(), metadata)
    database.replace_document_memories(document_id, _memory_from_document(metadata, content.strip(), path))
    database.audit('note_created', {'document_id': document_id})
    return {'document': database.get_document(document_id),
            'memories': [m for m in database.list_memories('candidate') if m['document_id'] == document_id]}
