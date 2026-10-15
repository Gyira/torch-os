from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def test_backup_restore_from_api_preserves_active_database(tmp_path):
    database=Database(tmp_path/'torch_os.sqlite3')
    gateway=ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1)
    app=create_app(database,tmp_path/'vault',gateway,security_environ={})
    client=TestClient(app)
    client.get('/')
    client.post('/api/notes',json={'title':'资料','content':'这是备份验收的虚构资料。'})
    created=client.post('/api/backups')
    assert created.status_code==201
    name=created.json()['filename']
    assert client.get('/api/backups').json()[0]['filename']==name
    before=database.list_documents()
    restored=client.post(f'/api/backups/{name}/restore')
    assert restored.status_code==201
    directory=Path(restored.json()['target_dir'])
    assert directory.is_relative_to(tmp_path/'restored')
    copy=Database(directory/'torch_os.sqlite3')
    assert copy.list_documents()==before==database.list_documents()
    assert client.post('/api/backups/not-present.zip/restore').status_code==404
