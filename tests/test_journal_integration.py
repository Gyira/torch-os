import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def make_app(tmp_path):
    database = Database(tmp_path / 'app.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    return app, client, database


def test_daily_workflow_through_application_routes_and_backup(tmp_path):
    app, client, database = make_app(tmp_path)
    day = '2025-10-01'
    missing = client.get(f'/api/journal/{day}').json()
    assert missing['recorded'] is False and missing['revision'] == 0
    review = client.post(f'/api/journal/{day}/review').json()
    assert review['recorded'] is False and '未记录不代表' in review['summary']
    goal = client.post('/api/goals', json={'title':'虚构验收学习目标'}).json()
    payload = {'revision':0, 'content':'这是虚构验收记录：海盐计划完成两章阅读。',
               'highlights':'为例子补充测试。', 'obstacles':'待核对边界。',
               'next_action':'检查海盐计划引用', 'goal_id':goal['id']}
    saved = client.put(f'/api/journal/{day}', json=payload)
    assert saved.status_code == 200 and saved.json()['revision'] == 1
    assert client.put(f'/api/journal/{day}', json=payload).status_code == 409
    action = {'revision':1, 'confirm':True}
    assert client.post(f'/api/journal/{day}/next-task', json={'revision':1, 'confirm':False}).status_code == 422
    first = client.post(f'/api/journal/{day}/next-task', json=action).json()
    second = client.post(f'/api/journal/{day}/next-task', json=action).json()
    assert first['created'] is True and second['created'] is False
    assert first['task']['id'] == second['task']['id']
    assert first['task']['goal_id'] == goal['id']
    exported = client.post(f'/api/journal/{day}/export', json=action)
    assert exported.status_code == 200
    result = exported.json()
    assert result['created'] is True and result['memories']
    assert all(memory['status'] == 'candidate' for memory in result['memories'])
    document = client.get(f"/api/documents/{result['document']['id']}").json()
    assert document['coverage_summary']['extraction_version'] == 'journal-fields-v1'
    before_ids = [row['id'] for row in database.list_memories()]
    reextract=client.post(f"/api/documents/{document['id']}/reextract",json={'revision':document['revision'],'sha256':document['sha256'],'confirm':True})
    assert reextract.status_code==200 and reextract.json()['changed'] is False
    assert reextract.json()['document']['coverage_summary']['extraction_version']=='journal-fields-v1'
    assert [row['id'] for row in database.list_memories()]==before_ids
    assert json.loads(document['metadata_json'])['event_date'] == day
    assert client.post(f'/api/journal/{day}/export', json=action).json()['created'] is False
    assert database.search_memories('海盐计划') == []
    memory = next(row for row in result['memories'] if '海盐计划' in row['content'])
    client.patch(f"/api/memories/{memory['id']}/status", json={'status':'active'})
    answer = client.post('/api/chat', json={'message':'海盐计划'}).json()
    assert answer['citations'][0]['source_date'] == day
    assert answer['citations'][0]['source_location_basis'] == 'document_body'
    assert answer['citations'][0]['source_start_line'] is not None
    backup = client.post('/api/backups').json()
    restored = client.post(f"/api/backups/{backup['filename']}/restore").json()
    restored_db = Database(Path(restored['database_path']))
    with restored_db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM journal_entries').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM journal_actions').fetchone()[0] == 2


def test_daily_endpoints_keep_security_and_validation(tmp_path):
    app, client, _ = make_app(tmp_path)
    assert TestClient(app).get('/api/journal/2025-10-01').status_code == 401
    assert client.get('/api/journal/2025-10-01', headers={'origin':'https://evil.example'}).status_code == 403
    assert client.get('/api/journal?from_date=2025-01-01&to_date=2025-02-10').status_code == 422
    assert client.get('/api/journal/2025-2-01').status_code == 422
    assert client.get('/api/journal/2999-01-01').status_code == 422
    assert client.put('/api/journal/2025-10-01', json={'revision':0,'content':'  '}).status_code == 422


def test_legacy_reextract_requires_confirmation_and_is_not_repeated(tmp_path):
    app, client, database = make_app(tmp_path)
    created = client.post('/api/notes', json={'title':'虚构旧版长文','content':'正文第一段。\n\n正文第二段。'}).json()
    document_id = created['document']['id']
    old_memory = created['memories'][0]
    client.patch(f"/api/memories/{old_memory['id']}/status", json={'status':'active'})
    # Simulate the existing 0.2 metadata without rewriting its original note.
    with database.connect() as connection:
        connection.execute('UPDATE documents SET coverage_summary_json=NULL WHERE id=?', (document_id,))
    document = client.get(f'/api/documents/{document_id}').json()
    payload = {'revision':document['revision'],'sha256':document['sha256'],'confirm':True}
    assert client.post(f'/api/documents/{document_id}/reextract',json={**payload,'confirm':False}).status_code == 422
    assert database.list_memories('active')
    result = client.post(f'/api/documents/{document_id}/reextract',json=payload)
    assert result.status_code == 200 and result.json()['changed'] is True
    assert database.list_memories('active') == []
    assert len(database.list_memories('candidate')) == 2
    assert any(row['id']==old_memory['id'] for row in database.list_memories('archived'))
    count = len(database.list_memories())
    repeat = client.post(f'/api/documents/{document_id}/reextract',json=payload)
    assert repeat.json()['changed'] is False and len(database.list_memories()) == count
    assert client.post(f'/api/documents/{document_id}/reextract',json={**payload,'sha256':'0'*64}).status_code == 409


def test_unknown_extraction_version_is_not_downgraded(tmp_path):
    app,client,database=make_app(tmp_path)
    note=client.post('/api/notes',json={'title':'未来版本样例','content':'未来提取算法保留的记录。'}).json()
    did=note['document']['id']
    mid=note['memories'][0]['id']
    database.update_memory_status(mid,'active')
    with database.connect() as connection:
        connection.execute('UPDATE documents SET coverage_summary_json=? WHERE id=?',
                           (json.dumps({'extraction_version':'future-v999','candidate_count':1}),did))
    document=database.get_document(did)
    response=client.post(f'/api/documents/{did}/reextract',json={'revision':document['revision'],'sha256':document['sha256'],'confirm':True})
    assert response.status_code==409
    assert database.list_memories('active')[0]['id']==mid
