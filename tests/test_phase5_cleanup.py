"""Phase 5 cleanup guards: retired routes stay gone, legacy tables stay put."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway
from backend.app.scheduler import SchedulerService


def build(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    return database, client


def test_retired_http_routes_return_404(tmp_path):
    _, client = build(tmp_path)
    for method, path, body in (
        ('get', '/api/bazi/profile', None),
        ('post', '/api/bazi/profile/clear', {'confirm': True}),
        ('get', '/api/suggestions', None),
        ('get', '/api/profile', None),
        ('get', '/api/profile/activity', None),
        ('post', '/api/chat/preview', {'message': '你好'}),
        ('post', '/api/imports/share/preview', {'url': 'https://example.invalid/share'}),
        ('get', '/api/memories/1/usage', None),
        ('get', '/api/journal/2026-10-02', None),
        ('post', '/api/journal/2026-10-02/review', {}),
        ('post', '/api/journal/monthly-review', {'month': '2026-10'}),
    ):
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert response.status_code == 404, (method, path, response.status_code, response.text)


def test_retired_capabilities_are_not_advertised(tmp_path):
    _, client = build(tmp_path)
    capabilities = client.get('/api/config/status').json()['capabilities']
    for key in ('bazi', 'profile', 'chat_preview', 'memory_usage', 'share_import', 'suggestions'):
        assert key not in capabilities, key
    assert len(capabilities) == 44


def test_legacy_tables_survive_and_have_no_writers(tmp_path):
    database, _ = build(tmp_path)
    with database.connect() as connection:
        tables = {row['name'] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {'bazi_profiles', 'suggestions', 'journal_entries', 'journal_actions'} <= tables
        assert connection.execute('SELECT COUNT(*) FROM bazi_profiles').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM suggestions').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM journal_actions').fetchone()[0] == 0


def test_scheduler_no_longer_generates_suggestions(tmp_path):
    database, _ = build(tmp_path)
    result = SchedulerService(database).run_due(
        now=datetime(2026, 10, 4, 8, 2, tzinfo=ZoneInfo('Asia/Shanghai'))
    )
    assert set(result) == {'created', 'existing'}
    with database.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM suggestions').fetchone()[0] == 0
