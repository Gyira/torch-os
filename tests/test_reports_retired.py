from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def test_retired_reports_are_not_exposed_or_started(tmp_path, monkeypatch):
    from backend.app.scheduler import SchedulerService

    calls = []
    monkeypatch.setattr(SchedulerService, 'run_due', lambda *a, **kw: calls.append(True))
    vault = tmp_path / 'vault'
    vault.mkdir()
    app = create_app(database=Database(tmp_path / 'test.sqlite3'), vault_dir=vault,
                     host='127.0.0.1', security_environ={}, runtime_label='qa', enable_dsh=False)
    with TestClient(app) as client:
        client.get('/')
        capabilities = client.get('/api/config/status').json()['capabilities']
        assert capabilities['scheduler'] is False
        assert capabilities['scheduler_weekly'] is False
        assert client.get('/api/schedule').status_code == 404
        assert client.post('/api/schedule/run-now', json={'kind': 'morning'}).status_code == 404
    assert calls == []
