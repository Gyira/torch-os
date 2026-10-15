"""Model-key settings tests use a temporary .env and an injected gateway only."""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway

KEY = 'sk-' + 'a' * 32


def build(tmp_path, *, runtime_label='personal'):
    env_file = tmp_path / '.env'
    env_file.write_text(
        'MODEL_PROVIDER=openai-compatible\nMODEL_BASE_URL=https://api.deepseek.com/v1\n'
        'MODEL_NAME=deepseek-chat\nMODEL_API_KEY=\n# keep this comment\nOTHER=keep\n',
        encoding='utf-8',
    )
    gateway = ModelGateway(provider='openai-compatible', base_url='https://api.deepseek.com/v1',
                           model='deepseek-chat', api_key='', timeout=1)
    database = Database(tmp_path / 'app.sqlite3')
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={},
                     runtime_label=runtime_label, env_file=env_file)
    return app, env_file, gateway, database


def session_client(app):
    client = TestClient(app)
    client.get('/')
    return client


def test_model_key_requires_session_and_strict_shape(tmp_path):
    app, env_file, _, _ = build(tmp_path)
    assert TestClient(app).post('/api/config/model-key', json={'api_key': KEY, 'confirm': True}).status_code == 401
    client = session_client(app)
    assert client.post('/api/config/model-key', json={'api_key': KEY, 'confirm': False}).status_code == 422
    assert client.post('/api/config/model-key', json={'api_key': 'short', 'confirm': True}).status_code == 422
    assert client.post('/api/config/model-key', json={'api_key': 'sk-abc\nevil=1', 'confirm': True}).status_code == 422
    assert client.post('/api/config/model-key', json={'api_key': 'sk-abc def', 'confirm': True}).status_code == 422
    assert client.post('/api/config/model-key', json={'api_key': 'x' * 201, 'confirm': True}).status_code == 422
    assert client.post('/api/config/model-key', json={'api_key': KEY, 'confirm': 1}).status_code == 422
    assert KEY not in env_file.read_text(encoding='utf-8')


def test_saving_a_key_writes_only_env_and_hot_reloads(tmp_path):
    app, env_file, gateway, database = build(tmp_path)
    client = session_client(app)
    assert client.get('/api/config/status').json()['model_configured'] is False
    response = client.post('/api/config/model-key', json={'api_key': KEY, 'confirm': True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['saved'] is True and payload['configured'] is True and payload['model_name'] == 'deepseek-chat'
    assert KEY not in response.text
    text = env_file.read_text(encoding='utf-8')
    assert f'MODEL_API_KEY={KEY}' in text and '# keep this comment' in text and 'OTHER=keep' in text
    assert text.count('MODEL_API_KEY=') == 1
    assert gateway.configured is True
    assert client.get('/api/config/status').json()['model_configured'] is True
    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='model_key_updated'")]
    assert len(rows) == 1 and KEY not in rows[0]['detail_json']


def test_a_second_key_replaces_the_first_without_growing_the_file(tmp_path):
    app, env_file, _, _ = build(tmp_path)
    client = session_client(app)
    first = 'sk-' + 'b' * 32
    second = 'sk-' + 'c' * 32
    assert client.post('/api/config/model-key', json={'api_key': first, 'confirm': True}).status_code == 200
    assert client.post('/api/config/model-key', json={'api_key': second, 'confirm': True}).status_code == 200
    text = env_file.read_text(encoding='utf-8')
    assert text.count('MODEL_API_KEY=') == 1
    assert second in text and first not in text and 'OTHER=keep' in text


def test_isolated_runtime_refuses_key_writes(tmp_path):
    app, env_file, gateway, _ = build(tmp_path, runtime_label='qa')
    client = session_client(app)
    assert client.get('/api/config/status').json()['capabilities']['model_key_write'] is False
    response = client.post('/api/config/model-key', json={'api_key': KEY, 'confirm': True})
    assert response.status_code == 409
    assert KEY not in env_file.read_text(encoding='utf-8') and gateway.configured is False
