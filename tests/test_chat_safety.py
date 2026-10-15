import math

import pytest
from fastapi.testclient import TestClient
from backend.app.application import create_app, local_answer
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway, ModelGatewayError


class FakeCloud:
    configured=True
    def __init__(self,action=lambda:None,fail=False):
        self.action=action
        self.fail=fail
        self.calls=0
    def public_status(self):
        return {'model':'fake','provider':'fake'}
    async def generate(self,question,evidence,**kwargs):
        self.calls+=1
        self.last_kwargs=kwargs
        self.action()
        if self.fail:
            raise ModelGatewayError('model_unauthorized')
        return {'answer':'来自云端的旧职业：建筑师','usage':{'total_tokens':3},'mode':'cloud'}


def evidence_item(title="记录", *, low_confidence=False):
    return {"title": title, "content": f"{title}的内容", "confidence": "explicit",
            "low_confidence": low_confidence}


def test_local_answer_states_insufficiency_when_every_evidence_item_is_low_confidence():
    answer = local_answer([evidence_item("散步记录", low_confidence=True)])
    assert "没有找到与问题直接相关的已确认记忆" in answer
    assert "不能当作对这个问题的回答" in answer
    assert "低置信（仅语义相关）" in answer
    assert "以下是已确认记忆的直接摘录" not in answer


def test_local_answer_keeps_normal_header_when_any_evidence_is_direct():
    answer = local_answer([
        evidence_item("播客记录"),
        evidence_item("散步记录", low_confidence=True),
    ])
    assert "以下是已确认记忆的直接摘录" in answer
    assert "播客记录 · 已确认记录\n" in answer
    assert "散步记录 · 已确认记录 · 低置信（仅语义相关）" in answer


class LowOnlyProvider:
    """Only a weak semantic match exists for the probe question."""

    name = "fake-low-only"

    def embed(self, texts):
        vectors = []
        for text in texts:
            if "如何提高外语水平" in text:
                vectors.append([1.0, 0.0])
            elif "周末去公园散步" in text:
                vectors.append([0.42, math.sqrt(1 - 0.42 ** 2)])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def test_chat_says_unknown_when_only_low_confidence_semantic_matches_exist(tmp_path):
    database = Database(tmp_path / "app.sqlite3", embedding_provider=LowOnlyProvider())
    app = create_app(database, tmp_path / "vault", None, security_environ={})
    client = TestClient(app)
    client.get("/")
    note = client.post("/api/notes", json={"title": "散步记录", "type": "note", "content": "周末去公园散步"}).json()
    memory_id = note["memories"][0]["id"]
    assert client.patch(f"/api/memories/{memory_id}/status", json={"status": "active"}).status_code == 200

    result = client.post("/api/chat", json={"message": "如何提高外语水平"}).json()
    assert result["mode"] == "local"
    assert "没有找到与问题直接相关的已确认记忆" in result["answer"]
    assert "低置信（仅语义相关）" in result["answer"]
    assert result["citations"] and result["citations"][0]["low_confidence"] is True


@pytest.mark.parametrize('action',['delete','change','restrict','withdraw'])
def test_source_changed_while_cloud_waits_is_not_returned(tmp_path,action):
    gateway=FakeCloud()
    db=Database(tmp_path/'app.sqlite3')
    app=create_app(db,tmp_path/'vault',gateway,security_environ={})
    client=TestClient(app)
    client.get('/')
    note=client.post('/api/notes',json={'title':'个人档案','type':'fact','content':'我的职业是建筑师。'}).json()
    mid=note['memories'][0]['id']
    client.patch(f'/api/memories/{mid}/status',json={'status':'active'})
    path=tmp_path/'vault'/note['document']['path']
    def mutation():
        if action=='delete':
            path.unlink()
        elif action=='change':
            path.write_text('职业已经变更。',encoding='utf-8')
        elif action=='restrict':
            target=tmp_path/'vault'/'relationships'
            target.mkdir()
            path.rename(target/path.name)
        else:
            db.update_memory_status(mid,'rejected')
    gateway.action=mutation
    result=client.post('/api/chat',json={'message':'我是谁？','use_cloud':True}).json()
    assert result['mode']=='fallback'
    assert result['warning']
    assert '建筑师' not in result['answer']
    assert not result['citations']


def test_model_failure_is_labelled_and_not_silently_reported_as_cloud(tmp_path):
    gateway=FakeCloud(fail=True)
    app=create_app(Database(tmp_path/'app.sqlite3'),tmp_path/'vault',gateway,security_environ={})
    client=TestClient(app)
    client.get('/')
    note=client.post('/api/notes',json={'title':'个人档案','type':'fact','content':'我的职业是建筑师。'}).json()
    mid=note['memories'][0]['id']
    client.patch(f'/api/memories/{mid}/status',json={'status':'active'})
    local=client.post('/api/chat',json={'message':'我是谁？'}).json()
    assert local['mode']=='local' and gateway.calls==0
    result=client.post('/api/chat',json={'message':'我是谁？','use_cloud':True}).json()
    assert result['mode']=='fallback' and result['warning'] and gateway.calls==1


def test_cloud_can_answer_general_questions_without_confirmed_memories(tmp_path):
    gateway = FakeCloud()
    db = Database(tmp_path / 'app.sqlite3')
    app = create_app(db, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    local = client.post('/api/chat', json={'message': '你好，你会做什么？'}).json()
    assert local['mode'] == 'local' and gateway.calls == 0
    assert '云端' in local['answer'] and 'MODEL_API_KEY' not in local['answer']
    cloud = client.post('/api/chat', json={'message': '你好，你会做什么？', 'use_cloud': True}).json()
    assert cloud['mode'] == 'model' and gateway.calls == 1
    assert gateway.last_kwargs == {'general_conversation': True}
    assert cloud['citations'] == [] and '建筑师' in cloud['answer']


def test_local_answer_only_hints_cloud_when_it_is_configured(tmp_path):
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    db = Database(tmp_path / 'app.sqlite3')
    app = create_app(db, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    answer = client.post('/api/chat', json={'message': '你好'}).json()['answer']
    assert '云端' not in answer and '我还不知道这件事' in answer


def test_cloud_with_sources_keeps_the_evidence_mode(tmp_path):
    gateway = FakeCloud()
    db = Database(tmp_path / 'app.sqlite3')
    app = create_app(db, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    note = client.post('/api/notes', json={'title': '个人档案', 'type': 'fact', 'content': '我的职业是建筑师。'}).json()
    client.patch(f"/api/memories/{note['memories'][0]['id']}/status", json={'status': 'active'})
    result = client.post('/api/chat', json={'message': '我是谁？', 'use_cloud': True}).json()
    assert result['mode'] == 'model' and gateway.calls == 1
    assert gateway.last_kwargs == {'general_conversation': False}
    assert result['citations'] and result['citations'][0]['quote'] == '我的职业是建筑师。'
