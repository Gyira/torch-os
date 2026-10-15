from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def growth_client(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.habits.shanghai_today',lambda:date(2026,10,3))
    monkeypatch.setattr('backend.app.learning.shanghai_today',lambda:date(2026,10,3))
    database=Database(tmp_path/'torch_os.sqlite3')
    gateway=ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1)
    app=create_app(database,tmp_path/'vault',gateway,security_environ={})
    client=TestClient(app)
    client.get('/')
    return app,client,database


def test_habits_and_study_are_authorized_independent_records(tmp_path,monkeypatch):
    app,client,database=growth_client(tmp_path,monkeypatch)
    assert TestClient(app).get('/api/habits').status_code==401
    assert TestClient(app).get('/api/learning/subjects').status_code==401
    assert client.post('/api/habits',headers={'origin':'https://evil.example'},json={}).status_code==403
    caps=client.get('/api/config/status').json()['capabilities']
    assert caps['habits'] is caps['learning'] is True
    habit=client.post('/api/habits',json={'title':'虚构阅读习惯','minimum_action':'读一页','start_date':'2026-10-01'}).json()
    assert habit['revision']==1
    done=client.put(f"/api/habits/{habit['id']}/entries/2026-10-01",json={'revision':0,'status':'done','note':'完成演示记录'})
    assert done.status_code==200
    skipped=client.put(f"/api/habits/{habit['id']}/entries/2026-10-02",json={'revision':0,'status':'skipped'})
    assert skipped.status_code==200
    summary=client.get(f"/api/habits/{habit['id']}/summary?from_date=2026-10-01&to_date=2026-10-03").json()
    assert summary['done']==summary['skipped']==summary['unrecorded']==1
    assert summary['completion_rate']==0.5
    assert client.put(f"/api/habits/{habit['id']}/entries/2026-10-01",json={'revision':0,'status':'unrecorded'}).status_code==409

    goal=client.post('/api/goals',json={'title':'虚构学习目标'}).json()
    subject=client.post('/api/learning/subjects',json={'title':'演示科目','goal_id':goal['id'],'weekly_target_minutes':120}).json()
    payload={'subject_id':subject['id'],'study_date':'2026-10-02','minutes':30,'content':'仅为隔离测试','next_step':'再读一页','request_key':str(uuid4())}
    first=client.post('/api/learning/sessions',json=payload).json()
    repeat=client.post('/api/learning/sessions',json=payload).json()
    assert first['created'] is True and repeat['created'] is False
    assert first['session']['id']==repeat['session']['id']
    assert first['session']['goal_id_at_recording']==goal['id']
    stats=client.get('/api/learning/weekly?week_start=2026-09-28').json()
    assert stats['totals']['studied_minutes']==30 and stats['totals']['progress_percent']==25
    assert client.get('/api/tasks').json()==[]
    assert database.list_memories()==[]

    archive=client.post('/api/backups')
    assert archive.status_code==201, archive.text
    restored=client.post(f"/api/backups/{archive.json()['filename']}/restore")
    assert restored.status_code==201,restored.text
    restored_db=Database(Path(restored.json()['database_path']))
    with restored_db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM habit_entries').fetchone()[0]==2
        assert connection.execute('SELECT count(*) FROM study_sessions').fetchone()[0]==1


def test_learning_correction_and_archive_do_not_rewrite_history(tmp_path,monkeypatch):
    _,client,_=growth_client(tmp_path,monkeypatch)
    subject=client.post('/api/learning/subjects',json={'title':'历史科目','weekly_target_minutes':60}).json()
    session=client.post('/api/learning/sessions',json={'subject_id':subject['id'],'study_date':'2026-10-02','minutes':30,'request_key':str(uuid4())}).json()['session']
    changed=client.patch(f"/api/learning/sessions/{session['id']}",json={'revision':session['revision'],'minutes':20})
    assert changed.status_code==200 and changed.json()['minutes']==20
    assert client.patch(f"/api/learning/sessions/{session['id']}",json={'revision':session['revision'],'minutes':15}).status_code==409
    client.patch(f"/api/learning/subjects/{subject['id']}",json={'revision':subject['revision'],'status':'archived'})
    stats=client.get('/api/learning/weekly?week_start=2026-09-28').json()['totals']
    assert stats['studied_minutes']==20 and stats['active_studied_minutes']==0 and stats['progress_percent'] is None
