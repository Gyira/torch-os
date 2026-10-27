"""Phase 5 cleanup guards: retired routes stay gone, legacy tables stay put."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.scheduler import SchedulerService


def build(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    app = create_app(database, tmp_path / 'vault', security_environ={}, env_file=tmp_path / '.env')
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


def test_retired_four_library_write_routes_return_404(tmp_path):
    _, client = build(tmp_path)
    for method, path, body in (
        ('post', '/api/capture', {'title': '一条灵感', 'body': '正文'}),
        ('post', '/api/notes/update', {'body': '改过的正文'}),
        ('post', '/api/notes/move', {'path': '03_知识库/00_收件箱/笔记.md', 'chapter': 'concept'}),
        ('post', '/api/reviews/weekly', {}),
        ('post', '/api/reviews/monthly', {}),
        ('post', '/api/ops/health/months/2026-10/rows', {'date': '2026-10-21'}),
        ('post', '/api/ops/health/months/2026-10/rows/abcdef123456',
         {'date': '2026-10-21', 'weight_kg': '68.1'}),
        ('delete', '/api/ops/health/months/2026-10/rows/abcdef123456', None),
        ('post', '/api/ops/health/months/2026-10/rows/abcdef123456/restore',
         {'date': '2026-10-21', 'weight_kg': '68.1'}),
        ('post', '/api/steward/proposals/apply', {'proposal_id': 'st-missing', 'confirm': True}),
        ('post', '/api/steward/proposals/apply-batch',
         {'proposal_ids': ['st-missing'], 'confirm': True}),
        ('post', '/api/journal/note/generate', {'day': '2026-10-21', 'mode': 'create'}),
    ):
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert response.status_code == 404, (method, path, response.status_code, response.text)



def test_retired_capabilities_are_not_advertised(tmp_path):
    _, client = build(tmp_path)
    capabilities = client.get('/api/config/status').json()['capabilities']
    for key in ('bazi', 'profile', 'chat_preview', 'memory_usage', 'share_import', 'suggestions', 'agent', 'harness_reset', 'model_key_write', 'semantic_search', 'workbench_polish'):
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
