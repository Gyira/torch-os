import json
from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.application import APP_VERSION, create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.habits.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.learning.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.profile.shanghai_today', lambda: date(2026, 10, 3))
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    return app, client, database


def add_note(client, title, content, kind='fact'):
    response = client.post('/api/notes', json={'title': title, 'content': content, 'type': kind})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload['memories']) == 1, payload
    return payload


def activate(client, note):
    memory_id = note['memories'][0]['id']
    response = client.patch(f'/api/memories/{memory_id}/status', json={'status': 'active'})
    assert response.status_code == 200, response.text
    return memory_id


def document_id_of(database, memory_id):
    with database.connect() as connection:
        return connection.execute('SELECT document_id FROM memories WHERE id=?', (memory_id,)).fetchone()['document_id']


def sql_execute(database, statement, params=()):
    with database.connect() as connection:
        connection.execute(statement, params)


def table_count(database, table):
    with database.connect() as connection:
        return connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_profile_and_preview_require_session_and_validate_input(tmp_path, monkeypatch):
    app, client, _ = build_client(tmp_path, monkeypatch)
    assert TestClient(app).get('/api/profile').status_code == 401
    assert TestClient(app).post('/api/chat/preview', json={'message': '问题'}).status_code == 401
    assert client.post('/api/chat/preview', json={'message': ''}).status_code == 422
    assert client.post('/api/chat/preview', json={'message': '问' * 4001}).status_code == 422
    assert client.post('/api/chat/preview', json={'message': '问题', 'use_cloud': True}).status_code == 422
    assert client.get('/api/profile').status_code == 200


