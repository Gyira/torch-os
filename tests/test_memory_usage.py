"""Reverse references: which stored answers actually cited which memories.

The contract under test: only saved assistant messages create usage rows;
withdrawn memories keep their history with the current status attached, and
deleting a conversation removes the links. The retired profile/usage HTTP
pages are gone, so the remaining assertions read the database methods that
still back the chat feature.
"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def build_client(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    app = create_app(database, tmp_path / 'vault', security_environ={})
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



def store_chat(database, memory_id, question="我是谁？"):
    conversation_id = database.create_conversation(question)
    database.add_message(conversation_id, "user", question, [])
    database.add_message(conversation_id, "assistant", "回答", [{"memory_id": memory_id}])
    return conversation_id


def table_count(database, table):
    with database.connect() as connection:
        return connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def test_chat_records_usage_only_for_stored_assistant_answers(tmp_path):
    client, database = build_client(tmp_path)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    assert table_count(database, 'memory_usages') == 0

    conversation_id = store_chat(database, memory_id)

    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute('SELECT * FROM memory_usages').fetchall()]
    assert len(rows) == 1
    assert rows[0]['memory_id'] == memory_id
    assert rows[0]['conversation_id'] == conversation_id
    with database.connect() as connection:
        stored = connection.execute('SELECT role FROM messages WHERE id=?', (rows[0]['message_id'],)).fetchone()
    assert stored['role'] == 'assistant'
    # The user message is stored but never counted as a usage.
    assert table_count(database, 'messages') == 2


def test_duplicate_or_unknown_citations_are_collapsed_and_skipped(tmp_path):
    client, database = build_client(tmp_path)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    conversation_id = database.create_conversation('手工校验')
    database.add_message(conversation_id, 'assistant', '回答', [
        {'memory_id': memory_id}, {'memory_id': memory_id},
        {'memory_id': 999999}, {'document_id': 1},
    ])
    with database.connect() as connection:
        rows = connection.execute('SELECT memory_id FROM memory_usages').fetchall()
    assert [row['memory_id'] for row in rows] == [memory_id]


def test_usage_detail_and_recent_usage_expose_reverse_references(tmp_path):
    client, database = build_client(tmp_path)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    store_chat(database, memory_id)

    detail = database.memory_usage_detail(memory_id)
    assert detail is not None
    assert detail['total'] == 1
    assert detail['memory']['memory_id'] == memory_id
    assert detail['memory']['status'] == 'active'
    assert detail['items'][0]['question'] == '我是谁？'
    assert detail['items'][0]['used_at']

    stats = database.memory_usage_stats()
    assert stats[memory_id]['uses'] == 1 and stats[memory_id]['last_used_at']

    recent = database.recent_memory_usage()
    assert recent[0]['memory_id'] == memory_id
    assert recent[0]['question'] == '我是谁？'

    assert database.memory_usage_detail(99999) is None


def test_withdrawn_memory_keeps_history_with_current_status(tmp_path):
    client, database = build_client(tmp_path)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    store_chat(database, memory_id)
    archived = client.patch(f'/api/memories/{memory_id}/status', json={'status': 'archived'})
    assert archived.status_code == 200, archived.text

    detail = database.memory_usage_detail(memory_id)
    assert detail['total'] == 1
    assert detail['memory']['status'] == 'archived'
    with database.connect() as connection:
        active_ids = {row['id'] for row in connection.execute("SELECT id FROM memories WHERE status='active'")}
    assert memory_id not in active_ids
    recent = database.recent_memory_usage()
    assert any(item['memory_id'] == memory_id and item['status'] == 'archived' for item in recent)


def test_deleting_a_conversation_removes_only_the_usage_links(tmp_path):
    client, database = build_client(tmp_path)
    memory_id = add_active_memory(client, '身份记录', '我是本地示例用户，目前在上海生活。')
    store_chat(database, memory_id)
    assert table_count(database, 'memory_usages') == 1

    with database.connect() as connection:
        connection.execute('DELETE FROM conversations')
    assert table_count(database, 'memory_usages') == 0
    with database.connect() as connection:
        status = connection.execute('SELECT status FROM memories WHERE id=?', (memory_id,)).fetchone()['status']
    assert status == 'active'


def test_candidate_pool_distribution_counts_sources_and_months(tmp_path):
    client, database = build_client(tmp_path)
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

    pool = database.candidate_pool_distribution()
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
    pool = database.candidate_pool_distribution()
    assert pool["total"] == 3
    progress = {item["path"]: item for item in pool["by_source_progress"]}
    assert progress[reviewed_path] == {"path": reviewed_path, "candidate": 0, "reviewed": 1, "total": 1}
    assert all(item["candidate"] >= 0 and item["reviewed"] + item["candidate"] == item["total"] for item in pool["by_source_progress"])
