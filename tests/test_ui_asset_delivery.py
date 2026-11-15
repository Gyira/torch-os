from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def test_ui_assets_compressed_without_changing_api_or_security_headers(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    app = create_app(database=Database(tmp_path / 'test.sqlite3'), vault_dir=vault,
                     host='127.0.0.1', security_environ={}, runtime_label='qa', enable_dsh=False)
    with TestClient(app) as client:
        plain = client.get('/assets/styles.css', headers={'Accept-Encoding': 'identity'})
        zipped = client.get('/assets/styles.css', headers={'Accept-Encoding': 'gzip'})
        assert plain.status_code == zipped.status_code == 200
        assert zipped.headers['content-encoding'] == 'gzip'
        assert 'accept-encoding' in zipped.headers['vary'].lower()
        assert zipped.content == plain.content == (Path(__file__).parents[1] / 'frontend/styles.css').read_bytes()
        assert zipped.headers['content-security-policy'] == plain.headers['content-security-policy']
        assert 'content-encoding' not in client.get('/api/health', headers={'Accept-Encoding': 'gzip'}).headers
        assert client.get('/assets/missing-file.js', headers={'Accept-Encoding': 'gzip'}).status_code == 404
