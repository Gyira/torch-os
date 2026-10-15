"""Obsidian vault connection: describe, read-only preview, .env write, write guards."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.conversation_memory import ConversationMemoryService
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway
from backend.app.share_import import ShareImportService
from backend.app.vault_connection import (
    VaultReadOnlyError,
    describe_vault,
    normalize_vault_path,
    onboarding_report,
    preview_scan,
    read_only_enabled,
    render_onboarding_markdown,
)


def make_app(tmp_path, *, environ=None, runtime_label='personal', vault=None):
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, vault or (tmp_path / 'vault'), gateway,
                     security_environ={} if environ is None else environ,
                     runtime_label=runtime_label, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return database, client


def write_note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def build_vault(tmp_path) -> Path:
    vault = tmp_path / 'MyVault'
    (vault / '.obsidian').mkdir(parents=True)
    (vault / '.trash').mkdir(parents=True)
    write_note(vault / '.obsidian' / 'config.md', '# 插件配置\n')
    write_note(vault / '.trash' / 'old.md', '被删除的旧笔记\n')
    write_note(vault / 'Notes' / 'a.md', '我习惯早上写日记。\n')
    write_note(vault / 'Notes' / 'b.md', '我在学习英语。\n')
    write_note(vault / 'README.txt', '不是 Markdown\n')
    return vault


def test_describe_vault_reports_shape_and_skips_dot_directories(tmp_path):
    vault = build_vault(tmp_path)
    payload = describe_vault(vault, read_only=False)
    assert payload['exists'] is True
    assert payload['looks_like_obsidian'] is True
    assert payload['markdown_files'] == 2
    assert payload['read_only'] is False
    assert payload['writable'] is True
    assert sorted(payload['sample_paths']) == ['Notes/a.md', 'Notes/b.md']
    assert '.obsidian' in payload['skipped_directories']
    assert '.trash' in payload['skipped_directories']

    read_only = describe_vault(vault, read_only=True)
    assert read_only['writable'] is False
    assert read_only['markdown_files'] == 2

    missing = describe_vault(tmp_path / 'nope', read_only=True)
    assert missing['exists'] is False
    assert missing['markdown_files'] == 0


def test_normalize_vault_path_and_read_only_flag(tmp_path):
    vault = build_vault(tmp_path)
    assert normalize_vault_path(f'  "{vault}"  ') == vault.resolve()
    with pytest.raises(ValueError):
        normalize_vault_path('')
    with pytest.raises(ValueError):
        normalize_vault_path('relative/vault')
    with pytest.raises(ValueError):
        normalize_vault_path(str(tmp_path / 'missing'))
    with pytest.raises(ValueError):
        normalize_vault_path(str(vault / 'Notes' / 'a.md'))
    assert read_only_enabled({'VAULT_READ_ONLY': '1'}) is True
    assert read_only_enabled({'VAULT_READ_ONLY': 'off'}) is False
    assert read_only_enabled({}) is False


def test_scan_preview_classifies_without_writing(tmp_path):
    vault = build_vault(tmp_path)
    database = Database(tmp_path / 'preview.sqlite3')
    database.initialize()
    first = preview_scan(database, vault)
    assert first['new'] == 2
    assert first['would_index'] == 2
    assert first['missing'] == 0
    assert first['samples']['new'] == ['Notes/a.md', 'Notes/b.md']
    assert database.list_documents() == []

    body = '我习惯早上写日记。'
    raw = f'# 日记\n\n{body}\n'.encode('utf-8')
    import hashlib

    database.upsert_document('Notes/a.md', '日记', body, hashlib.sha256(raw).hexdigest(), {})
    second = preview_scan(database, vault)
    assert second['new'] == 1
    assert second['changed'] == 1
    assert second['unchanged'] == 0

    write_note(vault / 'Notes' / 'a.md', '我改成晚上写日记了。\n')
    database.upsert_document('Notes/a.md', '日记', '我改成晚上写日记了。',
                             hashlib.sha256((vault / 'Notes' / 'a.md').read_bytes()).hexdigest(), {})
    third = preview_scan(database, vault)
    assert third['new'] == 1
    assert third['unchanged'] == 1
    assert third['would_index'] == 1

    database.upsert_document('Notes/b.md', '英语', '我在学习英语。',
                             hashlib.sha256((vault / 'Notes' / 'b.md').read_bytes()).hexdigest(), {})
    (vault / 'Notes' / 'b.md').unlink()
    fourth = preview_scan(database, vault)
    assert fourth['missing'] == 1
    assert fourth['samples']['missing'] == ['Notes/b.md']
    assert fourth['note'].startswith('这是只读预览')
    assert database.list_documents()


def test_scan_preview_reports_duplicates_oversized_restricted_and_folders(tmp_path):
    vault = tmp_path / 'RiskyVault'
    (vault / '.obsidian').mkdir(parents=True)
    write_note(vault / '01-Notes' / 'a.md', '---\nid: dup-1\ntitle: 甲\n---\n\n我在练习写作，每周三篇。\n')
    write_note(vault / '02-Area' / 'b.md', '---\nid: dup-1\ntitle: 乙\n---\n\n我习惯早上写日记。\n')
    write_note(vault / '03-Rel' / 'r.md', '---\nid: rel-1\nscope: relationship\n---\n\n私人关系内容。\n')
    write_note(vault / '04-Huge' / 'h.md', '---\nid: big-1\n---\n\n' + '甲' * 1_100_000)
    database = Database(tmp_path / 'risky.sqlite3')
    database.initialize()
    payload = preview_scan(database, vault)
    assert payload['duplicate_id_count'] == 1
    assert payload['duplicate_ids'] == ['dup-1']
    assert payload['oversized'] == 1
    assert payload['excluded'] == 1
    assert payload['samples']['restricted'] == ['03-Rel/r.md']
    # 超大文件在读取前就被跳过，它的 id 不会进入统计。
    assert payload['with_source_id'] == 2
    assert payload['without_source_id'] == 0
    assert payload['top_level']['01-Notes'] == 1
    assert list(payload['top_level'])[:2] == ['01-Notes', '02-Area']
    assert database.list_documents() == []


def test_onboarding_report_and_markdown_never_include_note_text(tmp_path):
    vault = build_vault(tmp_path)
    secret = '机密正文不应出现在报告里'
    write_note(vault / 'Notes' / 'secret.md', f'# 私密\n\n{secret}\n')
    database = Database(tmp_path / 'onboarding.sqlite3')
    database.initialize()
    report = onboarding_report(database, vault, read_only=True)
    assert report['schema'] == 1
    assert report['connection']['read_only'] is True
    assert [item['key'] for item in report['checks']] == [
        'path', 'obsidian', 'read_only', 'duplicates', 'oversized', 'restricted', 'unreadable', 'index']
    assert report['diff']['would_index'] == 3
    assert '不包含任何笔记正文' in report['note']
    assert len(report['next_steps']) == 3
    rendered = render_onboarding_markdown(report)
    assert secret not in rendered
    assert '# Obsidian 库接入演练报告（只读）' in rendered
    assert '只读安全模式' in rendered
    assert '真正扫描时会索引 3 篇' in rendered
    assert database.list_documents() == []


def test_onboarding_endpoints_serve_json_and_markdown_without_writing(tmp_path):
    vault = build_vault(tmp_path)
    write_note(vault / 'Notes' / 'secret.md', '# 私密\n\n机密正文不应出现在报告里\n')
    database, client = make_app(tmp_path, environ={'VAULT_READ_ONLY': '1'}, vault=vault)
    payload = client.get('/api/vault/onboarding.json')
    assert payload.status_code == 200
    body = payload.json()
    assert body['connection']['read_only'] is True
    assert body['diff']['would_index'] == 3
    assert '机密正文' not in payload.text
    markdown = client.get('/api/vault/onboarding.md')
    assert markdown.status_code == 200
    assert markdown.headers['content-type'].startswith('text/markdown')
    assert 'attachment' in markdown.headers['content-disposition']
    assert '机密正文' not in markdown.text
    assert '## 建议步骤' in markdown.text
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0] == 0
    assert database.list_documents() == []


def test_connection_endpoint_writes_env_without_storing_the_path_in_audit(tmp_path):
    vault = build_vault(tmp_path)
    database, client = make_app(tmp_path)
    original = 'MODEL_PROVIDER=local\n'
    (tmp_path / '.env').write_text(original, encoding='utf-8')

    described = client.get('/api/vault/connection')
    assert described.status_code == 200
    assert described.json()['exists'] is True

    saved = client.post('/api/vault/connection', json={'path': str(vault), 'read_only': True})
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload['saved'] is True
    assert payload['restart_required'] is True
    assert payload['read_only'] is True
    assert payload['looks_like_obsidian'] is True
    env_text = (tmp_path / '.env').read_text(encoding='utf-8')
    assert 'MODEL_PROVIDER=local' in env_text
    assert f'VAULT_DIR={vault.resolve()}' in env_text
    assert 'VAULT_READ_ONLY=1' in env_text

    with database.connect() as connection:
        rows = connection.execute("SELECT detail_json FROM audit_events WHERE event_type='vault_connection_updated'").fetchall()
    assert len(rows) == 1
    detail = json.loads(rows[0]['detail_json'])
    assert detail['read_only'] is True
    assert detail['looks_like_obsidian'] is True
    assert str(vault) not in rows[0]['detail_json']

    invalid = client.post('/api/vault/connection', json={'path': str(tmp_path / 'missing'), 'read_only': True})
    assert invalid.status_code == 422
    assert '目录不存在' in invalid.json()['detail']


def test_qa_runtime_refuses_to_change_the_connection(tmp_path):
    vault = build_vault(tmp_path)
    _, client = make_app(tmp_path, runtime_label='qa')
    refused = client.post('/api/vault/connection', json={'path': str(vault), 'read_only': True})
    assert refused.status_code == 409
    assert '隔离验收环境' in refused.json()['detail']


def test_read_only_mode_refuses_every_vault_write(tmp_path):
    vault = build_vault(tmp_path)
    database, client = make_app(tmp_path, environ={'VAULT_READ_ONLY': '1'}, vault=vault)
    status = client.get('/api/config/status').json()
    assert status['capabilities']['vault_connection'] is True
    assert client.get('/api/vault/connection').json()['read_only'] is True

    created = client.post('/api/notes', json={'title': '不该写入', 'content': '只读模式测试。', 'type': 'note'})
    assert created.status_code == 409
    assert '只读模式' in created.json()['detail']
    assert not list(vault.rglob('*.md')) or all(
        path.relative_to(vault).as_posix().startswith(('Notes/', '.obsidian/', '.trash/')) for path in vault.rglob('*.md'))

    conversation_id = database.create_conversation('只读对话')
    database.add_message(conversation_id, 'user', '我计划每周跑三次步。', [])
    service = ConversationMemoryService(database, vault, read_only=True)
    with pytest.raises(VaultReadOnlyError):
        service.apply(conversation_id)

    payload = {'code': 0, 'data': {'biz_data': {'title': '只读分享', 'model_type': 'default', 'messages': [
        {'message_id': 1, 'role': 'USER', 'inserted_at': 1790000000.0,
         'fragments': [{'id': 1, 'type': 'REQUEST', 'content': '我在练习写作。'}]},
    ]}}}
    share_service = ShareImportService(database, vault, fetcher=lambda share_id: payload, read_only=True)
    with pytest.raises(VaultReadOnlyError):
        share_service.apply('read-only-share')
    assert database.list_memories() == []
