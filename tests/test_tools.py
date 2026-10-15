"""Read-only agent tools: shapes, guards, counts and count-only audits."""

from datetime import date

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.tools.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.journal.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.habits.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.learning.shanghai_today', lambda: date(2026, 10, 4))
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return app, database, client


def events(database, event_type=None):
    with database.connect() as connection:
        if event_type:
            rows = connection.execute('SELECT * FROM audit_events WHERE event_type=? ORDER BY id', (event_type,)).fetchall()
        else:
            rows = connection.execute('SELECT * FROM audit_events ORDER BY id').fetchall()
    return [dict(row) for row in rows]


def test_catalog_lists_only_read_only_get_tools():
    from backend.app.tools import tool_catalog

    catalog = tool_catalog()
    assert catalog['read_only'] is True
    names = [tool['name'] for tool in catalog['tools']]
    assert names == ['today', 'memories', 'goals', 'habits', 'learning', 'journal', 'profile', 'vault',
                     'body', 'documents', 'imports', 'vault_audit', 'identity']
    for tool in catalog['tools']:
        assert tool['method'] == 'GET'
        assert tool['path'].startswith('/api/tools')
        assert tool['summary']
    assert '永远不会' in catalog['note']


def test_every_tool_answers_on_an_empty_database_and_audits_counts(tmp_path, monkeypatch):
    app, database, client = build(tmp_path, monkeypatch)
    catalog = client.get('/api/tools').json()
    assert catalog['count'] == 13

    endpoints = [
        '/api/tools/today',
        '/api/tools/memories?q=我的目标',
        '/api/tools/goals',
        '/api/tools/habits?days=30',
        '/api/tools/learning?days=7',
        '/api/tools/journal?from=2026-09-30&to=2026-10-04',
        '/api/tools/profile',
        '/api/tools/vault',
        '/api/tools/body?days=7',
        '/api/tools/documents?limit=3',
        '/api/tools/imports',
        '/api/tools/vault_audit',
    ]
    for endpoint in endpoints:
        response = client.get(endpoint)
        assert response.status_code == 200, f'{endpoint} -> {response.status_code} {response.text}'

    logged = events(database, 'tool_called')
    assert [event['detail_json'] for event in logged].count('{"tool": "vault", "items": null}') == 1
    tools_called = [event['detail_json'] for event in logged]
    assert any('"tool": "today"' in detail for detail in tools_called)
    assert any('"tool": "memories"' in detail for detail in tools_called)
    # No tool endpoint may exist for writes.
    write_paths = [route.path for route in app.routes if getattr(route, 'path', '').startswith('/api/tools')
                   and 'GET' not in getattr(route, 'methods', set())]
    assert write_paths == []


def test_imports_tool_counts_packages_notes_history_and_stale_index(tmp_path, monkeypatch):
    app, database, client = build(tmp_path, monkeypatch)
    imports_dir = tmp_path / 'imports'
    imports_dir.mkdir()
    (imports_dir / 'export.zip').write_bytes(b'not-a-real-zip')
    vault = tmp_path / 'vault'
    note_dir = vault / '80-Wolai'
    note_dir.mkdir(parents=True)
    (note_dir / 'wolai_demo.md').write_text('demo', encoding='utf-8')
    history = vault / '.wolai-import-history' / 'wolai_demo'
    history.mkdir(parents=True)
    (history / ('a' * 64 + '.md')).write_text('old', encoding='utf-8')
    (history / ('b' * 64 + '.md')).write_text('older', encoding='utf-8')
    (history / 'ignore.txt').write_text('x', encoding='utf-8')
    attachments = note_dir / 'attachments'
    attachments.mkdir()
    (attachments / 'abc-pic.png').write_bytes(b'1234')
    (attachments / 'def-note.txt').write_bytes(b'123456')

    payload = client.get('/api/tools/imports').json()
    assert payload['packages'] == 1
    assert payload['imported_notes'] == 1
    assert payload['history_versions'] == 2
    assert payload['notes_index_stale'] == 1  # 从未扫描 → 索引里没有这篇
    assert payload['attachment_files'] == 2 and payload['attachment_bytes'] == 10
    assert payload['generated_at'] == '2026-10-04'
    assert '不含导入包名' in payload['note']
    # 计数审计只记工具名与条目数，不记文件名。
    logged = events(database, 'tool_called')
    detail = logged[-1]['detail_json']
    assert '"tool": "imports"' in detail and '"items": 1' in detail
    assert 'wolai_demo' not in detail and 'abc-pic' not in detail


def test_today_reports_structure_and_marks_missing_days(tmp_path, monkeypatch):
    _, database, client = build(tmp_path, monkeypatch)
    payload = client.get('/api/tools/today').json()
    assert payload['date'] == '2026-10-04'
    assert payload['journal']['recorded'] is False
    assert payload['tasks'] == {'open': 0, 'due_today': 0, 'overdue': 0, 'due_titles': []}
    assert payload['habits'] == []
    assert payload['study'] == {'minutes': 0, 'sessions': 0}
    assert payload['body']['recorded'] is False
    assert payload['inbox_drafts'] == 0
    assert '未记录' in payload['note'] or '不代表' in payload['note']

    saved = client.put('/api/journal/2026-10-04', json={
        'revision': 0, 'content': '今天在整理项目结构。', 'highlights': '想清楚了一件事',
        'obstacles': '', 'next_action': '写数据手册', 'mood': '平稳', 'diet_summary': '',
    })
    assert saved.status_code == 200, saved.text
    again = client.get('/api/tools/today').json()
    assert again['journal']['recorded'] is True
    assert again['journal']['next_action'] == '写数据手册'
    assert again['journal']['content'] == '今天在整理项目结构。'


