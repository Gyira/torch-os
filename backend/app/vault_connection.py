"""Point the app at an Obsidian vault, inspect it, and keep writes optional.

Read-only mode keeps every write path out of the vault while scanning keeps
working. The scan preview never writes: it walks the vault with the same rules
as the real scan and reports what *would* change (new / changed / unchanged /
missing) so a real vault can be inspected before anything is indexed.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .db import Database
from .vault import EXCLUDED_DIRECTORIES, MAX_NOTE_BYTES, parse_text, restricted_path, safe_note_path

READ_ONLY_ENV = 'VAULT_READ_ONLY'
SHANGHAI = ZoneInfo('Asia/Shanghai')
TRUTHY = {'1', 'true', 'yes', 'on'}
MAX_PREVIEW_FILES = 5000


class VaultReadOnlyError(RuntimeError):
    """Raised when a write is attempted while the vault is in read-only mode."""


def read_only_enabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(READ_ONLY_ENV, '')).strip().casefold() in TRUTHY


def ensure_vault_writable(read_only: bool, action: str) -> None:
    if read_only:
        raise VaultReadOnlyError(f'当前是 Obsidian 只读模式，已拒绝“{action}”；如需写入请先在设置里关闭只读模式并重启服务')


def normalize_vault_path(value: str) -> Path:
    text = str(value or '').strip().strip('"').strip("'")
    if not text:
        raise ValueError('请填写 Obsidian 库目录')
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError('请填写完整的绝对路径，例如 D:\\Obsidian\\MyVault')
    if not path.exists():
        raise ValueError('目录不存在，请检查路径后重试')
    if not path.is_dir():
        raise ValueError('这个路径不是文件夹')
    return path.resolve()


def vault_write_guard(read_only: bool):
    """Small callable the write routers use to refuse writes in read-only mode."""
    def guard(action: str) -> None:
        ensure_vault_writable(read_only, action)
    return guard


def _walk_markdown(root: Path, limit: int = MAX_PREVIEW_FILES):
    """Yield (path, relative, size) for every Markdown file the scan would look at."""
    seen = 0
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = [name for name in sorted(subdirs)
                      if not restricted_path((Path(directory) / name).relative_to(root))
                      and not (Path(directory) / name).is_symlink()
                      and not (hasattr(Path(directory) / name, 'is_junction') and (Path(directory) / name).is_junction())]
        for filename in sorted(filenames):
            if not filename.lower().endswith('.md'):
                continue
            path = Path(directory) / filename
            relative = path.relative_to(root)
            if restricted_path(relative):
                continue
            seen += 1
            if seen > limit:
                return
            yield path, relative.as_posix(), path.stat().st_size


def describe_vault(vault_dir: Path, *, read_only: bool | None = None, samples: int = 5) -> dict[str, Any]:
    root = Path(vault_dir)
    read_only = read_only_enabled() if read_only is None else bool(read_only)
    payload: dict[str, Any] = {
        'path': str(root),
        'exists': root.is_dir(),
        'looks_like_obsidian': (root / '.obsidian').is_dir(),
        'read_only': read_only,
        'markdown_files': 0,
        'total_bytes': 0,
        'truncated': False,
        'sample_paths': [],
        'skipped_directories': [],
        'writable': False,
        'note': '只读模式下扫描与检索照常，任何写入（新建笔记、提炼笔记、导入）都会被拒绝。',
    }
    if not root.is_dir():
        payload['note'] = '目录不存在或不是文件夹；指向一个 Obsidian 库（含 .obsidian 文件夹）即可。'
        return payload
    skipped: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not entry.is_dir():
            continue
        if entry.name.startswith('.') or entry.name.casefold() in EXCLUDED_DIRECTORIES:
            skipped.append(entry.name)
    payload['skipped_directories'] = skipped[:10]
    count = 0
    total = 0
    for _, relative, size in _walk_markdown(root):
        count += 1
        total += size
        if len(payload['sample_paths']) < samples:
            payload['sample_paths'].append(relative)
    payload['markdown_files'] = count
    payload['total_bytes'] = total
    payload['truncated'] = count >= MAX_PREVIEW_FILES
    payload['writable'] = not read_only and os.access(root, os.W_OK)
    return payload


def preview_scan(database: Database, vault_dir: Path, *, limit: int = MAX_PREVIEW_FILES) -> dict[str, Any]:
    """Read-only diff against the index: never writes to the vault or the database."""
    root = Path(vault_dir).resolve()
    known = database.document_digests()
    found: dict[str, str] = {}
    new = changed = unchanged = excluded = unreadable = 0
    oversized = 0
    with_id = 0
    top_level: dict[str, int] = {}
    id_paths: dict[str, list[str]] = {}
    samples: dict[str, list[str]] = {'new': [], 'changed': [], 'unreadable': [], 'oversized': [], 'restricted': []}
    for path, relative, size in _walk_markdown(root, limit):
        bucket = relative.split('/', 1)[0] if '/' in relative else '(根目录)'
        top_level[bucket] = top_level.get(bucket, 0) + 1
        try:
            if size > MAX_NOTE_BYTES:
                oversized += 1
                if len(samples['oversized']) < 5:
                    samples['oversized'].append(relative)
                raise ValueError('超过 1 MiB')
            raw = safe_note_path(root, relative).read_bytes()
            metadata, _ = parse_text(raw.decode('utf-8-sig'))
            if any(str(metadata.get(key, 'personal')).lower() not in {'personal', 'private', 'public', 'publishable', ''}
                   for key in ('scope', 'memory_scope', 'visibility', 'sensitivity')) or metadata.get('type') == 'constitution':
                excluded += 1
                if len(samples['restricted']) < 5:
                    samples['restricted'].append(relative)
                continue
            source_id = str(metadata.get('id') or '').strip()
            if source_id:
                with_id += 1
                id_paths.setdefault(source_id, []).append(relative)
            digest = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, yaml.YAMLError):
            unreadable += 1
            if len(samples['unreadable']) < 5:
                samples['unreadable'].append(relative)
            continue
        found[relative] = digest
        if relative not in known:
            new += 1
            if len(samples['new']) < 5:
                samples['new'].append(relative)
        elif known[relative] != digest:
            changed += 1
            if len(samples['changed']) < 5:
                samples['changed'].append(relative)
        else:
            unchanged += 1
    missing = [path for path in known if path not in found]
    duplicates = sorted(source_id for source_id, paths in id_paths.items() if len(paths) > 1)
    return {
        'path': str(root),
        'scanned': len(found),
        'new': new,
        'changed': changed,
        'unchanged': unchanged,
        'missing': len(missing),
        'excluded': excluded,
        'unreadable': unreadable,
        'oversized': oversized,
        'with_source_id': with_id,
        'without_source_id': len(found) - with_id,
        'duplicate_ids': duplicates[:10],
        'duplicate_id_count': len(duplicates),
        'top_level': dict(sorted(top_level.items(), key=lambda item: (-item[1], item[0]))[:10]),
        'would_index': new + changed,
        'samples': {**samples, 'missing': missing[:5]},
        'truncated': len(found) >= limit,
        'note': '这是只读预览：没有写入数据库，也没有改动任何文件。真正扫描时才会更新索引（原文件从不修改）。',
    }


def onboarding_report(database: Database, vault_dir: Path, *, read_only: bool | None = None) -> dict[str, Any]:
    """Everything a first connection needs to know, computed without writing anything."""
    connection = describe_vault(vault_dir, read_only=read_only)
    diff = preview_scan(database, vault_dir)
    checks = [
        {'key': 'path', 'level': 'ok' if connection['exists'] else 'error',
         'label': '目录存在', 'detail': '路径可读' if connection['exists'] else '目录不存在或不是文件夹'},
        {'key': 'obsidian', 'level': 'ok' if connection['looks_like_obsidian'] else 'info',
         'label': 'Obsidian 库标记', 'detail': '找到 .obsidian 文件夹' if connection['looks_like_obsidian'] else '没有 .obsidian，仍可按普通 Markdown 目录接入'},
        {'key': 'read_only', 'level': 'ok' if connection['read_only'] else 'warn',
         'label': '只读安全模式', 'detail': '开启：任何写入都会被拒绝' if connection['read_only'] else '未开启：应用可向 90-Inbox 写入新笔记'},
        {'key': 'duplicates', 'level': 'warn' if diff['duplicate_id_count'] else 'ok',
         'label': '重复记忆 ID', 'detail': f"{diff['duplicate_id_count']} 组重复：{('、'.join(diff['duplicate_ids'])) if diff['duplicate_ids'] else '没有重复'}"},
        {'key': 'oversized', 'level': 'warn' if diff['oversized'] else 'ok',
         'label': '超大文件', 'detail': f"{diff['oversized']} 篇超过 1 MiB 会被跳过" if diff['oversized'] else '没有超大文件'},
        {'key': 'restricted', 'level': 'info' if diff['excluded'] else 'ok',
         'label': '按隐私规则跳过', 'detail': f"{diff['excluded']} 篇声明了非个人范围或是 constitution；目录级跳过：{('、'.join(connection['skipped_directories']) or '无')}"},
        {'key': 'unreadable', 'level': 'warn' if diff['unreadable'] else 'ok',
         'label': '无法读取', 'detail': f"{diff['unreadable']} 篇读取或格式校验失败" if diff['unreadable'] else '全部可读'},
        {'key': 'index', 'level': 'info' if diff['would_index'] else 'ok',
         'label': '本次会索引', 'detail': f"{diff['would_index']} 篇（新增 {diff['new']}、变化 {diff['changed']}）；索引里已消失 {diff['missing']} 篇"},
    ]
    return {
        'schema': 1,
        'generated_at': datetime.now(SHANGHAI).isoformat(timespec='seconds'),
        'connection': connection,
        'diff': diff,
        'checks': checks,
        'next_steps': [
            '先确认“只读安全模式”为开启状态，这样扫描与问答可用、任何写入都会被拒绝。',
            '点“运行扫描”把索引建起来；原文件不会被修改，候选记忆仍需你在“记忆”页逐条确认。',
            '想写作时再关闭只读模式；应用只会写入 90-Inbox，不会改动你已有的笔记。',
        ],
        'note': '报告只统计路径、大小与哈希；不包含任何笔记正文，也不写入数据库或文件。',
    }


def render_onboarding_markdown(report: dict[str, Any]) -> str:
    connection = report['connection']
    diff = report['diff']
    lines = [
        '# Obsidian 库接入演练报告（只读）',
        '',
        f"- 生成时间：{report['generated_at']}",
        f"- 库路径：{connection['path']}",
        f"- 是否 Obsidian 库：{'是' if connection['looks_like_obsidian'] else '否（按普通 Markdown 目录接入）'}",
        f"- 读写模式：{'只读安全模式' if connection['read_only'] else '可写（会写入 90-Inbox）'}",
        f"- Markdown 篇数：{connection['markdown_files']}，合计 {connection['total_bytes']} 字节",
        f"- 跳过的目录：{'、'.join(connection['skipped_directories']) or '无'}",
        '',
        '## 体检结果',
        '',
    ]
    lines += [f"- [{item['level']}] {item['label']}：{item['detail']}" for item in report['checks']]
    lines += [
        '',
        '## 本次扫描会做什么',
        '',
        f"- 新增 {diff['new']}、变化 {diff['changed']}、未变 {diff['unchanged']}、索引里已消失 {diff['missing']}",
        f"- 按隐私规则跳过 {diff['excluded']}、超大 {diff['oversized']}、无法读取 {diff['unreadable']}",
        f"- 有来源 ID 的 {diff['with_source_id']} 篇、无 ID 的 {diff['without_source_id']} 篇",
        f"- 真正扫描时会索引 {diff['would_index']} 篇",
        '',
        '## 目录分布（前 10）',
        '',
    ]
    lines += [f"- {name}：{count} 篇" for name, count in diff['top_level'].items()] or ['- （空）']
    lines += [
        '',
        '## 建议步骤',
        '',
    ]
    lines += [f"{index}. {step}" for index, step in enumerate(report['next_steps'], start=1)]
    lines += ['', f"> {report['note']}", '']
    return '\n'.join(lines)
