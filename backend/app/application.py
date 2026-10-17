"""Application factory; tests inject temporary data and never call live models."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
from urllib.parse import urlsplit
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .db import Database
from .envfile import write_env_value
from .audit_summary import create_audit_summary_router
from .backup import (
    RESTORED_NAME_PATTERN, BackupError, create_backup, list_restored_copies,
    plan_retention, render_restore_drill_report, restore_backup, verify_restored_copy,
)
from .body import create_body_router
from .bazi import create_bazi_router, dependency_available as bazi_dependency_available
from .conversation_export import create_conversation_export_router
from .conversation_memory import create_conversation_memory_router
from .share_import import create_share_import_router
from .schema_export import create_schema_router
from .tools import create_tools_router
from .mcp_bridge import run_selftest as mcp_bridge_selftest
from . import harness_brain
from .suggestions import SuggestionService, create_suggestions_router
from .tools import ToolService
from . import agent as agent_module
from .workspaces import create_workspaces_router
from .vault_connection import (
    READ_ONLY_ENV,
    VaultReadOnlyError,
    describe_vault,
    ensure_vault_writable,
    normalize_vault_path,
    onboarding_report,
    preview_scan,
    read_only_enabled,
    render_onboarding_markdown,
)
from .diagnostics import create_diagnostics_router
from .export_bundle import create_export_bundle_router
from .goals import GoalTaskService, create_goals_router
from .growth_export import create_growth_export_router
from .identity import create_identity_router
from .journal import create_journal_router
from .activity import create_activity_router
from .banner_stats import create_banner_stats_router
from .journal_note import create_journal_note_router
from .todo_note import create_todo_router
from .tasks import create_tasks_router
from .weather import create_weather_router
from .journal_export import create_journal_export_router
from .habits import create_habits_router
from .learning import create_learning_router
from .task_import import create_task_import_router
from .link_audit import audit_wolai_links
from .attachment_plan import plan_wolai_attachment_localization
from .attachment_localize import localize_wolai_attachments
from .import_history import (
    list_wolai_import_history,
    preview_wolai_history_cleanup,
    restore_wolai_import_history,
)
from .memory_export import create_memory_export_router
from .model_gateway import ModelGateway, ModelGatewayError
from .planning_export import create_planning_export_router
from .profile import create_profile_router
from .scheduler import SchedulerService, create_scheduler_router
from .security import is_loopback_host, token_matches, validate_runtime_config
from .vault import EXTRACTION_VERSION, _memory_from_document, create_note, scan_vault, safe_note_path
from .vault_audit import create_vault_audit_router
from .wolai_import import WolaiImportError, preview_wolai_archive, import_selected_notes
from .wolai_import import preview_import_states
from .workbench import create_workbench_router

APP_VERSION = '0.99.0'

# Page manifest consumed by the local dsh sidebar plugin (0.88.0). The labels
# mirror frontend/nav-order.js; tests keep the two in sync.
# 对话页已移除（用户 2026-10-05：对话统一在 dsh 里），所以这里不再有 chat。
NAV_VIEWS = [
    ('workbench', '工作台'), ('identity', '数字分身'),
    ('brain', '第二大脑'), ('settings', '设置与状态'),
]


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ChatRequest(InputModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = Field(default=None, gt=0)
    use_cloud: bool = False
    use_harness: bool = False
    agent: bool = True


class HarnessResetRequest(InputModel):
    conversation_id: int | None = Field(default=None, gt=0)


class MemoryStatusRequest(InputModel):
    status: Literal['candidate', 'active', 'rejected', 'archived']


class MemoryBatchStatusRequest(InputModel):
    ids: list[int] = Field(min_length=1, max_length=50)
    status: Literal['active', 'rejected']
    confirm: bool = Field(strict=True)

    @field_validator('ids')
    @classmethod
    def unique_positive_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError('记忆 ID 不能重复')
        if any(value < 1 for value in values):
            raise ValueError('记忆 ID 必须为正整数')
        return values

    @model_validator(mode='after')
    def require_confirmation(self):
        if self.confirm is not True:
            raise ValueError('请明确确认批量操作')
        return self


class MemoryTidyRequest(InputModel):
    dry_run: bool = Field(default=False, strict=True)
    confirm: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def require_confirmation_for_apply(self):
        if not self.dry_run and self.confirm is not True:
            raise ValueError('请明确确认整理候选')
        return self


class MemoryEditRequest(InputModel):
    content: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, min_length=1, max_length=200)


class MemoryDeleteRequest(InputModel):
    confirm: StrictBool


class NoteRequest(InputModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50_000)
    type: Literal['fact', 'experience', 'belief', 'goal', 'preference', 'note', 'project'] = 'note'


class ImportSelection(InputModel):
    selected_paths: list[str] = Field(min_length=1, max_length=20)


class HistoryRestore(InputModel):
    source_path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class BridgeSelftest(InputModel):
    base_url: str | None = Field(default=None, max_length=200)


class ReextractRequest(InputModel):
    revision: StrictInt = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-fA-F0-9]{64}$')
    confirm: bool = Field(strict=True)


class VaultConnectionUpdate(InputModel):
    path: str = Field(min_length=1, max_length=500)
    read_only: bool = Field(default=True, strict=True)


class RestoreDrillRequest(InputModel):
    confirm: bool = Field(strict=True)


class ModelKeyUpdate(InputModel):
    api_key: str = Field(min_length=8, max_length=200, pattern=r'^[A-Za-z0-9._\-]+$')
    confirm: bool = Field(strict=True)

    @model_validator(mode='after')
    def require_confirmation(self):
        if self.confirm is not True:
            raise ValueError('请明确确认保存密钥')
        return self


def local_answer(evidence: list[dict], cloud_available: bool = False) -> str:
    if not evidence:
        message = '当前没有找到相关且已确认的记忆，我还不知道这件事。请先在记录页保存资料，再到记忆页核对并确认。'
        if cloud_available:
            message += ' 也可以勾选“本次使用云端模型”后重新发送，让云端模型基于问题本身作答（不会假设你的个人信息）。'
        return message
    weak_only = all(item.get('low_confidence') for item in evidence)
    if weak_only:
        lines = ['当前没有找到与问题直接相关的已确认记忆。下面只有语义上较弱的匹配（低置信，仅供参考），不能当作对这个问题的回答；如果资料里应该有答案，可以先补充记录，再到记忆页核对。']
    else:
        lines = ['以下是已确认记忆的直接摘录（本地检索，未调用云端模型）：']
    for index, item in enumerate(evidence, start=1):
        marker = '用户修订后的记忆' if item['confidence'] == 'user_corrected' else '已确认记录'
        suffix = ' · 低置信（仅语义相关）' if item.get('low_confidence') else ''
        lines.append(f"\n[资料{index}] {item['title']} · {marker}{suffix}\n{item['content']}")
    return '\n'.join(lines)


def vault_identity(vault_dir: Path | str) -> str:
    """Stable, non-secret identifier for a Vault path (shared with the Obsidian plugin).

    Windows paths are case-insensitive, so both this helper and the plugin
    lower-case them before hashing.  The health endpoint only exposes this
    digest, never the absolute path.
    """
    resolved = Path(vault_dir).resolve().as_posix()
    if os.name == 'nt':
        resolved = resolved.lower()
    return hashlib.sha256(resolved.encode('utf-8')).hexdigest()[:16]


def create_app(database: Database | None = None, vault_dir: Path | None = None,
               gateway: ModelGateway | None = None, *, session_token: str | None = None,
               host: str = '127.0.0.1', security_environ: dict | None = None,
               runtime_label: str = 'personal', embedding_provider: Any = None,
               env_file: Path | None = None, workspace_registry: Path | None = None,
               vault_scoped: bool | None = None) -> FastAPI:
    if database is None or vault_dir is None:
        from .config import DB_PATH, VAULT_DIR, HOST, VAULT_SCOPED
        database = database or Database(DB_PATH)
        vault_dir = vault_dir or VAULT_DIR
        host = HOST
        if vault_scoped is None:
            vault_scoped = VAULT_SCOPED
    if vault_scoped is None:
        vault_scoped = False
    security = validate_runtime_config(host, database.path.parent, vault_dir, environ=security_environ)
    if embedding_provider is not None:
        database.embedding_provider = embedding_provider
    if runtime_label not in {'personal', 'qa'}:
        raise ValueError('Unsupported runtime label')
    gateway = gateway or ModelGateway()
    if runtime_label == 'qa' and (gateway.configured or not is_loopback_host(host)):
        raise ValueError('QA runtime must be local-only with cloud models disabled')
    env_path = Path(env_file) if env_file is not None else Path(__file__).resolve().parents[2] / '.env'
    vault_read_only = read_only_enabled(security_environ)
    database.initialize()
    vault_dir.mkdir(parents=True, exist_ok=True)
    scheduler_service = SchedulerService(database)
    scheduler_state: dict[str, Any] = {"last_check_at": None, "consecutive_failures": 0, "last_error_at": None}

    async def scheduler_loop() -> None:
        # Local-only draft generator: checks every minute, writes at most one
        # inbox row per kind and day, and never touches tasks or memories.
        while True:
            try:
                await run_in_threadpool(scheduler_service.run_due)
                scheduler_state["consecutive_failures"] = 0
            except Exception:
                scheduler_state["consecutive_failures"] += 1
                scheduler_state["last_error_at"] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
                if scheduler_state["consecutive_failures"] == 1:
                    database.audit('scheduler_loop_error', {'detail': 'run_due failed'})
            scheduler_state["last_check_at"] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(scheduler_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(Exception):
                harness_service.close()

    local_token = session_token or secrets.token_urlsafe(32)
    frontend = Path(__file__).resolve().parents[2] / 'frontend'
    app = FastAPI(title='薪火OS', version=APP_VERSION, lifespan=lifespan)
    app.state.database = database
    app.state.gateway = gateway
    app.state.vault_dir = vault_dir
    app.state.runtime_label = runtime_label
    app.state.vault_scoped = bool(vault_scoped)
    app.state.data_lock = asyncio.Lock()
    app.state.chat_lock = asyncio.Lock()
    app.mount('/assets', StaticFiles(directory=frontend), name='assets')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','[::1]','testserver',host])

    def session_cookie_name(request: Request) -> str:
        # Browser cookies share a hostname across ports: separate QA and personal
        # workspaces (and ports) so visiting one does not overwrite the other.
        identity = f'{database.path.resolve()}|{request.url.port or 80}'
        return 'torch_session_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        if request.url.path.startswith('/api/'):
            remote = request.client and request.client.host not in {'127.0.0.1','::1','testclient'}
            if remote and not security.remote_api_enabled:
                return JSONResponse({'detail':'此服务仅接受本机访问'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'拒绝跨来源 API 请求'}, status_code=403)
            # 跨站请求默认拒绝；唯一例外是“内嵌的同源应用”——Obsidian（app://obsidian.md）
            # 用 iframe 承载工作台时，应用自己的 fetch 带 Sec-Fetch-Mode: same-origin +
            # Sec-Fetch-Site: cross-site；真实的跨站网页伪造不出这个组合（只能发
            # cors/no-cors 请求），Origin 检查与会话 Cookie 仍然照常生效。
            site = request.headers.get('sec-fetch-site') or ''
            mode = request.headers.get('sec-fetch-mode') or ''
            if site == 'cross-site' and mode != 'same-origin':
                return JSONResponse({'detail':'拒绝跨站点 API 请求'}, status_code=403)
            if request.url.path != '/api/health':
                provided = request.headers.get('x-api-token') or request.headers.get('authorization','').removeprefix('Bearer ')
                valid = token_matches(provided, security.api_token) if security.remote_api_enabled else (
                    token_matches(request.cookies.get(session_cookie_name(request)), local_token) or token_matches(provided, local_token))
                if not valid:
                    return JSONResponse({'detail':'会话已失效，请刷新工作台页面后重试'}, status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        # The workbench may only be framed from this machine (user rule
        # 2026-10-05: the product lives inside dsh, whose port can vary) or
        # from the local Obsidian app (user rule 2026-10-05 late: the whole
        # product also lives inside Obsidian as an embedded view; Obsidian
        # frames pages with the `app://obsidian.md` origin, hence the `app:`
        # scheme source below).
        # X-Frame-Options cannot express an allow-list, so framing is governed
        # by CSP frame-ancestors: loopback hosts on any port, nothing else.
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'self' http://127.0.0.1:* http://localhost:* app:; "
            "base-uri 'self'; form-action 'self'")
        return response

    @app.get('/', include_in_schema=False)
    def index(request: Request):
        response = FileResponse(frontend / 'index.html')
        # 嵌在 Obsidian（app://obsidian.md 顶层）里的 iframe 属于跨站上下文：SameSite=Strict
        # 的 Cookie 不会随它的请求发出，所以对这类请求改发 SameSite=None; Secure——
        # 127.0.0.1 属于可信来源，浏览器允许 http 回环上的 Secure Cookie；普通访问、
        # dsh 与脚本工具链（不发 Sec-Fetch-* 头）仍然拿到 Strict。
        embedded = (request.headers.get('sec-fetch-site') or '') == 'cross-site'
        response.set_cookie(
            session_cookie_name(request), local_token, httponly=True,
            samesite='none' if embedded else 'strict', secure=embedded, path='/')
        return response

    @app.get('/api/health')
    def health():
        return {'status':'ok','service':'torch-os','version':APP_VERSION,'runtime_label':runtime_label,
                'vault_id':vault_identity(vault_dir), 'vault_name':Path(vault_dir).name,
                'vault_scoped':bool(vault_scoped)}

    @app.get('/api/nav')
    def nav_manifest():
        """Page list for the local dsh sidebar panel: id, label and hash."""
        return {'views': [{'id': view, 'label': label, 'hash': f'#{view}'}
                          for view, label in NAV_VIEWS]}

    @app.get('/api/config/status')
    def config_status():
        state = gateway.public_status()
        return {'model_configured':gateway.configured, 'model_name':state.get('model'),
                'provider':state.get('provider'), 'cloud_enabled':gateway.configured,
                'state':'configured' if gateway.configured else 'local', 'default_mode':'local',
                'vault_dir':str(vault_dir), 'timezone':'Asia/Shanghai', 'version':APP_VERSION,
                'runtime_label':runtime_label, 'vault_name':Path(vault_dir).name,
                'vault_scoped':bool(vault_scoped),
                'embedding_provider':getattr(database.embedding_provider, 'name', None),
                'capabilities':{'link_audit':True,'habits':True,'learning':True,
                    'identity':True,'workbench':True,'workbench_polish':True,
                                'profile':True,'chat_preview':True,'scheduler':True,
                                'body_records':True,'memory_usage':True,'scheduler_weekly':True,
                                'memory_export':True,'body_export':True,'diagnostics':True,
                                'body_summary':True,
                                'planning_export':True,
                                'task_reorder':True,
                                'attachment_plan':True,
                                'attachment_localize':True,
                                'import_history':True,
                                'history_restore':True,
                                'history_cleanup_preview':True,
                                'growth_export':True,
                                'conversation_export':True,
                                'vault_audit':True,
                                'audit_summary':True,
                                'journal_export':True,
                                'export_bundle':True,
                                'backup_plan':True,
                                'memory_delete':True,
                                'memory_batch':True,
                                'candidate_tidy':True,
                                'model_key_write':runtime_label == 'personal',
                                'chat_memory_panel':True,
                                'restored_copies':True,
                                'monthly_review':True,
                                'learning_monthly':True,
                                'bazi':bazi_dependency_available(),
                                'conversation_extract':True,
                                'share_import':True,
                                'vault_connection':not vault_scoped,
                                'workspaces':runtime_label == 'personal' and not vault_scoped,
                                'schema_export':True,
                                'tools':True,
                                'mcp_bridge':True,
                                'harness':bool(harness_brain.resolve_command()),
                                'harness_reset':True,
                                'agent':True,
                                'suggestions':True,
                                'weather':True,
                                'tasks':True,
                                'restore_drill':True,
                'semantic_search':database.embedding_provider is not None}}

    @app.post('/api/config/model-key')
    async def save_model_key(payload: ModelKeyUpdate):
        """Write the key to the local .env only; it is never stored, echoed or exported."""
        if runtime_label != 'personal':
            raise HTTPException(409, '隔离验收环境不允许写入密钥')
        try:
            await run_in_threadpool(write_env_value, env_path, 'MODEL_API_KEY', payload.api_key)
        except OSError as error:
            raise HTTPException(409, '无法写入本地 .env；请检查文件权限后重试') from error
        try:
            gateway.set_api_key(payload.api_key)
        except ModelGatewayError as error:
            raise HTTPException(409, '密钥格式无效，未启用') from error
        database.audit('model_key_updated', {
            'provider': gateway.provider, 'model': gateway.model or None, 'configured': gateway.configured,
        })
        return {'saved': True, 'configured': gateway.configured, 'model_name': gateway.model or None,
                'note': '密钥已写入本地 .env 并立即生效；界面不会显示或回传密钥，数据库、备份与导出都不包含它。'}

    @app.get('/api/documents')
    def documents():
        return database.list_documents()

    @app.get('/api/documents/{document_id}')
    def document(document_id: int):
        row = database.get_document(document_id)
        if not row:
            raise HTTPException(404, '来源不存在或已失效')
        return row

    @app.post('/api/documents/{document_id}/reextract')
    async def reextract(document_id: int, payload: ReextractRequest):
        if payload.confirm is not True:
            raise HTTPException(422, '请明确确认重新提取后的候选需要再次审核')
        async with app.state.data_lock:
            await run_in_threadpool(scan_vault, vault_dir, database)
            document = database.get_document(document_id)
            if document is None:
                raise HTTPException(404, '来源不存在或已失效')
            if document['revision'] != payload.revision or document['sha256'].casefold() != payload.sha256.casefold():
                raise HTTPException(409, '原文已经变化，请重新打开原文并核对后再提取')
            coverage = document['coverage_summary']
            extraction_version = coverage.get('extraction_version')
            if extraction_version in {EXTRACTION_VERSION, 'journal-fields-v1'}:
                return {'document':document,'candidate_count':coverage['candidate_count'],'changed':False,
                        'warnings':['资料已使用对应的当前提取规则，本次保留已有审核决定，不重复创建候选。']}
            if extraction_version not in {None, 'legacy'}:
                raise HTTPException(409, '资料采用未识别的提取版本，本次保留现有记忆，不降级提取规则')
            candidates = _memory_from_document(json.loads(document['metadata_json']), document['content'], Path(document['path']))
            database.replace_document_memories(document_id, candidates)
            database.audit('source_reextracted', {'document_id':document_id,'extraction_version':EXTRACTION_VERSION,'candidate_count':len(candidates)})
            return {'document':database.get_document(document_id),'candidate_count':len(candidates),'changed':True,
                    'warnings':['旧候选及已确认记忆已归档并保留历史；新候选需要重新审核。', *candidates.coverage_summary['warnings']]}

    @app.post('/api/vault/scan')
    async def scan():
        async with app.state.data_lock:
            return await run_in_threadpool(scan_vault, vault_dir, database)

    @app.post('/api/bridge/selftest')
    async def bridge_selftest(request: Request, payload: BridgeSelftest | None = None):
        """Spawn the MCP bridge like a client would and verify one read-only call."""
        candidate = (payload.base_url if payload else None) or f"http://{request.headers.get('host') or ''}"
        parsed = urlsplit(candidate)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise HTTPException(422, '工具桥自检只允许本机回环地址')
        result = await run_in_threadpool(mcp_bridge_selftest, f'http://{parsed.netloc}', 20.0)
        database.audit('mcp_bridge_selftest', {'ok': bool(result.get('ok')), 'tools': result.get('tool_count')})
        return result

    @app.get('/api/vault/connection')
    def vault_connection():
        payload = describe_vault(vault_dir, read_only=vault_read_only)
        payload['runtime'] = runtime_label
        payload['read_only_env'] = READ_ONLY_ENV
        payload['restart_required_for_changes'] = True
        return payload

    @app.post('/api/vault/connection')
    async def update_vault_connection(payload: VaultConnectionUpdate):
        """Persist the vault path and read-only mode to the local .env; restart applies it."""
        if vault_scoped:
            raise HTTPException(409, '当前实例由 Obsidian 库承载，不能在界面里切换库；请在 Obsidian 里打开另一个库。')
        if runtime_label != 'personal':
            raise HTTPException(409, '隔离验收环境不允许修改 Vault 连接')
        try:
            target = await run_in_threadpool(normalize_vault_path, payload.path)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        try:
            await run_in_threadpool(write_env_value, env_path, 'VAULT_DIR', str(target))
            await run_in_threadpool(write_env_value, env_path, READ_ONLY_ENV, '1' if payload.read_only else '0')
        except OSError as error:
            raise HTTPException(409, '无法写入本地 .env；请检查文件权限后重试') from error
        # Only shape, not the path itself, goes into the audit log.
        database.audit('vault_connection_updated', {
            'read_only': payload.read_only,
            'path_length': len(str(target)),
            'looks_like_obsidian': (target / '.obsidian').is_dir(),
        })
        described = describe_vault(target, read_only=payload.read_only)
        return {**described, 'saved': True, 'restart_required': True,
                'note': '已写入本地 .env；重启本地服务后生效。只读模式下扫描与检索照常，写入会被拒绝。'}

    @app.post('/api/vault/scan-preview')
    def vault_scan_preview():
        """Read-only diff: what a real scan would index. Touches no file and no row."""
        return preview_scan(database, vault_dir)

    @app.get('/api/vault/onboarding.json')
    def vault_onboarding_json():
        """Everything a first connection needs to know; counts only, no note text."""
        return onboarding_report(database, vault_dir, read_only=vault_read_only)

    @app.get('/api/vault/onboarding.md')
    def vault_onboarding_markdown():
        report = onboarding_report(database, vault_dir, read_only=vault_read_only)
        body = render_onboarding_markdown(report)
        return PlainTextResponse(body, media_type='text/markdown; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename="vault-onboarding.md"'})

    @app.post('/api/notes', status_code=201)
    async def note(payload: NoteRequest):
        try:
            ensure_vault_writable(vault_read_only, '新建笔记')
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        async with app.state.data_lock:
            return await run_in_threadpool(create_note, vault_dir, database, payload.title, payload.content, payload.type)

    @app.get('/api/memories')
    def memories(status: Literal['candidate','active','rejected','archived'] | None = Query(default=None)):
        return database.list_memories(status)

    @app.patch('/api/memories/{memory_id}/status')
    async def memory_status(memory_id: int, payload: MemoryStatusRequest):
        async with app.state.data_lock:
            try:
                result = database.update_memory_status(memory_id, payload.status)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        if not result:
            raise HTTPException(404, '记忆不存在')
        database.audit('memory_status_changed', {'memory_id':memory_id,'status':payload.status})
        return result

    @app.post('/api/memories/batch-status')
    async def memory_batch_status(payload: MemoryBatchStatusRequest):
        """Explicit multi-select batch review; every changed memory keeps its own audit row."""
        async with app.state.data_lock:
            try:
                result = await run_in_threadpool(database.batch_update_memory_status, payload.ids, payload.status)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        for memory_id in result['updated_ids']:
            database.audit('memory_status_changed', {'memory_id': memory_id, 'status': payload.status, 'batch': True})
        return result

    @app.post('/api/memories/tidy-candidates')
    async def memory_tidy_candidates(payload: MemoryTidyRequest):
        """Preview or apply readable rewrites of candidate titles/content (raw quotes kept)."""
        async with app.state.data_lock:
            result = await run_in_threadpool(database.tidy_candidate_memories, dry_run=payload.dry_run)
        if not payload.dry_run:
            database.audit('candidates_tidied', {
                'updated': result['updated'], 'unchanged': result['unchanged'],
                'not_memory_like': result['not_memory_like'],
            })
        return result

    @app.post('/api/memories/{memory_id}/delete')
    async def memory_delete(memory_id: int, payload: MemoryDeleteRequest):
        if payload.confirm is not True:
            raise HTTPException(422, '请明确确认永久删除这条记忆')
        async with app.state.data_lock:
            result = await run_in_threadpool(database.delete_memory, memory_id)
        if not result:
            raise HTTPException(404, '记忆不存在')
        database.audit('memory_deleted', {
            'memory_id': memory_id, 'document_id': result['document_id'],
            'status_before': result['status_before'],
            'usages_removed': result['usages_removed'],
            'citations_removed': result['citations_removed'],
            'events_removed': result['events_removed'],
        })
        return result

    @app.patch('/api/memories/{memory_id}')
    async def edit_memory(memory_id: int, payload: MemoryEditRequest):
        async with app.state.data_lock:
            try:
                result = database.edit_memory(memory_id, payload.content, payload.title)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
        if not result:
            raise HTTPException(404, '记忆不存在')
        return result

    @app.get('/api/memories/{memory_id}/history')
    def history(memory_id: int):
        return {'history':database.memory_history(memory_id)}

    @app.get('/api/conversations')
    def conversations():
        return database.list_conversations()

    @app.get('/api/conversations/{conversation_id}')
    def conversation(conversation_id: int):
        result = database.get_conversation(conversation_id)
        if result is None:
            raise HTTPException(404, '会话不存在')
        return result

    def citation_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{'memory_id':row['id'], 'document_id':row['document_id'], 'path':row['path'],
                 'title':row['title'], 'quote':row['content'], 'source_quote':row['source_quote'],
                 'confidence':row['confidence'], 'revision':row['revision'],
                 'source_date':row.get('source_date'), 'valid_from':row.get('valid_from'),
                 'valid_until':row.get('valid_until'),
                 'low_confidence':bool(row.get('low_confidence')),
                 'semantic_score':row.get('semantic_score'), 'lexical_score':row.get('lexical_score'),
                 **{key:row.get(key) for key in ('source_start_line','source_end_line','source_start_offset','source_end_offset','source_location_basis')}} for row in rows]

    @app.post('/api/chat')
    async def chat(payload: ChatRequest, request: Request):
        if app.state.chat_lock.locked():
            raise HTTPException(429, '上一条回答尚未完成，请稍后再试')
        async with app.state.chat_lock:
            if payload.conversation_id and database.get_conversation(payload.conversation_id) is None:
                raise HTTPException(404, '会话不存在')
            conversation_id = payload.conversation_id or database.create_conversation(payload.message[:32])
            database.add_message(conversation_id, 'user', payload.message, [])
            async with app.state.data_lock:
                await run_in_threadpool(scan_vault, vault_dir, database)
                evidence = await run_in_threadpool(database.search_memories, payload.message, 6)
            mode, warning, usage = 'local', None, None
            answer = local_answer(evidence, cloud_available=gateway.configured)
            agent_run = None
            if payload.agent and not payload.use_harness:
                agent_run = await run_in_threadpool(agent_module.run, tool_service, payload.message, gateway.configured)
                answer += agent_run['digest']
                database.audit('agent_planned', {'tools': agent_run['tools'], 'count': len(agent_run['steps'])})
            harness_meta = None
            if payload.use_harness:
                if not harness_service.available():
                    mode, warning = 'fallback', '未检测到 dsh，Harness 模式不可用，已使用本地检索。'
                else:
                    base_url = f"http://{request.headers.get('host') or '127.0.0.1:8765'}"
                    try:
                        turn = await run_in_threadpool(harness_service.run_turn, conversation_id, payload.message, base_url)
                        answer, mode = turn['answer'], 'harness'
                        harness_meta = {'session_id': turn['session_id'], 'resumed': turn['resumed'],
                                        'tools': turn['tools'], 'stop_reason': turn['stop_reason']}
                        database.audit('harness_turn', {'conversation_id': conversation_id, 'ok': True,
                                                        'tools': len(turn['tools']), 'stop_reason': turn['stop_reason']})
                    except harness_brain.HarnessError as error:
                        mode, warning = 'fallback', f'Harness 未完成：{error} 已使用本地检索。'
                        database.audit('harness_turn', {'conversation_id': conversation_id, 'ok': False})
            if payload.use_cloud and not payload.use_harness:
                if not gateway.configured:
                    mode, warning = 'fallback', '模型尚未配置，已使用本地检索。密钥位置：本地 .env 的 MODEL_API_KEY（设置页不会显示或修改）。'
                else:
                    try:
                        generated = await gateway.generate(payload.message, evidence, general_conversation=not evidence)
                        if generated.get('answer'):
                            answer, mode, usage = generated['answer'], 'model', generated.get('usage')
                        else:
                            mode, warning = 'fallback', '模型没有生成可用回答，已使用本地检索'
                    except Exception as error:
                        mode, warning = 'fallback', '云端调用未完成，已使用本地检索。请检查模型连接或额度。'
                        database.audit('model_error', {'code':getattr(error,'code','model_unavailable')})
            # Do not surface withdrawn facts if another tab changed memory during the request.
            async with app.state.data_lock:
                await run_in_threadpool(scan_vault, vault_dir, database)
                current = {item['id']:item for item in await run_in_threadpool(database.search_memories, payload.message, 6)}
                if any(item['id'] not in current or current[item['id']]['revision'] != item['revision'] for item in evidence):
                    evidence = list(current.values())
                    answer, mode, warning = local_answer(evidence, cloud_available=gateway.configured), 'fallback', '回答期间记忆已变化，本次结果已按当前记忆重新生成'
                    if agent_run:
                        answer += agent_run['digest']
                citations = citation_payload(evidence)
                database.add_message(conversation_id,'assistant',answer,citations)
            database.audit('chat_completed', {'conversation_id':conversation_id,'mode':mode,'evidence_count':len(citations)})
            return {'conversation_id':conversation_id,'answer':answer,'citations':citations,'mode':mode,'warning':warning,'usage':usage,
                    'harness': harness_meta,
                    'agent': {'enabled': bool(payload.agent), 'steps': (agent_run or {}).get('steps', []),
                              'note': '本地步骤只在这台机器上执行；云端模式只发送问题与相关已确认记忆；'
                                      'Harness 模式由 dsh 决定上下文，只挂载只读工具。'}}

    def sse_event(payload: dict[str, Any]) -> str:
        return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'

    @app.post('/api/chat/stream')
    async def chat_stream(payload: ChatRequest, request: Request):
        """Harness-only SSE: incremental text plus one final done event."""
        if not payload.use_harness:
            raise HTTPException(400, '流式回答目前只用于 Harness 模式。')
        if payload.conversation_id and database.get_conversation(payload.conversation_id) is None:
            raise HTTPException(404, '会话不存在')
        if app.state.chat_lock.locked():
            raise HTTPException(429, '上一条回答尚未完成，请稍后再发')
        conversation_id = payload.conversation_id or database.create_conversation(payload.message[:32])
        database.add_message(conversation_id, 'user', payload.message, [])
        base_url = f"http://{request.headers.get('host') or '127.0.0.1:8765'}"

        async def event_stream():
            async with app.state.chat_lock:
                events: asyncio.Queue = asyncio.Queue()
                loop = asyncio.get_running_loop()

                def on_delta(text: str) -> None:
                    loop.call_soon_threadsafe(events.put_nowait, ('delta', text))

                async def worker() -> None:
                    try:
                        turn = await run_in_threadpool(
                            harness_service.run_turn, conversation_id, payload.message, base_url, on_delta)
                        await events.put(('done', turn))
                    except Exception as error:  # HarnessError or unexpected: fall back locally
                        await events.put(('fallback', str(error)))

                task = asyncio.create_task(worker())
                turn, warning = None, None
                try:
                    while True:
                        kind, value = await events.get()
                        if kind == 'delta':
                            yield sse_event({'type': 'delta', 'text': value})
                            continue
                        if kind == 'done':
                            turn = value
                        else:
                            warning = f'Harness 未完成：{value} 已使用本地回答。'
                        break
                finally:
                    await task
                if turn is not None:
                    mode, answer, citations, harness_meta = 'harness', turn['answer'], [], {
                        'session_id': turn['session_id'], 'resumed': turn['resumed'],
                        'tools': turn['tools'], 'stop_reason': turn['stop_reason']}
                    database.audit('harness_turn', {'conversation_id': conversation_id, 'ok': True,
                                                    'tools': len(turn['tools']), 'stop_reason': turn['stop_reason']})
                else:
                    mode, harness_meta, warning = 'fallback', None, warning or 'Harness 未完成，已使用本地回答。'
                    database.audit('harness_turn', {'conversation_id': conversation_id, 'ok': False})
                    async with app.state.data_lock:
                        await run_in_threadpool(scan_vault, vault_dir, database)
                        evidence = await run_in_threadpool(database.search_memories, payload.message, 6)
                    answer = local_answer(evidence, cloud_available=gateway.configured)
                    citations = citation_payload(evidence)
                database.add_message(conversation_id, 'assistant', answer, citations)
                database.audit('chat_completed', {'conversation_id': conversation_id, 'mode': mode,
                                                  'evidence_count': len(citations), 'stream': True})
                yield sse_event({'type': 'done', 'conversation_id': conversation_id, 'answer': answer,
                                 'citations': citations, 'mode': mode, 'warning': warning,
                                 'harness': harness_meta})

        return StreamingResponse(event_stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    app.include_router(create_goals_router(database))
    goal_service = GoalTaskService(database)

    async def journal_data_guard():
        # Journal Markdown exports share the same filesystem/DB boundary as scans and imports.
        async with app.state.data_lock:
            yield

    # 笔记路由必须先于 `/journal/{day}` 注册：否则 `note` 会被当成日期参数（FastAPI 按注册顺序匹配）。
    app.include_router(create_journal_note_router(database, vault_dir, read_only=vault_read_only,
                                                  gateway=gateway),
                       dependencies=[Depends(journal_data_guard)])
    app.include_router(create_todo_router(vault_dir, database, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_tasks_router(vault_dir, database, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_journal_router(database, vault_dir, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_task_import_router(database, vault_dir), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_habits_router(database), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_learning_router(database), dependencies=[Depends(journal_data_guard)])
    # Body records share the daily-record database boundary; one optional row per day.
    app.include_router(create_body_router(database), dependencies=[Depends(journal_data_guard)])
    # Local four-pillars reference: birth data stays in this database, never sent anywhere.
    app.include_router(create_bazi_router(database), dependencies=[Depends(journal_data_guard)])
    # Conversation snapshots are ordinary vault notes; keep them on the same data boundary.
    app.include_router(create_conversation_memory_router(database, vault_dir, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    # One allow-listed share link at a time; the snapshot note shares the same vault boundary.
    app.include_router(create_share_import_router(database, vault_dir, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    # Read-only live schema export: structure and counts only, no note text, no audit.
    app.include_router(create_schema_router(database))
    # Read-only tools for agents (today, memories, goals, habits, learning, journal, profile, vault).
    app.include_router(create_tools_router(database, vault_dir))
    # Identity lives in the Vault (`01-Identity/身份（identity）.md`): Markdown is the source of truth.
    app.include_router(create_identity_router(vault_dir, read_only=vault_read_only),
                       dependencies=[Depends(journal_data_guard)])
    # Workbench: one read of everything today; the polish step is opt-in and calls the model.
    app.include_router(create_workbench_router(database, vault_dir, gateway),
                       dependencies=[Depends(journal_data_guard)])
    tool_service = ToolService(database, vault_dir)
    harness_service = harness_brain.HarnessBrain(database, database.path.parent / 'harness-workspace')

    @app.get('/api/harness/status')
    def harness_status():
        return harness_service.status()

    @app.post('/api/harness/reset')
    async def harness_reset(payload: HarnessResetRequest):
        result = await run_in_threadpool(harness_service.reset, payload.conversation_id)
        database.audit('harness_reset', {'conversation_id': payload.conversation_id,
                                         'cleared': result['cleared_sessions']})
        return {'ok': True, **result}
    # Proactive suggestion cards: deterministic local rules, never an action.
    app.include_router(create_suggestions_router(database, vault_dir))
    # 时钟天气卡：白名单出站 + 30 分钟内存缓存，纯读不落盘。
    app.include_router(create_weather_router())
    # 记录模块：今天动过的保险库文件（真实 mtime），日记预填的原料。
    app.include_router(create_activity_router(database, vault_dir),
                       dependencies=[Depends(journal_data_guard)])
    # 横幅统计：连续日记天数 / 今日完成度 / 记忆与资料计数。
    app.include_router(create_banner_stats_router(database, vault_dir),
                       dependencies=[Depends(journal_data_guard)])
    # 工作区（多人/多数据库）：切换写入 .env，重启后生效；QA 环境只读。
    workspace_options = {} if workspace_registry is None else {'registry_path': workspace_registry}
    app.include_router(create_workspaces_router(
        data_dir=database.path.parent, vault_dir=vault_dir, read_only=vault_read_only,
        env_path=env_path, database=database, runtime_label=runtime_label,
        vault_scoped=bool(vault_scoped), **workspace_options))
    suggestion_service = SuggestionService(database, vault_dir)
    # Preview locks internally around scan+retrieval; do not add the shared guard here.
    app.include_router(create_profile_router(database, vault_dir, app.state.data_lock, APP_VERSION))
    app.include_router(create_scheduler_router(database, status_provider=lambda: dict(scheduler_state)))
    # Read-only export of the user's own memories; no vault or review writes.
    app.include_router(create_memory_export_router(database))
    # Read-only self-check: counts and integrity only, no content and no audit.
    app.include_router(create_diagnostics_router(database, vault_dir, APP_VERSION))
    # Read-only export of goals and tasks; no state changes.
    app.include_router(create_planning_export_router(database))
    # Read-only export of habits and study records; no state changes.
    app.include_router(create_growth_export_router(database))
    # Read-only export of saved conversations and citations; no state changes.
    app.include_router(create_conversation_export_router(database))
    # Read-only Vault structure audit; no writes, no restricted partitions.
    app.include_router(create_vault_audit_router(vault_dir))
    # Read-only audit summary: event types and counts, no content.
    app.include_router(create_audit_summary_router(database))
    # Read-only export of every saved daily record.
    app.include_router(create_journal_export_router(database))
    # One ZIP with every human-readable export (not a restore package).
    app.include_router(create_export_bundle_router(database))

    def import_archives():
        folder = database.path.parent / 'imports'
        found = {}
        if not folder.exists():
            return found
        for directory, subdirs, files in os.walk(folder, followlinks=False):
            subdirs[:] = [name for name in subdirs if not (Path(directory)/name).is_symlink()
                          and not (hasattr(Path(directory)/name,'is_junction') and (Path(directory)/name).is_junction())]
            for name in files:
                if not name.lower().endswith('.zip'):
                    continue
                path = Path(directory)/name
                relative = path.relative_to(folder).as_posix()
                try:
                    safe_note_path(folder,relative)
                except ValueError:
                    continue
                archive_id = hashlib.sha256(relative.encode('utf-8')).hexdigest()[:16]
                found[archive_id] = path
                if len(found) >= 100:
                    return found
        return found

    @app.get('/api/imports')
    def imports():
        return [{'id':key,'filename':path.name,'relative_path':path.relative_to(database.path.parent / 'imports').as_posix(),
                 'bytes':path.stat().st_size} for key,path in import_archives().items()]

    @app.post('/api/imports/{archive_id}/preview')
    async def import_preview(archive_id: str):
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404,'导出文件不存在')
        try:
            return await run_in_threadpool(preview_wolai_archive, archive, archive.parent / 'preview')
        except WolaiImportError as error:
            raise HTTPException(400,str(error)) from error

    @app.post('/api/imports/{archive_id}/apply')
    async def import_apply(archive_id: str, selection: ImportSelection):
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404,'导出文件不存在')
        try:
            ensure_vault_writable(vault_read_only, '导入 Wolai 记录')
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(import_selected_notes, archive, selection.selected_paths, vault_dir, database)
            except WolaiImportError as error:
                raise HTTPException(409,str(error)) from error

    @app.post('/api/imports/{archive_id}/states')
    async def import_states(archive_id: str, selection: ImportSelection):
        """Read-only: which of the selected notes are new, unchanged, updated or locally edited."""
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        try:
            return await run_in_threadpool(preview_import_states, archive, selection.selected_paths, vault_dir)
        except WolaiImportError as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/imports/{archive_id}/links/audit')
    async def import_link_audit(archive_id: str, selection: ImportSelection):
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(audit_wolai_links, archive, selection.selected_paths, archive.parent / 'link-audit')
            except WolaiImportError as error:
                raise HTTPException(400, str(error)) from error

    @app.post('/api/imports/{archive_id}/attachments/plan')
    async def import_attachment_plan(archive_id: str, selection: ImportSelection):
        """Read-only preview of attachment localization; never writes or copies."""
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(
                    plan_wolai_attachment_localization, archive, selection.selected_paths, vault_dir)
            except WolaiImportError as error:
                raise HTTPException(400, str(error)) from error

    @app.post('/api/imports/{archive_id}/attachments/apply')
    async def import_attachment_apply(archive_id: str, selection: ImportSelection):
        """Copy planned attachments, keep history and rewrite links (explicit call only)."""
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        try:
            ensure_vault_writable(vault_read_only, '本地化附件')
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(
                    localize_wolai_attachments, archive, selection.selected_paths, vault_dir, database)
            except WolaiImportError as error:
                raise HTTPException(409, str(error)) from error

    @app.post('/api/imports/{archive_id}/history')
    async def import_history(archive_id: str, selection: ImportSelection):
        """Read-only inventory of managed history versions for the selection."""
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(
                    list_wolai_import_history, archive, selection.selected_paths, vault_dir, database)
            except WolaiImportError as error:
                raise HTTPException(400, str(error)) from error

    @app.post('/api/imports/{archive_id}/history/restore')
    async def import_history_restore(archive_id: str, payload: HistoryRestore):
        """Restore one managed version after archiving the current file."""
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        try:
            ensure_vault_writable(vault_read_only, '恢复导入历史')
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(
                    restore_wolai_import_history, archive, payload.source_path, payload.sha256, vault_dir, database)
            except WolaiImportError as error:
                raise HTTPException(409, str(error)) from error

    @app.post('/api/imports/{archive_id}/history/cleanup-preview')
    async def import_history_cleanup_preview(archive_id: str, selection: ImportSelection):
        """Read-only retention preview for managed history; never deletes."""
        archive = import_archives().get(archive_id)
        if archive is None:
            raise HTTPException(404, '导出文件不存在')
        async with app.state.data_lock:
            try:
                return await run_in_threadpool(
                    preview_wolai_history_cleanup, archive, selection.selected_paths, vault_dir)
            except WolaiImportError as error:
                raise HTTPException(400, str(error)) from error

    @app.get('/api/backups')
    def backups():
        folder = database.path.parent / 'backups'
        if not folder.exists():
            return []
        return [{'filename':path.name,'bytes':path.stat().st_size,
                 'modified_at':datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo('Asia/Shanghai')).isoformat()}
                for path in sorted(folder.glob('*.zip'), reverse=True) if path.is_file() and not path.is_symlink()]

    @app.get('/api/backups/plan')
    def backup_plan(keep: int = Query(default=10, ge=1, le=200)):
        # Read-only preview; nothing is deleted or moved.
        return plan_retention(database.path.parent / 'backups', keep)

    @app.get('/api/backups/restored')
    def restored_copies():
        # Read-only inventory; never reads record content, deletes or switches anything.
        return list_restored_copies(database.path.parent / 'restored')

    @app.get('/api/backups/restored/{name}/verify')
    def verify_restored(name: str):
        if not RESTORED_NAME_PATTERN.fullmatch(name):
            raise HTTPException(400, '恢复副本名无效')
        target = database.path.parent / 'restored' / name
        if not target.is_dir() or target.is_symlink():
            raise HTTPException(404, '恢复副本不存在')
        try:
            return verify_restored_copy(database.path.parent / 'restored', name)
        except BackupError as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/backups', status_code=201)
    async def backup():
        async with app.state.data_lock:
            try:
                result = await run_in_threadpool(create_backup, database, vault_dir, database.path.parent / 'backups')
            except BackupError as error:
                raise HTTPException(409, str(error)) from error
        database.audit('backup_created', {'filename':result['filename']})
        return result

    @app.post('/api/backups/{filename}/restore', status_code=201)
    async def restore(filename: str):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+\.zip',filename) or '..' in filename:
            raise HTTPException(400,'备份文件名无效')
        archive = database.path.parent / 'backups' / filename
        if not archive.is_file():
            raise HTTPException(404,'备份不存在')
        parent = database.path.parent / 'restored'
        parent.mkdir(parents=True,exist_ok=True)
        async with app.state.data_lock:
            try:
                result = await run_in_threadpool(restore_backup, archive, parent / uuid4().hex, allowed_parent=parent)
            except BackupError as error:
                raise HTTPException(409,str(error)) from error
        database.audit('backup_restored_to_new_directory', {'filename':filename})
        return result

    @app.post('/api/backups/drill', status_code=201)
    async def restore_drill(payload: RestoreDrillRequest):
        """One-click drill: fresh backup → independent restore copy → read-only verify → report."""
        if payload.confirm is not True:
            raise HTTPException(422, '请明确确认运行恢复演练（不会覆盖当前数据）')
        backups_dir = database.path.parent / 'backups'
        restored_root = database.path.parent / 'restored'
        diagnostics_dir = database.path.parent / 'diagnostics'
        async with app.state.data_lock:
            try:
                backup = await run_in_threadpool(create_backup, database, vault_dir, backups_dir)
                archive = backups_dir / backup['filename']
                restored_root.mkdir(parents=True, exist_ok=True)
                copy = await run_in_threadpool(
                    restore_backup, archive, restored_root / uuid4().hex, allowed_parent=restored_root)
                verification = await run_in_threadpool(
                    verify_restored_copy, restored_root, Path(copy['target_dir']).name)
            except BackupError as error:
                raise HTTPException(409, str(error)) from error
        report = render_restore_drill_report(backup, copy, verification)
        drills_dir = diagnostics_dir / 'drills'
        drills_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S%f')
        report_path = drills_dir / f'restore-drill-{stamp}.md'
        report_path.write_text(report, encoding='utf-8')
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / 'restore-drill-latest.md').write_text(report, encoding='utf-8')
        database.audit('restore_drill', {
            'ok': bool(verification.get('ok')), 'files': int(verification.get('file_count') or 0),
            'vault_files': int(verification.get('vault_files') or 0), 'report_name': report_path.name,
        })
        return {'ok': bool(verification.get('ok')), 'backup': backup, 'copy': copy,
                'verification': verification, 'report_path': str(report_path),
                'note': '演练只写入 data/restored 与 data/diagnostics：没有覆盖当前库、没有删除任何备份、没有切换数据。'}

    @app.get('/api/backups/drill/last.md')
    def restore_drill_report():
        report_path = database.path.parent / 'diagnostics' / 'restore-drill-latest.md'
        if not report_path.is_file():
            raise HTTPException(404, '还没有演练报告；先运行一次恢复演练')
        return PlainTextResponse(report_path.read_text(encoding='utf-8'),
                                 media_type='text/markdown; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename="restore-drill.md"'})

    @app.get('/api/backups/drill/reports')
    def restore_drill_reports():
        """History of generated drill reports; only lists this app's own diagnostics files."""
        folder = database.path.parent / 'diagnostics' / 'drills'
        if not folder.exists():
            return {'count': 0, 'reports': [], 'note': '还没有演练报告；运行一次恢复演练就会留下记录。'}
        reports = []
        for path in sorted(folder.glob('restore-drill-*.md'), reverse=True):
            if not path.is_file() or path.is_symlink():
                continue
            reports.append({
                'name': path.name,
                'bytes': path.stat().st_size,
                'created_at': datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
            })
        return {'count': len(reports), 'reports': reports[:50],
                'note': '报告只增不减；每个文件只含演练的计数与路径，不含任何笔记正文。'}

    @app.get('/api/backups/drill/reports/{name}')
    def restore_drill_report_file(name: str):
        if not re.fullmatch(r'restore-drill-[0-9T]{8,24}\.md', name):
            raise HTTPException(400, '演练报告名无效')
        path = database.path.parent / 'diagnostics' / 'drills' / name
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, '演练报告不存在')
        return PlainTextResponse(path.read_text(encoding='utf-8'),
                                 media_type='text/markdown; charset=utf-8',
                                 headers={'Content-Disposition': f'attachment; filename="{name}"'})

    @app.post('/api/daily-plan/draft')
    def daily_plan():
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        tasks = [task for task in goal_service.list_tasks() if task['status'] in {'todo','in_progress'}
                 and (task['due_date'] is None or task['due_date'] <= today.isoformat())]
        tasks.sort(key=lambda task: ({'urgent':0,'high':1,'medium':2,'low':3}[task['priority']],task['due_date'] or '9999'))
        return {'status':'draft', 'title':'今日计划草稿', 'date':today.isoformat(),
                'items':[{'task_id':task['id'],'title':task['title'],'reason':task['description'] or '来自尚未完成的任务',
                          'confirmed':False} for task in tasks[:3]],
                'message':'仅整理已有任务；草稿不会自动创建任务或标记完成。'}

    return app
