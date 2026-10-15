from fastapi.testclient import TestClient
import pytest

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def app_at(path, label='personal'):
    return create_app(Database(path/'app.sqlite3'), path/'vault',
                      ModelGateway(provider='local',base_url='',model='',api_key='',timeout=1),
                      runtime_label=label, security_environ={})


@pytest.mark.parametrize('same_database', [False, True])
def test_cookie_names_do_not_collide_across_ports_and_qa(tmp_path, same_database):
    personal=TestClient(app_at(tmp_path/'personal'),base_url='http://127.0.0.1:8765')
    qa_path=tmp_path/'personal' if same_database else tmp_path/'qa'
    qa=TestClient(app_at(qa_path,'qa'),base_url='http://127.0.0.1:8766')
    personal.get('/')
    personal_cookies=dict(personal.cookies)
    qa.cookies.update(personal_cookies)
    assert qa.get('/api/documents').status_code==401
    qa.get('/')
    combined=dict(qa.cookies)
    assert len(combined)==2
    personal.cookies.update(combined)
    assert personal.get('/api/documents').status_code==qa.get('/api/documents').status_code==200
    assert personal.get('/api/health').json()['runtime_label']=='personal'
    assert qa.get('/api/config/status').json()['runtime_label']=='qa'
    stranger=TestClient(personal.app,base_url='http://127.0.0.1:8765')
    stranger.cookies.update({key:value for key,value in combined.items() if key not in personal_cookies})
    assert stranger.get('/api/documents').status_code==401


def test_qa_cannot_enable_cloud_or_ambiguous_runtime_label(tmp_path):
    cloud=ModelGateway(provider='deepseek',base_url='https://example.invalid',model='fake',api_key='test-only',timeout=1)
    with pytest.raises(ValueError,match='QA runtime'):
        create_app(Database(tmp_path/'qa.sqlite3'),tmp_path/'vault',cloud,runtime_label='qa',security_environ={})
    with pytest.raises(ValueError,match='runtime label'):
        app_at(tmp_path/'invalid','made-up')
