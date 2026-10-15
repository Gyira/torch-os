"""Live schema export: read-only, counts only, and kept in sync with the handbook."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway
from backend.app.schema_export import collect_schema, render_schema_markdown

HANDBOOK = Path(__file__).resolve().parents[1] / 'docs' / 'DATA_HANDBOOK.md'


def build(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return database, client


def test_schema_export_lists_every_table_with_columns_and_counts(tmp_path):
    database, _ = build(tmp_path)
    schema = collect_schema(database)
    names = [table['name'] for table in schema['tables']]
    assert len(names) >= 23
    assert names == sorted(names)
    assert not any(name.startswith('documents_fts') for name in names)
    assert schema['database']['tables'] == len(names)
    assert schema['database']['fts_helpers'] >= 5
    assert schema['database']['bytes'] > 0
    assert set(schema['groups']) == {
        '资料与记忆', '对话', '目标与任务', '每日记录', '习惯与学习', '个人参考', '建议与提醒', '系统'}
    for table in schema['tables']:
        assert table['columns'], table['name']
        assert table['rows'] >= 0
        assert all(column['name'] for column in table['columns'])
    assert '没有笔记正文' in schema['note']
    assert '不会切换当前库' in ' '.join(schema['recovery'])


def test_schema_endpoints_serve_structure_without_writing(tmp_path):
    database, client = build(tmp_path)
    payload = client.get('/api/schema.json')
    assert payload.status_code == 200
    body = payload.json()
    assert body['database']['tables'] >= 23
    assert client.get('/api/config/status').json()['capabilities']['schema_export'] is True

    markdown = client.get('/api/schema.md')
    assert markdown.status_code == 200
    assert markdown.headers['content-type'].startswith('text/markdown')
    assert 'attachment' in markdown.headers['content-disposition']
    assert '## 表清单' in markdown.text
    assert '`memories`' in markdown.text
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0] == 0
    assert database.list_documents() == []


def test_handbook_table_list_matches_the_live_schema(tmp_path):
    database, _ = build(tmp_path)
    schema = collect_schema(database)
    live = {table['name'] for table in schema['tables']}
    text = HANDBOOK.read_text(encoding='utf-8')
    documented = set()
    for first_cell in re.findall(r'^\| ([^|]+) \|', text, flags=re.MULTILINE):
        documented.update(re.findall(r'`([a-z_][a-z_0-9]*)`', first_cell))
    documented = {name for name in documented if not name.startswith('documents_fts')}
    assert documented == live, f"手册缺少：{sorted(live - documented)}；手册多出：{sorted(documented - live)}"


def test_rendered_markdown_matches_the_schema_it_came_from(tmp_path):
    database, _ = build(tmp_path)
    schema = collect_schema(database)
    rendered = render_schema_markdown(schema)
    for table in schema['tables']:
        assert f"| `{table['name']}` | {table['group']} |" in rendered
    assert rendered.startswith('# 薪火OS 数据手册')
    assert '## 恢复' in rendered
