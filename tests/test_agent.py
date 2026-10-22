"""Local read-only agent loop: planning rules, digest text and cloud boundary."""

from datetime import date

from fastapi.testclient import TestClient

from backend.app import agent
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


class FakeCloud:
    configured = True

    def __init__(self):
        self.calls = 0
        self.last_question = None
        self.last_evidence = None
        self.last_kwargs = None

    def public_status(self):
        return {'model': 'fake', 'provider': 'fake'}

    async def generate(self, question, evidence, **kwargs):
        self.calls += 1
        self.last_question = question
        self.last_evidence = evidence
        self.last_kwargs = kwargs
        return {'answer': '云端回答', 'usage': {'total_tokens': 5}, 'mode': 'cloud'}


class FakeTools:
    """Minimal read-only tool double for planner tests."""

    def __init__(self):
        self.calls = []

    def memories(self, query, limit=6):
        self.calls.append(('memories', query, limit))
        return {'count': 2, 'memories': [{'title': '跑步', 'content': '我每周跑步三次。'}]}

    def today(self):
        self.calls.append(('today',))
        return {'journal': {'recorded': False}, 'tasks': {'open': 3, 'due_today': 1, 'overdue': 1},
                'body': {'recorded': False}}

    def goals(self, limit=5):
        self.calls.append(('goals', limit))
        return {'goals': [{'title': '完成数据手册'}], 'open_task_count': 2, 'next_tasks': []}

    def habits(self, days=30):
        self.calls.append(('habits', days))
        return {'from_date': '2026-09-05', 'habits': [{'title': '早睡', 'done': 4}]}

    def vault(self):
        self.calls.append(('vault',))
        return {'markdown_files': 3, 'read_only': True}

    def body_summary(self, days=7):
        self.calls.append(('body', days))
        return {'from_date': '2026-09-28', 'recorded_days': 3, 'sleep_minutes_total': 1260,
                'exercise_minutes_total': 90}

    def documents_summary(self, limit=5):
        self.calls.append(('documents', limit))
        return {'documents': 7, 'recent': [{'title': '资料甲'}]}

    def imports_summary(self):
        self.calls.append(('imports',))
        return {'packages': 1, 'imported_notes': 3, 'history_versions': 2, 'notes_index_stale': 1,
                'attachment_files': 4, 'attachment_bytes': 2048}

    def vault_audit(self):
        self.calls.append(('vault_audit',))
        return {'checked': 7, 'issues': 2, 'by_code': {'missing_id': 2}}


