"""Stdio MCP bridge: schema parity, real-app calls and line framing."""

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.mcp_bridge import (
    RESOURCE_CATALOG_URI,
    RESOURCE_GUIDE_URI,
    TOOLS,
    Bridge,
    BridgeError,
    handle_message,
    run_selftest,
)

ROOT = Path(__file__).resolve().parents[1]


def make_app(tmp_path):
    database = Database(tmp_path / 'bridge.sqlite3')
    app = create_app(
        database, tmp_path / 'vault',
        security_environ={}, env_file=tmp_path / '.env')
    return app


def serve(tmp_path):
    app = make_app(tmp_path)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, 'uvicorn 未启动'
    return server, thread, f'http://127.0.0.1:{port}'


def test_bridge_schemas_match_the_live_tool_catalog(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.get('/')
    catalog = client.get('/api/tools').json()
    names = [tool['name'] for tool in catalog['tools']]
    assert names == list(TOOLS)
    for tool in catalog['tools']:
        spec = TOOLS[tool['name']]
        assert spec['path'].startswith('/api/tools')
        assert spec['description']
        assert set(spec['required']) <= set(spec['properties'])


def test_initialize_list_and_call_against_the_real_app(tmp_path):
    server, thread, base = serve(tmp_path)
    try:
        bridge = Bridge(base, timeout=5)
        initialized = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-06-18'},
        })
        assert initialized['result']['protocolVersion'] == '2025-06-18'
        assert initialized['result']['capabilities']['tools']['listChanged'] is False
        assert 'resources' in initialized['result']['capabilities']
        assert '只读' in initialized['result']['instructions']
        assert '不要编造' in initialized['result']['instructions']
        assert handle_message(bridge, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None

        listed = handle_message(bridge, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        assert [tool['name'] for tool in listed['result']['tools']] == list(TOOLS)
        assert all(tool['inputSchema']['type'] == 'object' for tool in listed['result']['tools'])

        called = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'today', 'arguments': {}},
        })
        assert called['result']['isError'] is False
        payload = json.loads(called['result']['content'][0]['text'])
        assert 'journal' in payload and 'tasks' in payload

        journal = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
            'params': {'name': 'journal', 'arguments': {'from': '2026-09-30', 'to': '2026-10-04'}},
        })
        assert journal['result']['isError'] is False

        too_small = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
            'params': {'name': 'habits', 'arguments': {'days': 3}},
        })
        assert too_small['result']['isError'] is True and '不能小于' in too_small['result']['content'][0]['text']
        missing = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
            'params': {'name': 'memories', 'arguments': {}},
        })
        assert missing['result']['isError'] is True and '缺少必填参数' in missing['result']['content'][0]['text']
        unknown = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
            'params': {'name': 'write_file', 'arguments': {}},
        })
        assert unknown['result']['isError'] is True and '未知工具' in unknown['result']['content'][0]['text']

        resources = handle_message(bridge, {'jsonrpc': '2.0', 'id': 8, 'method': 'resources/list'})
        uris = [item['uri'] for item in resources['result']['resources']]
        assert uris == [RESOURCE_CATALOG_URI, RESOURCE_GUIDE_URI]
        catalog = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 9, 'method': 'resources/read',
            'params': {'uri': RESOURCE_CATALOG_URI},
        })
        payload = json.loads(catalog['result']['contents'][0]['text'])
        assert [tool['name'] for tool in payload['tools']] == list(TOOLS)
        guide = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 10, 'method': 'resources/read',
            'params': {'uri': RESOURCE_GUIDE_URI},
        })
        assert '只读' in guide['result']['contents'][0]['text']
        missing = handle_message(bridge, {
            'jsonrpc': '2.0', 'id': 11, 'method': 'resources/read',
            'params': {'uri': 'torch://missing'},
        })
        assert missing['error']['code'] == -32002
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_stdio_subprocess_frames_json_lines(tmp_path):
    server, thread, base = serve(tmp_path)
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / 'scripts' / 'mcp_bridge.py'), '--base-url', base],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')

        def send(message):
            process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
            process.stdin.flush()

        def read():
            line = process.stdout.readline()
            assert line, process.stderr.read()
            return json.loads(line)

        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
              'params': {'protocolVersion': '2026-07-28'}})
        assert read()['result']['protocolVersion'] == '2026-07-28'
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        tools = read()['result']['tools']
        assert len(tools) == 12 and tools[-1]['name'] == 'identity'
        assert any(tool['name'] == 'imports' for tool in tools)
        send({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
              'params': {'name': 'imports', 'arguments': {}}})
        call = read()
        assert call['result']['isError'] is False
        assert 'packages' in json.loads(call['result']['content'][0]['text'])
        process.stdin.close()
        assert process.wait(timeout=10) == 0
    finally:
        if process and process.poll() is None:
            process.kill()
        server.should_exit = True
        thread.join(timeout=5)


def test_selftest_spawns_the_bridge_against_a_running_service(tmp_path):
    server, thread, base = serve(tmp_path)
    try:
        result = run_selftest(base, timeout=15)
        assert result['ok'] is True, result
        assert result['protocol_version'] == '2025-06-18'
        assert result['server_name'] == 'torch-os-tools'
        assert result['tool_count'] == 12 and result['tools'] == list(TOOLS)
        assert result['sample_tool'] == 'imports' and result['sample_ok'] is True
        assert result['instructions_ok'] is True
        assert result['resources'] == 2 and result['catalog_ok'] is True
        assert result['elapsed_ms'] >= 0
        assert '不写文件' in result['note']
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_selftest_reports_failure_instead_of_raising():
    result = run_selftest('http://127.0.0.1:9', timeout=10)
    assert result['ok'] is False
    assert result.get('error')
    assert result['sample_ok'] is False
    assert '不调用模型' in result['note']


def test_bridge_selftest_endpoint_validates_loopback_and_runs(tmp_path):
    database = Database(tmp_path / 'bridge.sqlite3')
    app = create_app(
        database, tmp_path / 'vault',
        security_environ={}, env_file=tmp_path / '.env')
    client = TestClient(app)
    client.get('/')
    url = '/api/bridge/selftest'
    # TestClient 的 Host 是 testserver，不在回环名单里 → 422。
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={'base_url': 'http://example.com'}).status_code == 422
    server, thread, base = serve(tmp_path)
    try:
        response = client.post(url, json={'base_url': base})
        assert response.status_code == 200
        payload = response.json()
        assert payload['ok'] is True and payload['tool_count'] == 12
        with database.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM audit_events WHERE event_type='mcp_bridge_selftest'")]
        assert len(rows) == 1 and '"tools": 12' in rows[0]['detail_json']
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"ok"


def test_initial_session_retries_until_the_service_is_up():
    attempts = {"count": 0}

    class FlakyOpener:
        def open(self, request, timeout=None):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise urllib.error.URLError("connection refused")
            return _FakeResponse()

    bridge = Bridge("http://127.0.0.1:9", retry_seconds=5, sleep=lambda _seconds: None)
    bridge.opener = FlakyOpener()
    bridge._ensure_session()
    assert attempts["count"] == 3
    assert bridge._session is True


def test_initial_session_fails_fast_with_a_clear_message():
    bridge = Bridge("http://127.0.0.1:9", retry_seconds=0, sleep=lambda _seconds: None)
    with pytest.raises(BridgeError, match="未就绪"):
        bridge._ensure_session()
    response = handle_message(bridge, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response["error"]["code"] == -32000
    assert "未就绪" in response["error"]["message"]
