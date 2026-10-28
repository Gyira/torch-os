"""QA startup is an independent, offline application with retained local data."""
import asyncio
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import qa_runtime


@pytest.fixture
def project(tmp_path):
    examples = tmp_path / 'examples' / 'vault' / 'fiction'
    examples.mkdir(parents=True)
    (examples / 'profile.md').write_text(
        '---\nid: fictional-qa\ntype: fact\ntitle: 虚构档案\n---\n\n'
        '这是虚构示例。我的职业是森林观察员。\n', encoding='utf-8')
    (examples / 'ignored.txt').write_text('not a Markdown fixture', encoding='utf-8')
    actual = tmp_path / 'data' / 'vault'
    actual.mkdir(parents=True)
    (actual / 'private.md').write_text('PRIVATE_SENTINEL', encoding='utf-8')
    (tmp_path / '.env').write_text('DATA_DIR=data\nMODEL_API_KEY=PRIVATE_SENTINEL\n', encoding='utf-8')
    return tmp_path


def client_for(app):
    client = TestClient(app, base_url='http://127.0.0.1:8766')
    assert client.get('/').status_code == 200
    return client


def test_new_workspaces_copy_only_fictional_markdown_and_keep_candidates(project):
    first, one = qa_runtime.create_qa_app(project_root=project)
    second, two = qa_runtime.create_qa_app(project_root=project)
    assert one != two
    assert one.parent == two.parent == project / '.test-data'
    assert first.state.database.path.parent == one
    assert first.state.vault_dir == one / 'vault'
    assert [p.relative_to(one / 'vault').as_posix() for p in (one / 'vault').rglob('*') if p.is_file()] == ['fiction/profile.md']
    memories = first.state.database.list_memories()
    assert memories and all(row['status'] == 'candidate' for row in memories)
    assert len(second.state.database.list_documents()) == 1
    assert (one / '.qa-workspace.json').is_file()
    assert 'PRIVATE_SENTINEL' not in json.dumps(first.state.database.list_documents())


def test_private_data_and_dotenv_are_not_opened_even_with_hostile_environment(project, monkeypatch):
    for key, value in {
        'MODEL_PROVIDER': 'deepseek', 'MODEL_BASE_URL': 'https://example.invalid',
        'MODEL_NAME': 'unwanted-model', 'MODEL_API_KEY': 'private-test-key',
        'MODEL_TIMEOUT_SECONDS': 'bad', 'DATA_DIR': str(project / 'data'),
        'VAULT_DIR': str(project / 'data' / 'vault'), 'HOST': '0.0.0.0',
        'ALLOW_REMOTE_API': 'true', 'API_TOKEN': 'private-test-token',
    }.items():
        monkeypatch.setenv(key, value)
    original_open = builtins.open
    original_io_open = io.open

    def check_path(file):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file)).absolute()
            assert path != project / '.env', '.env was opened'
            assert not path.is_relative_to(project / 'data'), 'Private data was opened'

    def guarded_open(file, *args, **kwargs):
        check_path(file)
        return original_open(file, *args, **kwargs)

    def guarded_io_open(file, *args, **kwargs):
        check_path(file)
        return original_io_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(io, 'open', guarded_io_open)
    app, directory = qa_runtime.create_qa_app(project_root=project)
    assert directory.parent == project / '.test-data'
    assert app.state.dsh_runtime.status()['state'] == 'disabled'
    status = client_for(app).get('/api/config/status').json()
    assert status['runtime_label'] == 'qa'
    assert status['capabilities']['dsh_web'] is False


