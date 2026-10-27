"""Live data handbook: the schema the running service actually has, plus boundaries.

Read-only: it reads sqlite_master, PRAGMA table_info and row counts. No note text,
no audit event, no write. The handbook in docs/ is checked against this export by
a test so the two cannot drift apart silently.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .db import Database

SHANGHAI = ZoneInfo('Asia/Shanghai')
FTS_PREFIX = 'documents_fts'
GROUPS: dict[str, set[str]] = {
    '资料与记忆': {'documents', 'memories', 'memory_events', 'memory_usages', 'memory_deletions', 'memory_embeddings'},
    '对话': {'conversations', 'messages', 'harness_sessions'},
    '目标与任务': {'goals', 'tasks', 'task_import_ledger'},
    '每日记录': {'journal_entries', 'journal_actions', 'body_entries', 'inbox_items', 'scheduler_settings', 'fortune_checkins'},
    '习惯与学习': {'habit_definitions', 'habit_entries', 'habit_cache_meta',
                'study_subjects', 'study_sessions'},
    '个人参考': {'bazi_profiles'},
    '建议与提醒': {'suggestions'},
    '系统': {'audit_events', 'schema_migrations',
             'dida_sync_settings', 'dida_task_links', 'dida_project_links'},
}
EXPORT_MEMBERS = [
    'memories.md/json', 'planning.md/json', 'growth.md/json', 'conversations.md/json',
    'journal.md/json', 'body.csv/json', 'README.txt',
]
DEPRECATED_TABLES = {
    'bazi_profiles', 'suggestions', 'journal_entries', 'journal_actions',
    'goals', 'tasks', 'study_subjects', 'study_sessions', 'body_entries',
    'task_import_ledger', 'conversations', 'messages', 'harness_sessions',
    'memory_embeddings',
}


def _group_of(table: str) -> str:
    for name, tables in GROUPS.items():
        if table in tables:
            return name
    return '其他'


def collect_schema(database: Database) -> dict[str, Any]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        names = [row['name'] for row in rows]
        tables = []
        for name in names:
            if name.startswith(FTS_PREFIX):
                continue
            columns = [dict(row) for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()]
            indexes = [dict(row) for row in connection.execute(f'PRAGMA index_list("{name}")').fetchall()]
            count = connection.execute(f'SELECT COUNT(*) AS total FROM "{name}"').fetchone()['total']
            tables.append({
                'name': name,
                'group': _group_of(name),
                'deprecated': name in DEPRECATED_TABLES,
                'rows': count,
                'columns': [
                    {'name': column['name'], 'type': column['type'] or '', 'not_null': bool(column['notnull']),
                     'primary_key': bool(column['pk'])}
                    for column in columns
                ],
                'indexes': sorted(index['name'] for index in indexes if not index['name'].startswith('sqlite_')),
            })
    db_path = Path(database.path)
    groups: dict[str, int] = {}
    for table in tables:
        groups[table['group']] = groups.get(table['group'], 0) + 1
    return {
        'schema': 1,
        'generated_at': datetime.now(SHANGHAI).isoformat(timespec='seconds'),
        'database': {
            'file': db_path.name,
            'bytes': db_path.stat().st_size if db_path.exists() else 0,
            'tables': len(tables),
            'fts_helpers': len(names) - len(tables),
        },
        'groups': groups,
        'deprecated_tables': sorted(DEPRECATED_TABLES),
        'tables': tables,
        'backup_and_export': {
            'backup': '备份是整个 SQLite 文件的一致快照，因此上表全部包含在备份 ZIP 内（另有 Vault 的 Markdown 副本与哈希清单）。',
            'export': f"可读导出包（不是恢复包）只含以下成员：{('、'.join(EXPORT_MEMBERS))}。",
            'vault': 'Markdown 是原始记录：应用只向 90-Inbox 写入新笔记，从不修改你已有的笔记；只读模式下写入全部被拒绝。',
        },
        'recovery': [
            '备份生成后立即校验：路径穿越、哈希清单与 SQLite 完整性都会被检查。',
            '恢复只写入 data/restored 下的独立副本，并在那里再次验证（数据库完整性、外键、必需表 + 文件清单）；不会切换当前库。',
            '“把副本切换为当前库”仍未实现，需要你明确决定后再做。',
        ],
        'note': '本导出只含结构与计数：没有笔记正文、没有问题文本、没有密钥；不写数据库、不写审计。',
    }


def render_schema_markdown(schema: dict[str, Any]) -> str:
    lines = [
        '# 薪火OS 数据手册（运行中服务的实时结构）',
        '',
        f"- 生成时间：{schema['generated_at']}",
        f"- 数据库文件：{schema['database']['file']}（{schema['database']['bytes']} 字节）",
        f"- 数据表：{schema['database']['tables']} 张（另有 {schema['database']['fts_helpers']} 张全文检索辅助表）",
        '',
        '## 表清单',
        '',
        '| 表 | 分组 | 行数 | 列数 | 索引 |',
        '|---|---|---|---|---|',
    ]
    lines += [
        f"| `{table['name']}` | {table['group']} | {table['rows']} | {len(table['columns'])} | {('、'.join(table['indexes']) or '—')} |"
        for table in schema['tables']
    ]
    deprecated = schema.get('deprecated_tables') or []
    if deprecated:
        lines += [
            '',
            '以下表自 0.99 起标记为 deprecated：' + '、'.join(f'`{name}`' for name in deprecated)
            + '。它们只保留历史数据，没有写入方，也不会被自动删除。',
        ]
    lines += ['', '## 列定义', '']
    for table in schema['tables']:
        lines.append(f"### `{table['name']}`")
        lines.append('')
        lines.append('| 列 | 类型 | 非空 | 主键 |')
        lines.append('|---|---|---|---|')
        lines += [
            f"| `{column['name']}` | {column['type'] or '—'} | {'是' if column['not_null'] else '否'} | {'是' if column['primary_key'] else '否'} |"
            for column in table['columns']
        ]
        lines.append('')
    lines += [
        '## 备份、导出与原始记录',
        '',
        f"- 备份：{schema['backup_and_export']['backup']}",
        f"- 导出：{schema['backup_and_export']['export']}",
        f"- Markdown：{schema['backup_and_export']['vault']}",
        '',
        '## 恢复',
        '',
    ]
    lines += [f"{index}. {step}" for index, step in enumerate(schema['recovery'], start=1)]
    lines += ['', f"> {schema['note']}", '']
    return '\n'.join(lines)


def create_schema_router(database: Database, prefix: str = '/api') -> APIRouter:
    router = APIRouter(prefix=prefix, tags=['schema'])

    @router.get('/schema.json')
    def schema_json():
        return collect_schema(database)

    @router.get('/schema.md')
    def schema_markdown():
        body = render_schema_markdown(collect_schema(database))
        return PlainTextResponse(body, media_type='text/markdown; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename="torch-os-data-handbook.md"'})

    return router