def test_memories_tool_only_returns_confirmed_and_valid_items(tmp_path, monkeypatch):
    _, _, client = build(tmp_path, monkeypatch)
    note = client.post('/api/notes', json={'title': '跑步习惯', 'content': '我计划每周跑步三次。', 'type': 'preference'}).json()
    memory_id = note['memories'][0]['id']
    pending = client.get('/api/tools/memories?q=跑步').json()
    assert pending['count'] == 0
    assert client.patch(f'/api/memories/{memory_id}/status', json={'status': 'active'}).status_code == 200
    found = client.get('/api/tools/memories?q=跑步').json()
    assert found['count'] == 1
    item = found['memories'][0]
    assert item['content'] == '我计划每周跑步三次。'
    assert item['source_path']
    assert item['low_confidence'] in (True, False)
    assert '已确认' in found['note']
    assert client.get('/api/tools/memories?q=').status_code == 422
    assert client.get('/api/tools/memories?q=跑步&limit=99').status_code == 422


def test_goal_habit_and_learning_tools_report_counts(tmp_path, monkeypatch):
    _, _, client = build(tmp_path, monkeypatch)
    goal = client.post('/api/goals', json={'title': '完成数据手册', 'category': 'project'}).json()
    client.post('/api/tasks', json={'title': '写表清单', 'goal_id': goal['id'], 'due_date': '2026-10-04'})
    client.post('/api/tasks', json={'title': '以后再做'})

    goals = client.get('/api/tools/goals').json()
    assert goals['goals'][0]['title'] == '完成数据手册'
    assert goals['open_task_count'] == 2
    assert goals['next_tasks'][0]['due_date'] == '2026-10-04'

    habit = client.post('/api/habits', json={
        'title': '早睡', 'cue': '洗漱后', 'minimum_action': '关灯',
        'start_date': '2026-10-01', 'weekdays': [0, 1, 2, 3, 4, 5, 6],
    }).json()
    client.put(f"/api/habits/{habit['id']}/entries/2026-10-04", json={'status': 'done', 'revision': 0})
    habits = client.get('/api/tools/habits?days=30').json()
    assert habits['habits'][0]['done'] >= 1
    assert '备注' in habits['note']

    subject = client.post('/api/learning/subjects', json={'title': '数学', 'weekly_target_minutes': 300}).json()
    created = client.post('/api/learning/sessions', json={
        'subject_id': subject['id'], 'study_date': '2026-10-04', 'minutes': 45,
        'request_key': '11111111-2222-4333-8444-555555555555',
    })
    assert created.status_code == 200, created.text
    learning = client.get('/api/tools/learning?days=7').json()
    assert learning['total_minutes'] == 45
    assert learning['by_subject'] == [{'title': '数学', 'minutes': 45}]
    assert client.get('/api/tools/learning?days=3').status_code == 422


def test_journal_and_vault_tools_never_leak_note_text(tmp_path, monkeypatch):
    _, database, client = build(tmp_path, monkeypatch)
    client.put('/api/journal/2026-10-04', json={
        'revision': 0, 'content': '机密正文不应出现在工具输出里', 'next_action': '明天继续',
    })
    journal = client.get('/api/tools/journal?from=2026-09-30&to=2026-10-04').json()
    assert journal['days'][0]['date'] == '2026-10-04'
    assert journal['days'][0]['has_next_step'] is True
    assert '机密正文' not in str(journal)
    assert client.get('/api/tools/journal?from=2026-10-04&to=2026-10-01').status_code == 422
    assert client.get('/api/tools/journal?from=2026-01-01&to=2026-10-04').status_code == 422

    vault = client.get('/api/tools/vault').json()
    assert vault['exists'] is True
    assert len(vault['sample_paths']) <= 3
    assert vault['read_only'] is False

    profile = client.get('/api/tools/profile').json()
    assert set(profile) >= {'confirmed_memories', 'pending_candidates', 'documents', 'active_goals', 'open_tasks'}


def test_body_documents_and_audit_tools_stay_summary_only(tmp_path, monkeypatch):
    _, _, client = build(tmp_path, monkeypatch)
    client.put('/api/body/entries/2026-10-04', json={'revision': 0, 'sleep_minutes': 420, 'exercise_minutes': 30, 'weight_kg': 70.5})
    body = client.get('/api/tools/body?days=7').json()
    assert body['recorded_days'] == 1
    assert body['sleep_minutes_total'] == 420 and body['exercise_minutes_total'] == 30
    assert '70.5' not in str(body) and '不含体重数值' in body['note']

    created = client.post('/api/notes', json={'title': '虚构笔记', 'content': '我在整理资料索引。', 'type': 'note'})
    assert created.status_code == 201, created.text
    documents = client.get('/api/tools/documents?limit=3').json()
    assert documents['documents'] == 1
    assert documents['recent'][0]['title'] == '虚构笔记'
    assert '不含正文' in documents['note']

    audit = client.get('/api/tools/vault_audit').json()
    assert audit['checked'] >= 1
    assert isinstance(audit['by_code'], dict)
    assert '不返回具体路径' in audit['note']
    assert client.get('/api/tools/body?days=3').status_code == 422
    assert client.get('/api/tools/documents?limit=99').status_code == 422