def test_fresh_interpreter_never_imports_config(project):
    script = '''
import importlib.abc
import pathlib
import sys
class RejectConfig(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'backend.app.config', 'backend.app.main'}:
            raise AssertionError('Private config imported')
        return None
sys.meta_path.insert(0, RejectConfig())
from backend.app.qa_runtime import create_qa_app
app, workspace = create_qa_app(project_root=pathlib.Path(sys.argv[1]))
assert app.state.dsh_runtime.status()['state'] == 'disabled'
assert 'backend.app.config' not in sys.modules
print('isolated')
'''
    result = subprocess.run([sys.executable, '-c', script, str(project)],
                            cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'isolated'


def test_qa_has_no_chat_or_model_surface(project):
    app, _ = qa_runtime.create_qa_app(project_root=project)
    client = client_for(app)
    memory = app.state.database.list_memories()[0]
    assert client.patch(f"/api/memories/{memory['id']}/status", json={'status': 'active'}).status_code == 200
    assert client.post('/api/chat', json={'message': '虚构问题'}).status_code == 404
    assert client.post('/api/config/model-key', json={'api_key': 'test', 'confirm': True}).status_code == 404
    # 恋爱系统属于个人真实数据：QA 必须连路由都不存在。
    assert client.get('/api/love/status').status_code == 404
    assert client.get('/api/love/schedule/status').status_code == 404
    status = client.get('/api/config/status').json()
    assert status['capabilities']['love'] is False
    assert status['capabilities']['love_schedule'] is False
    assert app.state.dsh_runtime.status()['state'] == 'disabled'


def test_reuse_preserves_local_edits_and_review_state(project):
    app, directory = qa_runtime.create_qa_app(project_root=project)
    client = client_for(app)
    added = client.post('/api/notes', json={'title': 'QA 保存', 'content': '虚构验收记忆'}).json()
    memory_id = added['memories'][0]['id']
    client.patch(f'/api/memories/{memory_id}/status', json={'status': 'active'})
    fixture = directory / 'vault' / 'fiction' / 'profile.md'
    fixture.write_text('QA 已修改的虚构示例', encoding='utf-8')
    resumed, reused = qa_runtime.create_qa_app(project_root=project, workspace=directory.name)
    assert reused == directory
    assert fixture.read_text(encoding='utf-8') == 'QA 已修改的虚构示例'
    assert any(row['id'] == memory_id for row in resumed.state.database.list_memories('active'))
    assert (project / 'data' / 'vault' / 'private.md').read_text(encoding='utf-8') == 'PRIVATE_SENTINEL'


@pytest.mark.parametrize('name', ['../data', 'qa-../data', 'qa-x/../data', 'qa-x\\vault',
                                 'qa-', 'qa-a\n', 'qa-A', 'C:\\data', '/data', 'qa-' + 'x' * 65])
def test_workspace_name_cannot_select_arbitrary_paths(project, name):
    with pytest.raises(qa_runtime.QAWorkspaceError):
        qa_runtime.create_qa_app(project_root=project, workspace=name)
    assert not (project / '.test-data').exists()


def test_reuse_requires_existing_qa_marker(project):
    with pytest.raises(qa_runtime.QAWorkspaceError):
        qa_runtime.create_qa_app(project_root=project, workspace='qa-missing')
    unmarked = project / '.test-data' / 'qa-manual'
    unmarked.mkdir(parents=True)
    sentinel = unmarked / 'untouched.txt'
    sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(qa_runtime.QAWorkspaceError):
        qa_runtime.create_qa_app(project_root=project, workspace=unmarked.name)
    assert sentinel.read_text(encoding='utf-8') == 'keep'


def test_existing_hardlink_cannot_redirect_database_or_vault(project):
    _, directory = qa_runtime.create_qa_app(project_root=project)
    source = project / 'data' / 'vault' / 'private.md'
    os.link(source, directory / 'vault' / 'alias.md')
    with pytest.raises(qa_runtime.QAWorkspaceError, match='hard links'):
        qa_runtime.create_qa_app(project_root=project, workspace=directory.name)
    assert source.read_text(encoding='utf-8') == 'PRIVATE_SENTINEL'


@pytest.mark.parametrize('linked_part', ['parent', 'workspace', 'nested', 'examples'])
def test_windows_junctions_and_symlinks_are_rejected(project, linked_part):
    private = project / 'data'
    if linked_part == 'parent':
        link = project / '.test-data'
        workspace = None
    elif linked_part == 'examples':
        link = project / 'examples' / 'vault' / 'private-alias'
        workspace = None
    else:
        _, existing = qa_runtime.create_qa_app(project_root=project)
        workspace = 'qa-linked' if linked_part == 'workspace' else existing.name
        link = existing.parent / workspace if linked_part == 'workspace' else existing / 'vault' / 'private-alias'
    if os.name == 'nt':
        # Junction creation does not need Windows developer-mode symlink privileges.
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(private)],
                                capture_output=True, text=True)
        assert result.returncode == 0, 'Could not create the temporary junction fixture'
    else:
        link.symlink_to(private, target_is_directory=True)
    with pytest.raises(qa_runtime.QAWorkspaceError, match='reparse points'):
        qa_runtime.create_qa_app(project_root=project, workspace=workspace)
    # Remove only the link itself; never recurse through it into the target.
    if os.name == 'nt':
        link.rmdir()
    else:
        link.unlink()
    assert (private / 'vault' / 'private.md').read_text(encoding='utf-8') == 'PRIVATE_SENTINEL'


def test_check_reports_only_safe_metadata_and_does_not_start_server(project, monkeypatch, capsys):
    original = qa_runtime.create_qa_app
    monkeypatch.setattr(qa_runtime, 'create_qa_app', lambda **kwargs: original(project_root=project, **kwargs))
    import uvicorn
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: pytest.fail('--check must not start HTTP'))
    assert qa_runtime.main(['--check']) == 0
    output = capsys.readouterr().out
    assert 'PRIVATE_SENTINEL' not in output and str(project) not in output
    report = json.loads(output)
    assert report['status'] == 'validated' and report['harness_enabled'] is False
    assert report['url'] == 'http://127.0.0.1:8766'
    assert report['updated_at'].endswith('+08:00')
    directory = project / '.test-data' / report['workspace']
    assert json.loads((directory / 'qa-report.json').read_text(encoding='utf-8')) == report


def test_cli_passes_built_app_to_loopback_server_and_retains_workspace(project, monkeypatch, capsys):
    original = qa_runtime.create_qa_app
    monkeypatch.setattr(qa_runtime, 'create_qa_app', lambda **kwargs: original(project_root=project, **kwargs))
    import uvicorn
    observed = {}

    def run(app, **kwargs):
        observed.update(kwargs)
        observed['workspace'] = app.state.qa_workspace
        assert not isinstance(app, str)
        assert app.state.dsh_runtime.status()['state'] == 'disabled'

    monkeypatch.setattr(uvicorn, 'run', run)
    assert qa_runtime.main(['--port', '8767']) == 0
    assert observed['host'] == '127.0.0.1' and observed['port'] == 8767
    assert observed['access_log'] is False
    directory = project / '.test-data' / observed['workspace']
    assert (directory / 'torch-os.sqlite3').is_file()
    assert json.loads((directory / 'qa-report.json').read_text(encoding='utf-8'))['status'] == 'exited'
    assert json.loads(capsys.readouterr().out)['status'] == 'startup_requested'


@pytest.mark.parametrize('args', [['--host', '0.0.0.0'], ['--data-dir', 'data'],
                                 ['--vault-dir', 'data/vault'], ['--port', '0'], ['--port', '65536']])
def test_cli_rejects_external_path_and_listener_settings(args):
    with pytest.raises(SystemExit) as result:
        qa_runtime.main(args)
    assert result.value.code == 2
