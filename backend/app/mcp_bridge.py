"""Stdio MCP bridge exposing the local read-only tools.

The bridge only issues GET requests to the loopback API (fetching the local
session cookie first). It never writes, never leaves this machine, and never
sees model keys. Any MCP client (for example DeepSeek Harness via
``@deepseek-ai/dsh-mcp-client``) can spawn it and call the 12 read-only tools.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = 'http://127.0.0.1:8765'
PROTOCOL_VERSION = '2025-06-18'
SERVER_NAME = 'torch-os-tools'
SERVER_VERSION = '1.0.0'
RESOURCE_CATALOG_URI = 'torch://tools/catalog'
RESOURCE_GUIDE_URI = 'torch://guide/readonly'
INSTRUCTIONS = (
    '薪火OS 把用户自己的本机记录暴露为只读工具（名称形如 mcp__torch__<tool>）。'
    '用法：当用户问到“今天/任务/目标/习惯/学习/日记/资料/导入健康/身体/Vault”，先调用对应工具再回答；'
    '“记忆”工具只返回用户已确认的内容，没有证据时明确说不知道，不要编造个人信息。'
    '所有工具都是只读：不要承诺创建任务、写日记、确认记忆或修改文件；需要写入时请用户回到 薪火OS 界面操作。'
    '回答时说明用了哪些本地工具结果；除非用户要求，不要整段复述原始 JSON。'
)
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')

TOOLS: dict[str, dict[str, Any]] = {
    'today': {
        'path': '/api/tools/today',
        'description': '某一天的结构化概况：日记字段、任务计数与到期标题、习惯状态、学习分钟、身体记录标记、草稿数。',
        'properties': {'day': {'type': 'string', 'description': 'YYYY-MM-DD，默认今天'}},
        'required': [],
    },
    'memories': {
        'path': '/api/tools/memories',
        'description': '按问题检索“已确认且来源有效”的记忆；候选/拒绝/归档不会出现。',
        'properties': {
            'q': {'type': 'string', 'description': '检索问题', 'minLength': 1, 'maxLength': 200},
            'limit': {'type': 'integer', 'description': '返回条数 1–10，默认 6', 'minimum': 1, 'maximum': 10},
        },
        'required': ['q'],
    },
    'goals': {
        'path': '/api/tools/goals',
        'description': '进行中的目标与未完成任务（标题、截止日期、所属目标）。',
        'properties': {'limit': {'type': 'integer', 'description': '返回条数 1–10，默认 5', 'minimum': 1, 'maximum': 10}},
        'required': [],
    },
    'habits': {
        'path': '/api/tools/habits',
        'description': '每个启用习惯最近 N 天的完成/跳过/未记录计数（不含备注）。',
        'properties': {'days': {'type': 'integer', 'description': '7–90，默认 30', 'minimum': 7, 'maximum': 90}},
        'required': [],
    },
    'learning': {
        'path': '/api/tools/learning',
        'description': '最近 N 天的学习分钟与科目分布。',
        'properties': {'days': {'type': 'integer', 'description': '7–90，默认 7', 'minimum': 7, 'maximum': 90}},
        'required': [],
    },
    'journal': {
        'path': '/api/tools/journal',
        'description': '一段日期里哪些天有记录、是否写了下一步（不含日记正文，最多 90 天）。',
        'properties': {
            'from': {'type': 'string', 'description': 'YYYY-MM-DD'},
            'to': {'type': 'string', 'description': 'YYYY-MM-DD'},
        },
        'required': ['from', 'to'],
    },
    'profile': {
        'path': '/api/tools/profile',
        'description': '认识我计数快照：可用/待确认记忆、资料数、进行中目标、未完成任务。',
        'properties': {},
        'required': [],
    },
    'vault': {
        'path': '/api/tools/vault',
        'description': 'Vault 现状：路径、是否 Obsidian 库、Markdown 篇数、跳过的目录、是否只读模式。',
        'properties': {},
        'required': [],
    },
    'body': {
        'path': '/api/tools/body',
        'description': '最近 N 天身体记录汇总：有记录天数、睡眠/运动分钟合计（不含体重数值、不做健康评价）。',
        'properties': {'days': {'type': 'integer', 'description': '7–90，默认 7', 'minimum': 7, 'maximum': 90}},
        'required': [],
    },
    'documents': {
        'path': '/api/tools/documents',
        'description': '已索引资料摘要：总数与最近索引的标题/路径。',
        'properties': {'limit': {'type': 'integer', 'description': '返回条数 1–10，默认 5', 'minimum': 1, 'maximum': 10}},
        'required': [],
    },
    'imports': {
        'path': '/api/tools/imports',
        'description': '资料导入健康计数：导入包、已导入笔记、历史版本、索引待刷新笔记、本地附件（不含文件名与正文）。',
        'properties': {},
        'required': [],
    },
    'vault_audit': {
        'path': '/api/tools/vault_audit',
        'description': 'Vault 只读体检：检查篇数与各类问题计数（不返回具体路径）。',
        'properties': {},
        'required': [],
    },
    'identity': {
        'path': '/api/tools/identity',
        'description': '我是谁：姓名、座右铭、三观与自由补充（来自 Vault 的 01-Identity/identity.md，用户自己写下的内容；空字段就是还没写）。',
        'properties': {},
        'required': [],
    },
}


class BridgeError(RuntimeError):
    """A local tool call or protocol step failed without writing anything."""


class Bridge:
    """GET-only client for the local tools API with loopback session handling."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0,
                 retry_seconds: float = 15.0, sleep: Any = time.sleep):
        self.base_url = str(base_url).rstrip('/')
        self.timeout = float(timeout)
        self.retry_seconds = max(0.0, float(retry_seconds))
        self.sleep = sleep
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._session = False

    def _ensure_session(self, force: bool = False) -> None:
        if self._session and not force:
            return
        deadline = time.monotonic() + (0.0 if force else self.retry_seconds)
        while True:
            try:
                with self.opener.open(urllib.request.Request(self.base_url + '/', method='GET'), timeout=self.timeout) as response:
                    response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if time.monotonic() < deadline:
                    self.sleep(0.5)
                    continue
                waited = '' if force else f'（已等待 {self.retry_seconds:.0f} 秒）'
                raise BridgeError(
                    f'本地服务 {self.base_url} 未就绪{waited}：{error}。请先启动 薪火OS 服务，再重试或重新加载 MCP。'
                ) from error
            self._session = True
            return

    def _get(self, path: str, params: dict[str, Any], retry: bool = True) -> Any:
        query = urllib.parse.urlencode(params)
        url = self.base_url + path + (f'?{query}' if query else '')
        try:
            with self.opener.open(urllib.request.Request(url, method='GET'), timeout=self.timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            if error.code == 401 and retry:
                self._ensure_session(force=True)
                return self._get(path, params, retry=False)
            try:
                detail = json.loads(error.read().decode('utf-8')).get('detail')
            except Exception:
                detail = None
            raise BridgeError(f'本地服务返回 {error.code}：{detail or "请求失败"}') from error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise BridgeError(f'本地服务请求失败：{error}') from error

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_session()
        return [
            {
                'name': name,
                'description': spec['description'],
                'inputSchema': {
                    'type': 'object', 'properties': spec['properties'],
                    'required': spec['required'], 'additionalProperties': False,
                },
            }
            for name, spec in TOOLS.items()
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        spec = TOOLS.get(str(name or ''))
        if spec is None:
            raise BridgeError(f'未知工具：{name}')
        values = dict(arguments or {})
        unknown = sorted(set(values) - set(spec['properties']))
        if unknown:
            raise BridgeError(f'不支持的参数：{", ".join(unknown)}')
        params: dict[str, Any] = {}
        for key, prop in spec['properties'].items():
            if key not in values or values[key] is None or values[key] == '':
                continue
            value = values[key]
            if prop['type'] == 'integer':
                try:
                    number = int(value)
                except (TypeError, ValueError) as error:
                    raise BridgeError(f'参数 {key} 必须是整数') from error
                if 'minimum' in prop and number < prop['minimum']:
                    raise BridgeError(f'参数 {key} 不能小于 {prop["minimum"]}')
                if 'maximum' in prop and number > prop['maximum']:
                    raise BridgeError(f'参数 {key} 不能大于 {prop["maximum"]}')
                params[key] = number
            else:
                text = str(value)
                if 'minLength' in prop and len(text) < prop['minLength']:
                    raise BridgeError(f'参数 {key} 太短')
                if 'maxLength' in prop and len(text) > prop['maxLength']:
                    raise BridgeError(f'参数 {key} 太长')
                params[key] = text
        for key in spec['required']:
            if key not in params:
                raise BridgeError(f'缺少必填参数：{key}')
        self._ensure_session()
        return self._get(spec['path'], params)


def handle_message(bridge: Bridge, message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; notifications return None."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': '不是有效的 JSON-RPC 2.0 消息'}}
    method = message.get('method')
    message_id = message.get('id')
    if method == 'initialize':
        requested = (message.get('params') or {}).get('protocolVersion')
        version = requested if isinstance(requested, str) and _DATE.fullmatch(requested) else PROTOCOL_VERSION
        return {
            'jsonrpc': '2.0', 'id': message_id,
            'result': {
                'protocolVersion': version,
                'capabilities': {'tools': {'listChanged': False},
                                 'resources': {'subscribe': False, 'listChanged': False}},
                'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
                'instructions': INSTRUCTIONS,
            },
        }
    if method in {'notifications/initialized', 'notifications/cancelled'}:
        return None
    if method == 'ping':
        return {'jsonrpc': '2.0', 'id': message_id, 'result': {}}
    if method == 'resources/list':
        return {
            'jsonrpc': '2.0', 'id': message_id,
            'result': {'resources': [
                {'uri': RESOURCE_CATALOG_URI, 'name': 'tools-catalog', 'title': '只读工具目录',
                 'description': '12 个只读工具的名称、参数与说明（本地静态目录，不含用户数据）',
                 'mimeType': 'application/json'},
                {'uri': RESOURCE_GUIDE_URI, 'name': 'readonly-guide', 'title': '只读使用说明',
                 'description': '如何选择工具、只读边界与不要编造个人信息', 'mimeType': 'text/markdown'},
            ]},
        }
    if method == 'resources/read':
        uri = str((message.get('params') or {}).get('uri') or '')
        if uri == RESOURCE_CATALOG_URI:
            text = json.dumps({'count': len(TOOLS), 'tools': [
                {'name': name, 'path': spec['path'], 'description': spec['description'],
                 'parameters': spec['properties'], 'required': spec['required']}
                for name, spec in TOOLS.items()
            ]}, ensure_ascii=False, indent=2)
            mime = 'application/json'
        elif uri == RESOURCE_GUIDE_URI:
            text = INSTRUCTIONS
            mime = 'text/markdown'
        else:
            return {'jsonrpc': '2.0', 'id': message_id, 'error': {'code': -32002, 'message': f'未知资源：{uri}'}}
        return {
            'jsonrpc': '2.0', 'id': message_id,
            'result': {'contents': [{'uri': uri, 'mimeType': mime, 'text': text}]},
        }
    if method == 'tools/list':
        try:
            tools = bridge.list_tools()
        except BridgeError as error:
            return {'jsonrpc': '2.0', 'id': message_id, 'error': {'code': -32000, 'message': str(error)}}
        return {'jsonrpc': '2.0', 'id': message_id, 'result': {'tools': tools}}
    if method == 'tools/call':
        params = message.get('params') or {}
        try:
            payload = bridge.call_tool(params.get('name'), params.get('arguments'))
        except BridgeError as error:
            return {
                'jsonrpc': '2.0', 'id': message_id,
                'result': {'content': [{'type': 'text', 'text': str(error)}], 'isError': True},
            }
        return {
            'jsonrpc': '2.0', 'id': message_id,
            'result': {
                'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False, indent=2)}],
                'isError': False,
            },
        }
    return {'jsonrpc': '2.0', 'id': message_id, 'error': {'code': -32601, 'message': f'不支持的方法：{method}'}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='薪火OS 只读工具 MCP 桥（stdio）')
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL)
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--retry-seconds', type=float, default=15.0)
    args = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            reconfigure(encoding='utf-8')
    bridge = Bridge(args.base_url, args.timeout, retry_seconds=args.retry_seconds)
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': '无法解析 JSON'}}
        else:
            response = handle_message(bridge, message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            sys.stdout.flush()
    return 0


SELFTEST_NOTE = '自检会用与 MCP 客户端相同的方式启动本机桥，只调用只读工具 imports；不写文件、不联网到外部、不调用模型。'


def run_selftest(base_url: str = DEFAULT_BASE_URL, timeout: float = 20.0) -> dict[str, Any]:
    """Spawn the bridge exactly as an MCP client would and verify one read-only call."""
    started = time.monotonic()

    def failure(message: str) -> dict[str, Any]:
        return {
            'ok': False, 'error': message, 'elapsed_ms': int((time.monotonic() - started) * 1000),
            'sample_ok': False, 'instructions_ok': False, 'resources': 0, 'catalog_ok': False,
            'note': SELFTEST_NOTE,
        }

    script = Path(__file__).resolve().parents[2] / 'scripts' / 'mcp_bridge.py'
    if not script.is_file():
        return failure(f'找不到桥脚本：{script}')
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'torch-os-selftest', 'version': SERVER_VERSION}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'imports', 'arguments': {}}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/list'},
        {'jsonrpc': '2.0', 'id': 5, 'method': 'resources/read', 'params': {'uri': RESOURCE_CATALOG_URI}},
    ]
    payload = ''.join(json.dumps(message, ensure_ascii=False) + '\n' for message in messages)
    try:
        completed = subprocess.run(
            [sys.executable, str(script), '--base-url', str(base_url), '--retry-seconds', '4'],
            input=payload, capture_output=True, text=True, encoding='utf-8',
            timeout=timeout, cwd=str(script.parents[1]))
    except subprocess.TimeoutExpired:
        return failure(f'桥在 {timeout:.0f} 秒内没有完成')
    except OSError as error:
        return failure(f'无法启动桥：{error}')
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        return failure(f'桥退出码 {completed.returncode}：{(completed.stderr or "").strip()[:200]}')
    responses: dict[int, dict[str, Any]] = {}
    protocol_errors: list[str] = []
    for line in (completed.stdout or '').splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message.get('id'), int):
            responses[message['id']] = message
        if isinstance(message.get('error'), dict) and message['error'].get('message'):
            protocol_errors.append(str(message['error']['message']))
    initialized = responses.get(1, {}).get('result') or {}
    listed = responses.get(2, {}).get('result') or {}
    called = responses.get(3, {}).get('result') or {}
    resource_list = responses.get(4, {}).get('result') or {}
    resource_read = responses.get(5, {}).get('result') or {}
    names = [tool.get('name') for tool in listed.get('tools', []) if isinstance(tool, dict)]
    instructions_ok = '只读' in str(initialized.get('instructions') or '')
    resource_count = len(resource_list.get('resources', []))
    catalog_ok = False
    if resource_read.get('contents'):
        try:
            catalog = json.loads(resource_read['contents'][0].get('text') or '{}')
            catalog_ok = [tool.get('name') for tool in catalog.get('tools', [])] == list(TOOLS)
        except (json.JSONDecodeError, AttributeError, IndexError):
            catalog_ok = False
    sample_ok = called.get('isError') is False
    sample_payload: dict[str, Any] = {}
    sample_error = None
    if sample_ok and called.get('content'):
        try:
            sample_payload = json.loads(called['content'][0].get('text') or '{}')
        except (json.JSONDecodeError, AttributeError, IndexError):
            sample_ok = False
    elif called.get('content'):
        sample_error = str(called['content'][0].get('text') or '')[:200]
    ok = (bool(initialized.get('protocolVersion')) and names == list(TOOLS)
          and sample_ok and 'packages' in sample_payload
          and instructions_ok and resource_count == 2 and catalog_ok)
    result: dict[str, Any] = {
        'ok': bool(ok),
        'protocol_version': initialized.get('protocolVersion'),
        'server_name': (initialized.get('serverInfo') or {}).get('name'),
        'tool_count': len(names), 'tools': names, 'sample_tool': 'imports',
        'sample_ok': sample_ok, 'elapsed_ms': elapsed_ms,
        'instructions_ok': instructions_ok, 'resources': resource_count, 'catalog_ok': catalog_ok,
        'note': SELFTEST_NOTE,
    }
    if not ok:
        result['error'] = sample_error or (protocol_errors[0] if protocol_errors
                                           else '桥的握手或工具调用与预期不一致，请检查本地服务与脚本路径。')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
