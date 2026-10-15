"""Monthly review drafts use temporary databases and a frozen Shanghai date."""

from datetime import date
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import body, habits, journal, learning
from backend.app.application import APP_VERSION, create_app
from backend.app.db import Database
from backend.app.journal import create_journal_router
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, 'shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr(learning, 'shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr(habits, 'shanghai_today', lambda: date(2026, 10, 4))
    monkeypatch.setattr(body, 'shanghai_today', lambda: date(2026, 10, 4))
    # Freeze the completion clock too: otherwise the real UTC time can land on the
    # next Shanghai day and fall outside the frozen reporting range.
    monkeypatch.setattr('backend.app.goals.utc_now', lambda: '2026-10-04T02:00:00+00:00')
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={})
    client = TestClient(app)
    client.get('/')
    return app, client, database


def seed_october(client, tmp_path):
    assert client.put('/api/journal/2026-10-02', json={'revision': 0, 'content': '虚构十月记录二'}).status_code == 200
    assert client.put('/api/journal/2026-10-03', json={'revision': 0, 'content': '虚构十月记录三'}).status_code == 200
    assert client.put('/api/journal/2026-09-20', json={'revision': 0, 'content': '虚构九月记录'}).status_code == 200

    task = client.post('/api/tasks', json={'title': '虚构完成任务'})
    assert task.status_code in {200, 201}, task.text
    task_id = task.json()['id']
    assert client.patch(f'/api/tasks/{task_id}', json={'status': 'completed'}).status_code == 200

    subject = client.post('/api/learning/subjects', json={'title': '虚构科目', 'weekly_target_minutes': 120})
    assert subject.status_code in {200, 201}, subject.text
    session = client.post('/api/learning/sessions', json={
        'subject_id': subject.json()['id'], 'study_date': '2026-10-03', 'minutes': 40,
        'request_key': str(uuid4()),
    })
    assert session.status_code in {200, 201}, session.text

    habit = client.post('/api/habits', json={
        'title': '虚构习惯', 'minimum_action': '做一次', 'start_date': '2026-10-01',
        'weekdays': list(range(7)),
    })
    assert habit.status_code in {200, 201}, habit.text
    entry = client.put(f"/api/habits/{habit.json()['id']}/entries/2026-10-03",
                       json={'revision': 0, 'status': 'done'})
    assert entry.status_code == 200, entry.text

    body_entry = client.put('/api/body/entries/2026-10-02', json={'revision': 0, 'sleep_minutes': 420})
    assert body_entry.status_code == 200, body_entry.text


def audit_count(database):
    with database.connect() as connection:
        return connection.execute('SELECT COUNT(*) AS n FROM audit_events').fetchone()['n']


def test_monthly_review_reports_only_recorded_facts_for_current_month(tmp_path, monkeypatch):
    app, client, database = build_client(tmp_path, monkeypatch)
    assert TestClient(app).post('/api/journal/monthly-review', json={'month': '2026-10'}).status_code == 401
    seed_october(client, tmp_path)

    before = audit_count(database)
    response = client.post('/api/journal/monthly-review', json={'month': '2026-10'})
    assert response.status_code == 200, response.text
    assert audit_count(database) == before  # the draft never writes audit rows
    payload = response.json()
    assert payload['month'] == '2026-10'
    assert payload['range'] == {'start': '2026-10-01', 'end': '2026-10-04',
                                'month_end': '2026-10-31', 'partial': True}
    assert payload['journal'] == {'days_recorded': 2}
    assert payload['tasks']['completed'] == 1
    assert payload['tasks']['completed_examples'] == ['虚构完成任务']
    assert payload['study'] == {'minutes': 40, 'days_recorded': 1,
                                'subjects': [{'title': '虚构科目', 'minutes': 40}]}
    assert payload['habits'] == {'scheduled': 4, 'done': 1, 'skipped': 0, 'unrecorded': 3}
    assert payload['body'] == {'days_recorded': 1}
    assert '已记录日记 2 天' in payload['summary']
    assert '学习 40 分钟' in payload['summary']
    assert '本月至今' in payload['summary']
    assert '不做评分' in payload['warning']
    assert len(payload['reflection_prompts']) == 3
    focus = payload['next_month_focus']
    assert 1 <= len(focus) <= 3
    assert all({'kind', 'text', 'evidence'} <= set(item) for item in focus)
    assert any('虚构科目' in item['text'] for item in focus)
    assert any('每周至少记录 1 天' in item['text'] for item in focus)
    assert '不会自动创建任务' in payload['next_month_note']
    assert str(payload).find('虚构九月记录') == -1


def test_monthly_review_of_a_past_month_uses_the_whole_month(tmp_path, monkeypatch):
    _, client, _ = build_client(tmp_path, monkeypatch)
    seed_october(client, tmp_path)
    response = client.post('/api/journal/monthly-review', json={'month': '2026-09'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['range'] == {'start': '2026-09-01', 'end': '2026-09-30',
                                'month_end': '2026-09-30', 'partial': False}
    assert payload['journal'] == {'days_recorded': 1}
    assert payload['tasks']['completed'] == 0
    assert payload['study'] == {'minutes': 0, 'days_recorded': 0, 'subjects': []}
    assert payload['habits'] == {'scheduled': 0, 'done': 0, 'skipped': 0, 'unrecorded': 0}
    assert payload['body'] == {'days_recorded': 0}
    assert '整月' in payload['summary']


def test_monthly_review_validates_month_and_works_without_optional_tables(tmp_path, monkeypatch):
    _, client, _ = build_client(tmp_path, monkeypatch)
    assert client.post('/api/journal/monthly-review', json={'month': '2026-1'}).status_code == 422
    assert client.post('/api/journal/monthly-review', json={'month': '2026-13'}).status_code == 422
    assert client.post('/api/journal/monthly-review', json={'month': '2099-01'}).status_code == 422
    assert client.post('/api/journal/monthly-review', json={'month': '2026-10', 'extra': True}).status_code == 422

    # A minimal journal-only app has no tasks/study/habits/body tables; facts default to zero.
    monkeypatch.setattr(journal, 'shanghai_today', lambda: date(2026, 10, 4))
    database = Database(tmp_path / 'journal_only.sqlite3')
    app = FastAPI()
    app.include_router(create_journal_router(database, tmp_path / 'vault'))
    minimal = TestClient(app)
    response = minimal.post('/api/journal/monthly-review', json={'month': '2026-10'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['journal'] == {'days_recorded': 0}
    assert payload['tasks']['completed'] == 0
    assert payload['study']['minutes'] == 0
    assert payload['habits']['scheduled'] == 0
    assert payload['body']['days_recorded'] == 0
    # 空月份也会给一个最小起点的草稿，但不会编造“保持记录”的建议。
    assert [item['kind'] for item in payload['next_month_focus']] == ['study']
    assert '最小起点' in payload['next_month_focus'][0]['text']

    capabilities = client.get('/api/config/status').json()['capabilities']
    assert capabilities['monthly_review'] is True
    assert client.get('/api/health').json()['version'] == APP_VERSION
