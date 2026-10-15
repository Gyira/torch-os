from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def test_task_import_is_authenticated_versioned_and_independent(tmp_path):
    database=Database(tmp_path/'app.sqlite3')
    gateway=ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1)
    app=create_app(database,tmp_path/'vault',gateway,security_environ={})
    client=TestClient(app)
    client.get('/')
    note=client.post('/api/notes',json={'title':'虚构待办','content':'# 学习\n\n- [ ] 阅读一章\n- [x] 旧记录已完成'}).json()
    did=note['document']['id']
    url=f'/api/documents/{did}/tasks'
    assert TestClient(app).get(url+'/preview').status_code==401
    preview=client.get(url+'/preview').json()
    assert len(preview['items'])==2
    assert client.get('/api/tasks').json()==[]
    payload={'revision':preview['revision'],'sha256':preview['sha256'],
             'selected_keys':[row['key'] for row in preview['items']],'goal_id':None,'confirm':True}
    created=client.post(url+'/apply',json=payload)
    assert created.status_code==200 and created.json()['created_count']==2
    completed=[row for row in created.json()['tasks'] if row['status']=='completed'][0]
    assert completed['completed_at'] is None
    assert client.patch(f"/api/tasks/{completed['id']}",json={'status':'completed'}).json()['completed_at'] is None
    assert client.post(url+'/apply',json=payload).json()['existing_count']==2
    assert len(database.list_memories('candidate'))==2
    assert not database.list_memories('active')
    path=tmp_path/'vault'/note['document']['path']
    path.write_text(path.read_text(encoding='utf-8')+'\n- [ ] 新的一行\n',encoding='utf-8')
    assert client.post(url+'/apply',json=payload).status_code==409
    assert len(client.get('/api/tasks').json())==2