def test_preview_is_read_only_and_matches_search(tmp_path, monkeypatch):
    _, client, database = build_client(tmp_path, monkeypatch)
    active_note = add_note(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    active_id = activate(client, active_note)
    add_note(client, '候选内容', '这是一条尚未确认的本地内容。', 'note')

    before = {table: table_count(database, table) for table in ('conversations', 'messages', 'memory_events')}
    with database.connect() as connection:
        audit_ids = {row['id'] for row in connection.execute('SELECT id FROM audit_events').fetchall()}
    response = client.post('/api/chat/preview', json={'message': '我是谁'})
    assert response.status_code == 200, response.text
    payload = response.json()
    after = {table: table_count(database, table) for table in before}
    assert after == before
    # The freshness scan is the same one /api/chat runs; it may only add vault_scan records.
    with database.connect() as connection:
        new_audits = [row['event_type'] for row in connection.execute(
            'SELECT event_type FROM audit_events WHERE id NOT IN (%s)' % ','.join('?' * len(audit_ids)),
            tuple(audit_ids)).fetchall()] if audit_ids else []
    assert set(new_audits) <= {'vault_scan'}
    assert payload['query'] == '我是谁'
    assert payload['notes'][0] == '预览不调用模型，不创建对话，也不改变记忆审核状态。'

    expected = database.search_memories('我是谁')
    assert [item['memory_id'] for item in payload['evidence']] == [row['id'] for row in expected]
    assert [item['quote'] for item in payload['evidence']] == [row['content'] for row in expected]
    assert payload['evidence'][0]['memory_id'] == active_id
    assert payload['evidence'][0]['path'].endswith('.md')

    counts, excluded = payload['counts'], payload['excluded']
    assert counts['max_limit'] == 6
    assert counts['active_total'] == 1
    assert counts['selected'] == counts['matched_total'] == counts['usable_total'] == 1
    assert excluded['candidate'] == 1
    assert excluded['not_matching'] == 0
    assert counts['active_total'] == (counts['selected'] + excluded['source_unavailable'] + excluded['not_personal']
                                     + excluded['stale_source'] + excluded['expired'] + excluded['not_matching'] + excluded['over_limit'])
    assert counts['usable_total'] == counts['matched_total'] + excluded['not_matching']
    assert counts['matched_total'] == counts['selected'] + excluded['over_limit']


def test_preview_exclusion_categories_and_invariants(tmp_path, monkeypatch):
    _, client, database = build_client(tmp_path, monkeypatch)

    unavailable_id = activate(client, add_note(client, '来源缺失', '我是即将失去来源的记录。'))
    unavailable_doc = document_id_of(database, unavailable_id)
    with database.connect() as connection:
        relative = connection.execute('SELECT path FROM documents WHERE id=?', (unavailable_doc,)).fetchone()['path']
    (tmp_path / 'vault' / relative).unlink()
    assert client.post('/api/vault/scan').status_code == 200
    # The scan archives the memory; restore it to active to model a legacy inconsistent row.
    sql_execute(database, "UPDATE memories SET status='active' WHERE id=?", (unavailable_id,))

    private_id = activate(client, add_note(client, '非个人分区', '我是隐私分区中的记录。'))
    sql_execute(database, "UPDATE documents SET privacy='private' WHERE id=?", (document_id_of(database, private_id),))

    stale_id = activate(client, add_note(client, '来源已变更', '我是来源版本落后的记录。'))
    sql_execute(database, 'UPDATE documents SET revision=revision+1 WHERE id=?', (document_id_of(database, stale_id),))

    expired_id = activate(client, add_note(client, '已过期', '我是已经过期的记录。'))
    expired_doc = document_id_of(database, expired_id)
    with database.connect() as connection:
        metadata = json.loads(connection.execute('SELECT metadata_json FROM documents WHERE id=?', (expired_doc,)).fetchone()['metadata_json'])
    metadata['valid_until'] = '2020-01-01'
    sql_execute(database, 'UPDATE documents SET metadata_json=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), expired_doc))

    activate(client, add_note(client, '与问题无关', '桥梁巡检安排在第 3 周。', 'project'))
    for index in range(8):
        activate(client, add_note(client, f'确认记忆{index}', f'我是已确认记忆第 {index} 条。'))

    candidate = add_note(client, '待确认', '等待确认的内容。', 'note')
    rejected_id = activate(client, add_note(client, '将拒绝', '我是会被拒绝的记录。'))
    assert client.patch(f'/api/memories/{rejected_id}/status', json={'status': 'rejected'}).status_code == 200
    archived_id = activate(client, add_note(client, '将归档', '我是会被归档的记录。'))
    assert client.patch(f'/api/memories/{archived_id}/status', json={'status': 'archived'}).status_code == 200

    response = client.post('/api/chat/preview', json={'message': '我是谁'})
    assert response.status_code == 200, response.text
    payload = response.json()
    counts, excluded = payload['counts'], payload['excluded']
    assert len(payload['evidence']) == 6
    assert counts['active_total'] == 13
    assert counts['selected'] == 6
    assert counts['usable_total'] == 9
    assert counts['matched_total'] == 8
    assert excluded['source_unavailable'] == 1
    assert excluded['not_personal'] == 1
    assert excluded['stale_source'] == 1
    assert excluded['expired'] == 1
    assert excluded['not_matching'] == 1
    assert excluded['over_limit'] == 2
    assert excluded['candidate'] == 1
    assert excluded['rejected'] == 1
    assert excluded['archived'] == 1
    assert counts['active_total'] == (counts['selected'] + excluded['source_unavailable'] + excluded['not_personal']
                                     + excluded['stale_source'] + excluded['expired'] + excluded['not_matching'] + excluded['over_limit'])
    assert counts['usable_total'] == counts['matched_total'] + excluded['not_matching']
    assert counts['matched_total'] == counts['selected'] + excluded['over_limit']
    assert candidate['memories'][0]['id'] not in [item['memory_id'] for item in payload['evidence']]


def test_profile_summary_aggregates_memories_goals_and_rhythm(tmp_path, monkeypatch):
    _, client, database = build_client(tmp_path, monkeypatch)

    activate(client, add_note(client, '身份', '我是本地示例用户。'))
    add_note(client, '候选', '等待确认。', 'note')
    rejected_id = activate(client, add_note(client, '拒绝', '将被拒绝。'))
    assert client.patch(f'/api/memories/{rejected_id}/status', json={'status': 'rejected'}).status_code == 200
    archived_id = activate(client, add_note(client, '归档', '将被归档。'))
    assert client.patch(f'/api/memories/{archived_id}/status', json={'status': 'archived'}).status_code == 200

    goal = client.post('/api/goals', json={'title': '示例目标'})
    assert goal.status_code in {200, 201}, goal.text
    assert client.post('/api/tasks', json={'title': '示例任务一'}).status_code in {200, 201}
    assert client.post('/api/tasks', json={'title': '示例任务二'}).status_code in {200, 201}

    journal = client.put('/api/journal/2026-10-03', json={'content': '示例每日记录', 'revision': 0})
    assert journal.status_code == 200, journal.text
    habit = client.post('/api/habits', json={'title': '示例习惯', 'minimum_action': '读一页', 'start_date': '2026-09-28'})
    assert habit.status_code in {200, 201}, habit.text
    entry = client.put(f"/api/habits/{habit.json()['id']}/entries/2026-10-03", json={'revision': 0, 'status': 'done'})
    assert entry.status_code == 200, entry.text
    body_entry = client.put('/api/body/entries/2026-10-03', json={'revision': 0, 'weight_kg': 68.5})
    assert body_entry.status_code == 200, body_entry.text
    subject = client.post('/api/learning/subjects', json={'title': '示例科目', 'weekly_target_minutes': 120})
    assert subject.status_code in {200, 201}, subject.text
    session = client.post('/api/learning/sessions', json={'subject_id': subject.json()['id'], 'study_date': '2026-10-03',
                                                          'minutes': 60, 'request_key': str(uuid4())})
    assert session.status_code in {200, 201}, session.text

    before = table_count(database, 'audit_events')
    response = client.get('/api/profile')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert table_count(database, 'audit_events') == before
    assert payload['version'] == APP_VERSION
    assert payload['documents']['available'] >= 4
    assert payload['documents']['other_privacy'] == 0

    memories = payload['memories']
    assert memories['active'] == 1
    assert memories['candidate'] == 1
    assert memories['rejected'] == 1
    assert memories['archived'] == 1
    assert memories['active_usable'] == 1
    assert sum(memories['active_blocked'].values()) == 0
    assert memories['by_kind'] == {'fact': 1}
    assert payload['active_memories'][0]['source_path'].endswith('.md')
    assert payload['active_memories'][0]['content'] == '我是本地示例用户。'
    assert payload['excluded'] == {'candidate': 1, 'rejected': 1, 'archived': 1}

    assert payload['goals']['active'] == 1
    assert payload['goals']['open_tasks'] == 2
    assert payload['goals']['completed_tasks'] == 0
    assert payload['goals']['items'][0]['title'] == '示例目标'

    rhythm = payload['rhythm']
    assert rhythm['journal_days_last_7'] == 1
    assert rhythm['habits_active'] == 1
    assert rhythm['habit_done_last_7'] == 1
    assert rhythm['study_minutes_last_7'] == 60
    assert rhythm['study_subjects_active'] == 1
    assert rhythm['body_record_days_last_7'] == 1

    assert len(payload['usage_rules']) == 5
    assert any('使用记录' in rule for rule in payload['usage_rules'])
    assert payload['relationship_vault']['connected'] is False


def test_config_capabilities_and_health_report_v05(tmp_path, monkeypatch):
    _, client, _ = build_client(tmp_path, monkeypatch)
    capabilities = client.get('/api/config/status').json()['capabilities']
    assert capabilities['profile'] is True
    assert capabilities['chat_preview'] is True
    library = client.get('/api/profile').json()['library_health']
    assert library['documents'] == 0
    assert library['vault']['exists'] is True
    assert library['audit']['checked'] == 0
    assert '不含正文' in library['note']
    health = client.get('/api/health').json()
    assert health['version'] == APP_VERSION


def test_library_health_reports_counts_without_paths_or_text(tmp_path, monkeypatch):
    _, client, database = build_client(tmp_path, monkeypatch)
    add_note(client, '虚构资料', '我每周跑步三次，用来测试资料健康度。')
    vault = tmp_path / 'vault'
    (vault / '90-Inbox' / 'broken.md').write_text('---\ntitle: 缺 id\n---\n\n', encoding='utf-8')
    library = client.get('/api/profile').json()['library_health']
    assert library['documents'] >= 1
    assert library['recent'][0]['title']
    assert library['vault']['markdown_files'] >= 1
    assert library['audit']['checked'] >= 1
    assert isinstance(library['audit']['by_code'], dict)
    rendered = str(library)
    assert 'broken.md' not in rendered
    assert '我每周跑步三次' not in rendered
    assert '不含正文' in library['note']


def test_profile_activity_counts_only_recorded_days_without_scoring(tmp_path, monkeypatch):
    app, client, database = build_client(tmp_path, monkeypatch)
    monkeypatch.setattr('backend.app.profile.shanghai_today', lambda: date(2026, 10, 4))

    assert TestClient(app).get('/api/profile/activity').status_code == 401
    assert client.get('/api/profile/activity?days=6').status_code == 422
    assert client.get('/api/profile/activity?days=91').status_code == 422
    assert client.get('/api/profile/activity?days=abc').status_code == 422

    inside = client.put('/api/journal/2026-10-04', json={'revision': 0, 'content': '虚构记录'})
    assert inside.status_code == 200, inside.text
    outside = client.put('/api/journal/2026-08-01', json={'revision': 0, 'content': '虚构窗口外记录'})
    assert outside.status_code == 200, outside.text
    body_entry = client.put('/api/body/entries/2026-10-02', json={'revision': 0, 'sleep_minutes': 400})
    assert body_entry.status_code == 200, body_entry.text

    habit = client.post('/api/habits', json={'title': '虚构习惯', 'minimum_action': '做一次',
                                             'start_date': '2026-09-01', 'weekdays': list(range(7))})
    assert habit.status_code in {200, 201}, habit.text
    habit_id = habit.json()['id']
    assert client.put(f'/api/habits/{habit_id}/entries/2026-10-04',
                      json={'revision': 0, 'status': 'done'}).status_code == 200
    assert client.put(f'/api/habits/{habit_id}/entries/2026-10-03',
                      json={'revision': 0, 'status': 'skipped'}).status_code == 200

    subject = client.post('/api/learning/subjects', json={'title': '虚构科目', 'weekly_target_minutes': 120})
    assert subject.status_code in {200, 201}, subject.text
    subject_id = subject.json()['id']
    for day, minutes in (('2026-10-04', 60), ('2026-10-02', 30)):
        created = client.post('/api/learning/sessions', json={
            'subject_id': subject_id, 'study_date': day, 'minutes': minutes, 'request_key': str(uuid4()),
        })
        assert created.status_code in {200, 201}, created.text

    before = table_count(database, 'audit_events')
    response = client.get('/api/profile/activity')
    assert response.status_code == 200, response.text
    assert table_count(database, 'audit_events') == before
    payload = response.json()
    assert payload['window'] == {'days': 30, 'start': '2026-09-05', 'end': '2026-10-04'}
    assert payload['journal'] == {'days_recorded': 1}
    assert payload['body'] == {'days_recorded': 1}
    assert payload['habits'] == {'days_recorded': 2, 'done': 1, 'skipped': 1, 'active_definitions': 1}
    assert payload['study'] == {'days_recorded': 2, 'minutes': 90, 'subjects_recorded': 1}
    assert any('未记录不计为失败' in note for note in payload['notes'])
    assert any('不评价' in note for note in payload['notes'])
    # No score or rate fields may sneak into the fact-only payload.
    assert not any(key in str(payload) for key in ('score', 'completion_rate', 'streak'))

    week = client.get('/api/profile/activity', params={'days': 7}).json()
    assert week['window'] == {'days': 7, 'start': '2026-09-28', 'end': '2026-10-04'}
    assert week['journal'] == {'days_recorded': 1}
    assert week['study'] == {'days_recorded': 2, 'minutes': 90, 'subjects_recorded': 1}
