from fastapi.testclient import TestClient

from backend.app import task_notes
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def test_task_import_is_authenticated_versioned_and_independent(tmp_path):
    database = Database(tmp_path / 'app.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    vault = tmp_path / 'vault'
    app = create_app(database, vault, gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    note = client.post('/api/notes', json={
        'title': '虚构待办', 'content': '# 学习\n\n- [ ] 阅读一章\n- [x] 旧记录已完成'}).json()
    did = note['document']['id']
    url = f'/api/documents/{did}/tasks'
    assert TestClient(app).get(url + '/preview').status_code == 401
    preview = client.get(url + '/preview').json()
    assert len(preview['items']) == 2
    assert task_notes.scan_task_notes(vault)[0] == []
    payload = {
        'revision': preview['revision'], 'sha256': preview['sha256'],
        'selected_keys': [row['key'] for row in preview['items']],
        'goal_path': '', 'confirm': True,
    }
    created = client.post(url + '/apply', json=payload)
    assert created.status_code == 200 and created.json()['created_count'] == 2
    completed = [row for row in created.json()['tasks'] if row['status'] == 'completed'][0]
    assert completed['completed_at'] is None
    assert client.post(url + '/apply', json=payload).json()['existing_count'] == 2
    assert len(database.list_memories('candidate')) == 2
    assert not database.list_memories('active')

    notes = task_notes.scan_task_notes(vault)[0]
    assert len(notes) == 2
    assert sum(1 for item in notes if item['done']) == 1
    assert all(item['done_on'] == '' for item in notes if item['done'])

    path = vault / note['document']['path']
    path.write_text(path.read_text(encoding='utf-8') + '\n- [ ] 新的一行\n', encoding='utf-8')
    assert client.post(url + '/apply', json=payload).status_code == 409
    assert len(task_notes.scan_task_notes(vault)[0]) == 2