def build(tmp_path, monkeypatch, gateway=None):
    monkeypatch.setattr('backend.app.tools.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.journal_shared.shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr('backend.app.habits.shanghai_today', lambda: date(2026, 10, 4))
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = gateway or ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return database, client, gateway


def events(database, event_type):
    with database.connect() as connection:
        rows = connection.execute('SELECT detail_json FROM audit_events WHERE event_type=? ORDER BY id', (event_type,)).fetchall()
    return [row['detail_json'] for row in rows]


def test_plan_always_starts_with_memories_and_never_exceeds_three_steps():
    plan = agent.plan_steps('我今天的目标完成得怎么样？顺便看看最近的学习和习惯')
    assert plan[0]['tool'] == 'memories'
    assert len(plan) == agent.MAX_STEPS
    tools = [step['tool'] for step in plan]
    assert tools[1] in {'today', 'goals', 'habits', 'vault'}
    assert all(step['tool'] in agent.TOOL_LABELS for step in plan)
    quiet = agent.plan_steps('你好')
    assert [step['tool'] for step in quiet] == ['memories']


def test_run_executes_only_the_planned_read_tools_and_summarizes():
    service = FakeTools()
    result = agent.run(service, '我今天的目标完成得怎么样？', cloud_available=True)
    assert result['tools'] == ['memories', 'today', 'goals']
    assert [step['label'] for step in result['steps']] == ['已确认记忆', '今天的记录', '目标与任务']
    assert '找到 2 条已确认记忆' in result['digest']
    assert '未完成任务 3 项' in result['digest']
    assert '云端模式只发送' in result['digest']
    assert all(call[0] in agent.TOOL_LABELS for call in service.calls)
    assert len(service.calls) == 3

    local_only = agent.run(FakeTools(), '我今天的目标完成得怎么样？', cloud_available=False)
    assert '不会发送给任何外部服务' in local_only['digest']


def test_new_intents_reach_body_documents_and_audit_tools():
    service = FakeTools()
    body = agent.run(service, '我最近的睡眠和运动怎么样？', cloud_available=False)
    assert [step['tool'] for step in body['steps']] == ['memories', 'today', 'body']
    assert any('身体记录' in step['label'] for step in body['steps'])
    assert '不含体重' in body['digest']

    audit = agent.run(service, '我的资料体检有什么问题？', cloud_available=False)
    assert [step['tool'] for step in audit['steps']] == ['memories', 'documents', 'vault_audit']
    assert '体检 7 篇，发现 2 个问题' in audit['digest']


def test_import_intent_reaches_the_imports_tool():
    service = FakeTools()
    result = agent.run(service, '我的 Wolai 导入和附件历史怎么样？', cloud_available=False)
    assert [step['tool'] for step in result['steps']] == ['memories', 'imports']
    assert any('资料导入' in step['label'] for step in result['steps'])
    assert '导入包 1 个、已导入笔记 3 篇、历史版本 2 个、索引待刷新 1 篇' in result['digest']
    assert ('imports',) in service.calls


def test_a_greeting_gets_no_digest_when_nothing_was_found():
    service = FakeTools()
    service.memories = lambda query, limit=6: {'count': 0, 'memories': []}
    result = agent.run(service, '你好', cloud_available=False)
    assert result['steps'][0]['tool'] == 'memories'
    assert result['digest'] == ''


def test_chat_returns_local_steps_and_audits_them(tmp_path, monkeypatch):
    database, client, _ = build(tmp_path, monkeypatch)
    payload = client.post('/api/chat', json={'message': '我今天还有哪些任务？'}).json()
    assert client.get('/api/config/status').json()['capabilities']['agent'] is True
    assert payload['mode'] == 'local'
    assert payload['agent']['enabled'] is True
    tools = [step['tool'] for step in payload['agent']['steps']]
    assert tools == ['memories', 'today', 'goals']
    assert '我做的本地步骤' in payload['answer']
    assert '不会发送给任何外部服务' in payload['answer']
    logged = events(database, 'agent_planned')
    assert len(logged) == 1
    assert '"tools": ["memories", "today", "goals"]' in logged[0]

    plain = client.post('/api/chat', json={'message': '我今天还有哪些任务？', 'agent': False}).json()
    assert plain['agent'] == {'enabled': False, 'steps': [],
                              'note': '本地步骤只在这台机器上执行；云端模式只发送问题与相关已确认记忆；'
                                      'Harness 模式由 dsh 决定上下文，只挂载只读工具。'}
    assert '我做的本地步骤' not in plain['answer']


def test_cloud_mode_still_sends_only_the_question_and_memories(tmp_path, monkeypatch):
    gateway = FakeCloud()
    database, client, _ = build(tmp_path, monkeypatch, gateway=gateway)
    client.post('/api/notes', json={
        'title': '跑步习惯', 'content': '我今天的目标是完成数据手册，另外每周跑步三次。', 'type': 'preference',
    })
    candidate = client.get('/api/memories?status=candidate').json()[0]
    client.patch(f"/api/memories/{candidate['id']}/status", json={'status': 'active'})

    payload = client.post('/api/chat', json={'message': '我今天的目标完成得怎么样？', 'use_cloud': True}).json()
    assert payload['mode'] == 'model'
    assert gateway.calls == 1
    assert gateway.last_question == '我今天的目标完成得怎么样？'
    # The cloud only ever receives the question plus confirmed-memory evidence.
    assert [item['content'] for item in gateway.last_evidence] == ['我今天的目标是完成数据手册，另外每周跑步三次。']
    assert gateway.last_kwargs == {'general_conversation': False}
    assert all('我做的本地步骤' not in str(item) for item in gateway.last_evidence)
    # The user still sees what the local agent looked at, marked as local-only.
    assert [step['tool'] for step in payload['agent']['steps']] == ['memories', 'today', 'goals']
    assert '云端模式只发送' in payload['answer'] or payload['answer'] == '云端回答'
