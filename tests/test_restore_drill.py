"""One-click restore drill: fresh backup → independent copy → read-only verify → report."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return database, client


def counts(database):
    with database.connect() as connection:
        documents = connection.execute('SELECT COUNT(*) AS n FROM documents').fetchone()['n']
        memories = connection.execute('SELECT COUNT(*) AS n FROM memories').fetchone()['n']
    return {'documents': documents, 'memories': memories}


def test_drill_requires_confirmation_and_writes_a_report(tmp_path):
    database, client = build(tmp_path)
    client.post('/api/notes', json={'title': '演练笔记', 'content': '我用来验证恢复演练的内容。', 'type': 'note'})
    before = counts(database)

    refused = client.post('/api/backups/drill', json={'confirm': False})
    assert refused.status_code == 422
    assert client.get('/api/backups/drill/last.md').status_code == 404

    result = client.post('/api/backups/drill', json={'confirm': True})
    assert result.status_code == 201, result.text
    payload = result.json()
    assert payload['ok'] is True
    assert payload['backup']['verified'] is True
    assert payload['verification']['sqlite_integrity'] == 'ok'
    assert payload['verification']['vault_files'] >= 1
    assert Path(payload['report_path']).is_file()
    assert '不会覆盖当前数据' in payload['note'] or '没有覆盖当前库' in payload['note']
    assert counts(database) == before

    report = client.get('/api/backups/drill/last.md')
    assert report.status_code == 200
    assert report.headers['content-type'].startswith('text/markdown')
    assert 'attachment' in report.headers['content-disposition']
    assert '恢复演练报告（一键流程）' in report.text
    assert 'SQLite integrity=ok' in report.text
    assert '不覆盖当前库' in report.text
    assert '把副本切换为当前库' in report.text

    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='restore_drill'").fetchall()]
    assert len(rows) == 1
    detail = json.loads(rows[0]['detail_json'])
    assert detail['ok'] is True and detail['vault_files'] >= 1


def test_each_drill_adds_one_copy_and_keeps_the_previous_report_readable(tmp_path):
    database, client = build(tmp_path)
    first = client.post('/api/backups/drill', json={'confirm': True}).json()
    second = client.post('/api/backups/drill', json={'confirm': True}).json()
    assert first['copy']['target_dir'] != second['copy']['target_dir']
    copies = client.get('/api/backups/restored').json()
    names = {item['name'] for item in copies['copies']} if isinstance(copies, dict) else set()
    assert len(names) >= 2
    assert client.get('/api/backups/drill/last.md').status_code == 200
    with database.connect() as connection:
        drills = connection.execute("SELECT COUNT(*) AS n FROM audit_events WHERE event_type='restore_drill'").fetchone()['n']
    assert drills == 2


def test_drill_reports_are_listed_and_individually_downloadable(tmp_path):
    database, client = build(tmp_path)
    empty = client.get('/api/backups/drill/reports').json()
    assert empty['count'] == 0 and '还没有演练报告' in empty['note']

    first = client.post('/api/backups/drill', json={'confirm': True}).json()
    second = client.post('/api/backups/drill', json={'confirm': True}).json()
    listing = client.get('/api/backups/drill/reports').json()
    assert listing['count'] == 2
    assert '只增不减' in listing['note']
    names = [item['name'] for item in listing['reports']]
    assert names == sorted(names, reverse=True)
    assert all(name.startswith('restore-drill-') and name.endswith('.md') for name in names)
    assert all(item['bytes'] > 0 for item in listing['reports'])

    older = client.get(f"/api/backups/drill/reports/{names[-1]}")
    assert older.status_code == 200
    assert '恢复演练报告（一键流程）' in older.text
    assert 'attachment' in older.headers['content-disposition']
    assert Path(first['report_path']).name in names and Path(second['report_path']).name in names
    assert client.get('/api/backups/drill/reports/..%2Fsecret.md').status_code in (400, 404)
    assert client.get('/api/backups/drill/reports/notes.md').status_code == 400
    assert client.get('/api/backups/drill/reports/restore-drill-99999999T999999999999.md').status_code == 404
