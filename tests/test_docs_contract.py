"""Documentation cannot drift: the handbook, capability list and version labels are checked."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import APP_VERSION, create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway

ROOT = Path(__file__).resolve().parents[1]


def client(tmp_path):
    database = Database(tmp_path / 'torch_os.sqlite3')
    gateway = ModelGateway(provider='local', base_url='', model='', api_key='', timeout=1)
    app = create_app(database, tmp_path / 'vault', gateway, security_environ={}, env_file=tmp_path / '.env')
    test_client = TestClient(app)
    test_client.get('/')
    return test_client


def test_feature_map_lists_exactly_the_live_capabilities(tmp_path):
    text = (ROOT / 'docs' / 'FEATURE_MAP.md').read_text(encoding='utf-8')
    line = next(line for line in text.splitlines() if line.startswith('`GET /api/config/status` 返回当前版本与能力标记'))
    documented = set(re.findall(r'`([a-z_]+)`', line.split('：', 1)[1]))
    # 版本与两个说明性词条不算能力名。
    documented.discard('GET')
    live = set(client(tmp_path).get('/api/config/status').json()['capabilities'])
    assert documented == live, f"FEATURE_MAP 缺少：{sorted(live - documented)}；多出：{sorted(documented - live)}"


def test_release_documents_name_the_running_version():
    series = '.'.join(APP_VERSION.split('.')[:2])
    readme = (ROOT / 'README.md').read_text(encoding='utf-8').splitlines()[0]
    assert readme.strip() == f'# 薪火OS（Torch OS）· {series}', readme
    wake = (ROOT / 'docs' / 'WAKE_UP_START.md').read_text(encoding='utf-8')
    assert f'这份说明对应运行中的 {series} 版' in wake


def test_restore_drill_document_keeps_its_evidence_shape():
    text = (ROOT / 'docs' / 'RESTORE_DRILL.md').read_text(encoding='utf-8')
    assert '## 本次演练的实际结果' in text
    assert 'POST /api/backups' in text
    assert 'GET /api/backups/restored/{name}/verify' in text
    assert 'verified: true' in text and 'integrity: ok' in text
    assert '不会覆盖当前库' in text or '不覆盖当前库' in text
    assert '把副本切换为当前库' in text  # 边界仍然写清楚
