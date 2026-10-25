"""Restored-copy inventory and verification tests use only temporary data."""

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.application import APP_VERSION, create_app
from backend.app.backup import create_backup, restore_backup
from backend.app.db import Database


def build_client(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    app = create_app(database, tmp_path / 'vault', security_environ={})
    client = TestClient(app)
    client.get('/')
    return app, client, database


def make_copy(tmp_path, client, database, name=None):
    response = client.post('/api/notes', json={'title': '虚构记录', 'content': '这是虚构的测试内容。', 'type': 'fact'})
    assert response.status_code == 201, response.text
    backup = create_backup(database, tmp_path / 'vault', tmp_path / 'backups')
    parent = tmp_path / 'restored'
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / (name or uuid4().hex)
    restore_backup(tmp_path / 'backups' / backup['filename'], target, allowed_parent=parent)
    return target


def audit_count(database):
    with database.connect() as connection:
        return connection.execute('SELECT COUNT(*) AS n FROM audit_events').fetchone()['n']


def test_restored_list_and_verify_are_read_only(tmp_path):
    _, client, database = build_client(tmp_path)
    empty = client.get('/api/backups/restored')
    assert empty.status_code == 200, empty.text
    assert empty.json()['total'] == 0
    assert empty.json()['copies'] == []
    assert '不会删除' in empty.json()['note'] or '不删除' in empty.json()['note']

    target = make_copy(tmp_path, client, database)
    before = audit_count(database)
    listed = client.get('/api/backups/restored')
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload['total'] == 1
    assert payload['total_bytes'] > 0
    item = payload['copies'][0]
    assert item['name'] == target.name
    assert item['has_database'] is True
    assert item['file_count'] >= 2
    assert item['vault_files'] >= 1
    assert item['modified_at']

    verified = client.get(f"/api/backups/restored/{target.name}/verify")
    assert verified.status_code == 200, verified.text
    result = verified.json()
    assert result['ok'] is True
    assert result['sqlite_integrity'] == 'ok'
    assert result['sqlite_user_version'] == 0
    assert result['file_count'] == item['file_count']
    assert result['total_bytes'] == item['total_bytes']
    assert result['vault_files'] == item['vault_files']
    assert '不改动' in result['note']
    assert audit_count(database) == before  # list and verify never write audit rows

    # The database inside the copy still opens read-only and holds the snapshot.
    with database.connect() as connection:
        live = connection.execute('SELECT COUNT(*) AS n FROM memories').fetchone()['n']
    assert live >= 1


def test_restored_routes_require_session_and_validate_names(tmp_path):
    app, client, database = build_client(tmp_path)
    assert TestClient(app).get('/api/backups/restored').status_code == 401
    assert TestClient(app).get(f"/api/backups/restored/{'a' * 32}/verify").status_code == 401
    assert client.get('/api/backups/restored/../verify').status_code in {400, 404, 422}
    assert client.get('/api/backups/restored/not-a-copy/verify').status_code == 400
    assert client.get(f"/api/backups/restored/{'a' * 31}/verify").status_code == 400
    assert client.get(f"/api/backups/restored/{'a' * 32}/verify").status_code == 404

    # A regular file with a valid-looking name is not a restored copy either.
    parent = tmp_path / 'restored'
    parent.mkdir(parents=True, exist_ok=True)
    (parent / ('b' * 32)).write_text('not a directory', encoding='utf-8')
    assert client.get(f"/api/backups/restored/{'b' * 32}/verify").status_code == 404
    listed = client.get('/api/backups/restored').json()
    assert listed['total'] == 0

    capabilities = client.get('/api/config/status').json()['capabilities']
    assert capabilities['restored_copies'] is True
    assert client.get('/api/health').json()['version'] == APP_VERSION


def test_restored_verify_reports_a_corrupted_database_without_content(tmp_path):
    _, client, database = build_client(tmp_path)
    target = make_copy(tmp_path, client, database)
    (target / 'torch_os.sqlite3').write_bytes(b'not a sqlite database')
    response = client.get(f"/api/backups/restored/{target.name}/verify")
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert 'SQLite' in detail or '数据库' in detail
    assert '虚构' not in detail
    listed = client.get('/api/backups/restored').json()
    assert listed['total'] == 1
    assert listed['copies'][0]['has_database'] is True
