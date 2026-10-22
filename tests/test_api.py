import json

from fastapi.testclient import TestClient
from backend.app import pillar_notes, task_notes
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def make_client(tmp_path):
    app = create_app(Database(tmp_path / 'test.sqlite3'), tmp_path / 'vault',
        ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1), security_environ={})
    client = TestClient(app)
    client.get('/')
    return client, app


def test_health_and_chat(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get('/api/health').json()['status'] == 'ok'
    result = client.post('/api/chat', json={'message':'我是谁？'}).json()
    assert result['conversation_id']
    assert result['mode'] == 'local'
    assert result['citations'] == []


def test_note_confirmation_correction_and_withdrawal(tmp_path):
    client, app = make_client(tmp_path)
    result = client.post('/api/notes', json={'title':'个人档案','content':'我的职业是建筑师。','type':'fact'})
    assert result.status_code == 201
    note = result.json()
    memory_id = note['memories'][0]['id']
    assert client.post('/api/chat',json={'message':'我是谁？'}).json()['citations'] == []
    client.patch(f'/api/memories/{memory_id}/status',json={'status':'active'})
    response = client.post('/api/chat',json={'message':'我是谁？'}).json()
    assert '建筑师' in response['answer']
    assert response['citations'][0]['document_id'] == note['document']['id']
    client.patch(f'/api/memories/{memory_id}',json={'content':'我的职业是软件工程师。'})
    assert client.post('/api/chat',json={'message':'我是谁？'}).json()['citations'] == []
    client.patch(f'/api/memories/{memory_id}/status',json={'status':'active'})
    corrected = client.post('/api/chat',json={'message':'我是谁？'}).json()
    assert '软件工程师' in corrected['answer'] and '建筑师' not in corrected['answer']
    history = client.get(f'/api/memories/{memory_id}/history').json()['history']
    assert any(event['event_type']=='user_corrected' for event in history)
    client.patch(f'/api/memories/{memory_id}/status',json={'status':'rejected'})
    assert client.post('/api/chat',json={'message':'我是谁？'}).json()['citations'] == []


def test_cross_origin_unauthenticated_and_bad_input_rejected(tmp_path):
    client, app = make_client(tmp_path)
    stranger = TestClient(app)
    assert stranger.get('/api/memories').status_code == 401
    assert client.post('/api/vault/scan',headers={'origin':'https://example.com'}).status_code == 403
    assert client.get('/api/health',headers={'host':'evil.example'}).status_code == 400
    assert client.post('/api/notes',json={'title':'   ','content':'text'}).status_code == 422
    assert client.post('/api/chat',json={'message':' ','use_cloud':False}).status_code == 422
    assert client.post('/api/chat',json={'message':'test','conversation_id':99999}).status_code == 404
    assert client.post('/api/chat',json={'message':'" OR * : - ; ( )'}).status_code == 200


def test_source_delete_stops_future_answers(tmp_path):
    client, app = make_client(tmp_path)
    note=client.post('/api/notes',json={'title':'学习目标','content':'我的目标是学习葡萄牙语。','type':'goal'}).json()
    mid=note['memories'][0]['id']
    client.patch(f'/api/memories/{mid}/status',json={'status':'active'})
    assert client.post('/api/chat',json={'message':'我的目标'}).json()['citations']
    (app.state.vault_dir / note['document']['path']).unlink()
    assert client.post('/api/chat',json={'message':'我的目标'}).json()['citations']==[]
    assert client.patch(f'/api/memories/{mid}/status',json={'status':'active'}).status_code == 409


def test_markdown_goals_and_tasks_replace_the_retired_backends(tmp_path):
    client, app = make_client(tmp_path)
    vault = app.state.vault_dir
    goal = pillar_notes.create_note(vault, pillar='life', chapter='goal_year',
                                    title='学习计划', note_type='goal')
    task_notes.create_task_note(vault, '', '阅读十页', {
        'goal': goal['path'], 'priority': 'high', 'created': '2026-10-01'})
    goals = client.get('/api/goals').json()
    assert goals[0]['id'] == goal['path'] and goals[0]['title'] == '学习计划'
    tool = client.get('/api/tools/goals').json()
    assert tool['goals'][0]['title'] == '学习计划'
    assert tool['open_task_count'] == 1
    assert tool['next_tasks'][0]['title'] == '阅读十页'
    board = client.get('/api/time/tasks').json()
    assert board['tasks'][0]['goal'] == goal['path']
    # Retired write/learning/body/draft endpoints stay gone.
    assert client.post('/api/goals', json={'title': '学习计划'}).status_code == 405
    assert client.post('/api/learning/subjects', json={'title': 'x', 'weekly_target_minutes': 10}).status_code == 404
    assert client.put('/api/body/entries/2026-10-04', json={'revision': 0, 'weight_kg': 70}).status_code == 404
    assert client.post('/api/daily-plan/draft').status_code == 404


def test_memory_list_exposes_origin_month_without_raw_metadata(tmp_path):
    client, app = make_client(tmp_path)
    dated = client.post('/api/notes', json={'title': '带日期的记录', 'content': '虚构内容一。', 'type': 'fact'}).json()
    undated = client.post('/api/notes', json={'title': '无日期的记录', 'content': '虚构内容二。', 'type': 'fact'}).json()
    database = app.state.database
    with database.connect() as connection:
        connection.execute(
            "UPDATE documents SET path=?, metadata_json=? WHERE id=?",
            ('90-Inbox/2026-09-10-旧笔记.md', json.dumps({'event_date': '2026-09-10'}), dated['document']['id']),
        )
        connection.execute(
            "UPDATE documents SET path=?, metadata_json='{}' WHERE id=?",
            ('90-Inbox/无日期笔记.md', undated['document']['id']),
        )
    rows = client.get('/api/memories?status=candidate').json()
    by_id = {row['id']: row for row in rows}
    dated_row = by_id[dated['memories'][0]['id']]
    assert dated_row['source_date'] == '2026-09-10'
    assert dated_row['record_month'] == '2026-09'
    assert 'source_metadata_json' not in dated_row
    assert 'event_date' not in json.dumps(dated_row, ensure_ascii=False)
    undated_row = by_id[undated['memories'][0]['id']]
    assert undated_row['source_date'] is None and undated_row['record_month'] is None


def test_memory_batch_status_updates_each_item_with_audit_and_skips(tmp_path):
    client, app = make_client(tmp_path)
    first = client.post('/api/notes', json={'title': '批量一', 'content': '虚构内容甲。', 'type': 'fact'}).json()
    second = client.post('/api/notes', json={'title': '批量二', 'content': '虚构内容乙。', 'type': 'fact'}).json()
    third = client.post('/api/notes', json={'title': '批量三', 'content': '虚构内容丙。', 'type': 'fact'}).json()
    ids = [first['memories'][0]['id'], second['memories'][0]['id']]
    missing = 999999

    assert client.post('/api/memories/batch-status', json={'ids': ids, 'status': 'active', 'confirm': False}).status_code == 422
    assert client.post('/api/memories/batch-status', json={'ids': [], 'status': 'active', 'confirm': True}).status_code == 422
    assert client.post('/api/memories/batch-status', json={'ids': [1, 1], 'status': 'active', 'confirm': True}).status_code == 422
    assert client.post('/api/memories/batch-status', json={'ids': ids, 'status': 'archived', 'confirm': True}).status_code == 422
    assert client.post('/api/memories/batch-status', json={'ids': list(range(1, 52)), 'status': 'active', 'confirm': True}).status_code == 422

    response = client.post('/api/memories/batch-status', json={'ids': ids + [missing], 'status': 'rejected', 'confirm': True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == 'rejected' and payload['requested'] == 3 and payload['updated'] == 2
    assert sorted(payload['updated_ids']) == sorted(ids)
    assert payload['skipped'] == [{'memory_id': missing, 'reason': 'not_found'}]
    rejected = {row['id'] for row in client.get('/api/memories?status=rejected').json()}
    assert set(ids) <= rejected and third['memories'][0]['id'] not in rejected

    repeat = client.post('/api/memories/batch-status', json={'ids': ids, 'status': 'rejected', 'confirm': True})
    assert repeat.status_code == 200
    assert repeat.json()['updated'] == 0
    assert repeat.json()['skipped'] == [{'memory_id': item, 'reason': 'already'} for item in ids]

    third_id = third['memories'][0]['id']
    with app.state.database.connect() as connection:
        connection.execute('UPDATE documents SET revision=revision+1 WHERE id=?', (third['document']['id'],))
    confirm = client.post('/api/memories/batch-status', json={'ids': [third_id], 'status': 'active', 'confirm': True})
    assert confirm.status_code == 200
    assert confirm.json()['updated'] == 0
    assert confirm.json()['skipped'] == [{'memory_id': third_id, 'reason': 'source_changed'}]

    with app.state.database.connect() as connection:
        details = [json.loads(row['detail_json']) for row in connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='memory_status_changed' ORDER BY id")]
    batch = [item for item in details if item.get('batch')]
    assert sorted(item['memory_id'] for item in batch) == sorted(ids)
    assert all(item['status'] == 'rejected' for item in batch)
    assert client.get('/api/config/status').json()['capabilities']['memory_batch'] is True
