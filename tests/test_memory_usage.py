"""Reverse references: which stored answers actually cited which memories.

The contract under test: only saved assistant messages create usage rows;
previews and drafts stay silent, withdrawn memories keep their history with
the current status attached, and deleting a conversation removes the links.
"""
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.profile.shanghai_today', lambda: date(2026, 10, 4))
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    return client, database


def add_note(client, title, content, kind='fact'):
    response = client.post('/api/notes', json={'title': title, 'content': content, 'type': kind})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload['memories']) == 1, payload
    return payload['memories'][0]['id']


def activate(client, memory_id):
    response = client.patch(f'/api/memories/{memory_id}/status', json={'status': 'active'})
    assert response.status_code == 200, response.text
    return memory_id


def add_active_memory(client, title, content, kind='fact'):
    return activate(client, add_note(client, title, content, kind))


def table_count(database, table):
    with database.connect() as connection:
        return connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_chat_records_usage_only_for_stored_assistant_answers(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    assert table_count(database, 'memory_usages') == 0

    response = client.post('/api/chat', json={'message': '我是谁？'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['citations'] and payload['citations'][0]['memory_id'] == memory_id, payload

    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute('SELECT * FROM memory_usages').fetchall()]
    assert len(rows) == 1
    assert rows[0]['memory_id'] == memory_id
    assert rows[0]['conversation_id'] == payload['conversation_id']
    with database.connect() as connection:
        stored = connection.execute('SELECT role FROM messages WHERE id=?', (rows[0]['message_id'],)).fetchone()
    assert stored['role'] == 'assistant'
    # The user message is stored but never counted as a usage.
    assert table_count(database, 'messages') == 2


def test_preview_never_creates_usage_rows(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')

    for _ in range(2):
        assert client.post('/api/chat/preview', json={'message': '我是谁？'}).status_code == 200
    assert table_count(database, 'memory_usages') == 0
    assert table_count(database, 'messages') == 0
    assert table_count(database, 'conversations') == 0


def test_duplicate_or_unknown_citations_are_collapsed_and_skipped(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    conversation_id = database.create_conversation('手工校验')
    database.add_message(conversation_id, 'assistant', '回答', [
        {'memory_id': memory_id}, {'memory_id': memory_id},
        {'memory_id': 999999}, {'document_id': 1},
    ])
    with database.connect() as connection:
        rows = connection.execute('SELECT memory_id FROM memory_usages').fetchall()
    assert [row['memory_id'] for row in rows] == [memory_id]


def test_usage_detail_and_profile_summary_expose_reverse_references(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    client.post('/api/chat', json={'message': '我是谁？'})

    detail = client.get(f'/api/memories/{memory_id}/usage')
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload['total'] == 1
    assert payload['memory']['memory_id'] == memory_id
    assert payload['memory']['status'] == 'active'
    assert payload['items'][0]['question'] == '我是谁？'
    assert payload['items'][0]['used_at']

    summary = client.get('/api/profile')
    assert summary.status_code == 200, summary.text
    body = summary.json()
    entry = next(item for item in body['active_memories'] if item['id'] == memory_id)
    assert entry['usage']['uses'] == 1 and entry['usage']['last_used_at']
    assert body['usage']['window_days'] == 30
    assert body['usage']['memories_used'] == 1
    assert body['usage']['recent'][0]['memory_id'] == memory_id
    assert body['usage']['recent'][0]['question'] == '我是谁？'

    assert client.get('/api/memories/99999/usage').status_code == 404


def test_withdrawn_memory_keeps_history_with_current_status(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    client.post('/api/chat', json={'message': '我是谁？'})
    archived = client.patch(f'/api/memories/{memory_id}/status', json={'status': 'archived'})
    assert archived.status_code == 200, archived.text

    detail = client.get(f'/api/memories/{memory_id}/usage').json()
    assert detail['total'] == 1
    assert detail['memory']['status'] == 'archived'
    body = client.get('/api/profile').json()
    assert all(item['id'] != memory_id for item in body['active_memories'])
    assert any(item['memory_id'] == memory_id and item['status'] == 'archived' for item in body['usage']['recent'])


def test_deleting_a_conversation_removes_only_the_usage_links(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    client.post('/api/chat', json={'message': '我是谁？'})
    assert table_count(database, 'memory_usages') == 1

    with database.connect() as connection:
        connection.execute('DELETE FROM conversations')
    assert table_count(database, 'memory_usages') == 0
    with database.connect() as connection:
        status = connection.execute('SELECT status FROM memories WHERE id=?', (memory_id,)).fetchone()['status']
    assert status == 'active'


def test_candidate_pool_distribution_counts_sources_and_months(tmp_path, monkeypatch):
    client, database = build_client(tmp_path, monkeypatch)
    first_id = add_note(client, '候选一', '我偏爱清晨散步，这条尚未确认。', 'preference')
    add_note(client, '候选二', '我计划学习写周记，这条尚未确认。', 'goal')
    journal_id, _ = database.upsert_document(
        '02-Life/journal/2026-09-15-reflection.md', '旧日记', '那天我完成了一次长途骑行。', 'b' * 64,
        {'source_kind': 'user-authored'},
    )
    database.replace_document_memories(journal_id, [{
        'kind': 'experience', 'title': '旧日记', 'content': '那天我完成了一次长途骑行。',
        'source_quote': '那天我完成了一次长途骑行。', 'confidence': 'explicit',
    }])
    wolai_id, _ = database.upsert_document(
        '80-Wolai/wolai_abcdef', '导入资料', '这是一条没有明确日期的导入内容。', 'c' * 64,
        {'source_kind': 'wolai'},
    )
    database.replace_document_memories(wolai_id, [{
        'kind': 'note', 'title': '导入资料', 'content': '这是一条没有明确日期的导入内容。',
        'source_quote': '这是一条没有明确日期的导入内容。', 'confidence': 'explicit',
    }])

    body = client.get('/api/profile').json()
    pool = body['candidate_pool']
    assert pool['total'] == 4
    assert sum(item['count'] for item in pool['by_source']) == 4
    assert all(item['path'] for item in pool['by_source'])
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    assert {'month': month, 'count': 4} in pool['by_month'], 'extraction month covers every candidate'
    assert {'month': month, 'count': 2} in pool['by_record_month'], 'inbox notes carry their own created_at'
    assert {'month': '2026-09', 'count': 1} in pool['by_record_month'], 'journal path date is parsed'
    assert pool['record_dated_total'] == 3
    assert pool['record_unknown_total'] == 1, 'the Wolai import has no reliable date'
    assert '未标注' in pool['record_basis']
    assert pool['oldest_extracted_at'] and pool['newest_extracted_at']
    assert pool['basis'] == '按提取时间统计，非原始记录日期'

    empty = Database(tmp_path / 'empty.sqlite3')
    empty.initialize()
    assert empty.candidate_pool_distribution()['total'] == 0
    assert empty.memory_usage_stats() == {}
    assert empty.recent_memory_usage() == []
    assert empty.memory_usage_detail(1) is None

    # Reviewing one candidate removes it from the pending pool but keeps the
    # per-source progress visible.
    assert client.patch(f"/api/memories/{first_id}/status", json={"status": "active"}).status_code == 200
    with database.connect() as connection:
        reviewed_path = connection.execute(
            "SELECT d.path FROM memories m JOIN documents d ON d.id=m.document_id WHERE m.id=?",
            (first_id,),
        ).fetchone()["path"]
    pool = client.get("/api/profile").json()["candidate_pool"]
    assert pool["total"] == 3
    progress = {item["path"]: item for item in pool["by_source_progress"]}
    assert progress[reviewed_path] == {"path": reviewed_path, "candidate": 0, "reviewed": 1, "total": 1}
    assert all(item["candidate"] >= 0 and item["reviewed"] + item["candidate"] == item["total"] for item in pool["by_source_progress"])
