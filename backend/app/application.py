"""Application factory; tests inject temporary data and never start external services."""
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
from .deepseek_balance import DeepSeekBalanceService, create_deepseek_balance_router
from .dsh_proxy import DshProxy, create_dsh_router
from .dsh_web import DisabledDshRuntime, DshWebRuntime
from .envfile import write_env_value
from .audit_summary import create_audit_summary_router
from .backup import (
    RESTORED_NAME_PATTERN, BackupError, create_backup, list_restored_copies,
    plan_retention, render_restore_drill_report, restore_backup, verify_restored_copy,
)
from .conversation_export import create_conversation_export_router
from .conversation_memory import create_conversation_memory_router
from .schema_export import create_schema_router
from .tools import create_tools_router
from .mcp_bridge import run_selftest as mcp_bridge_selftest
from .workspaces import create_workspaces_router
from .vault_admin import create_vault_admin_router
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
from .growth_export import create_growth_export_router
from .identity import create_identity_router
from .activity import create_activity_router
from .banner_stats import create_banner_stats_router
from .journal_note import create_journal_note_router
from .todo_note import create_todo_router
from .tasks import create_tasks_router
from .dida_sync import DidaSyncService, create_dida_sync_router, dida_sync_loop
from .weather import create_weather_router
from .love import LoveSettings, create_love_router
from .love_lijie import create_lijie_router
from .journal_export import create_journal_export_router
from .habits import create_habits_router
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
from .planning_export import create_planning_export_router
from .scheduler import SchedulerService, create_scheduler_router
from .security import is_loopback_host, token_matches, validate_runtime_config
from .vault import EXTRACTION_VERSION, _memory_from_document, create_note, scan_vault, safe_note_path
from .migration_api import create_migration_router
from .ops import create_ops_router
from .pillars import create_pillars_router
from .steward import create_steward_router
from .vault_audit import create_vault_audit_router
from .wolai_import import WolaiImportError, preview_wolai_archive, import_selected_notes
from .wolai_import import preview_import_states
from .workbench import create_workbench_router

APP_VERSION = '0.99.0'

# Page manifest consumed by the local dsh sidebar plugin (0.88.0). The labels
# mirror frontend/nav-order.js; tests keep the two in sync.
# 对话页已移除（用户 2026-10-05：对话统一在 dsh 里），所以这里不再有 chat。
NAV_VIEWS = [
    ('workbench', '工作台'), ('life', '人生路'), ('self', '自我档案'),
    ('ops', '生活运营'), ('knowledge', '知识库'), ('settings', '设置与状态'),
]


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


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


def create_app(database: Database | None = None, vault_dir: Path | None = None, *,
               session_token: str | None = None,
               host: str = '127.0.0.1', security_environ: dict | None = None,
               runtime_label: str = 'personal',
               env_file: Path | None = None, workspace_registry: Path | None = None,
               vault_scoped: bool | None = None,
               dida_provider_factory: Any = None,
               enable_dida_auto_push: bool | None = None,
               dsh_runtime: DshWebRuntime | DisabledDshRuntime | None = None,
               enable_dsh: bool | None = None,
               dsh_workspace: Path | None = None) -> FastAPI:
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
    if runtime_label not in {'personal', 'qa'}:
        raise ValueError('Unsupported runtime label')
    if runtime_label == 'qa' and not is_loopback_host(host):
        raise ValueError('QA runtime must be local-only')
    env_path = Path(env_file) if env_file is not None else Path(__file__).resolve().parents[2] / '.env'
    vault_read_only = read_only_enabled(security_environ)
    database.initialize()
    vault_dir.mkdir(parents=True, exist_ok=True)
    data_lock = asyncio.Lock()
    dida_service = DidaSyncService(
        database, vault_dir, read_only=vault_read_only,
        supported=(runtime_label == 'personal'),
        provider_factory=dida_provider_factory,
    )
    if dsh_runtime is None:
        if enable_dsh is False or runtime_label == 'qa':
            dsh_runtime = DisabledDshRuntime(vault_dir)
        else:
            configured_workspace = (dsh_workspace
                                    or os.environ.get('TORCH_DSH_WORKSPACE')
                                    or vault_dir)
            dsh_runtime = DshWebRuntime(
                database.path.parent, Path(configured_workspace),
            )
    dsh_proxy = DshProxy(dsh_runtime)
    deepseek_balance = DeepSeekBalanceService(
        database.path.parent, enabled=runtime_label == 'personal')
    scheduler_service = SchedulerService(database, vault_dir)
    scheduler_state: dict[str, Any] = {"last_check_at": None, "consecutive_failures": 0, "last_error_at": None}

    # 每一次本地任务/清单写操作后立即尝试回写滴答。250ms 防抖只合并同一批快速
    # 操作；正常的一次点击/保存都会触发一次。测试和自定义 Provider 默认关闭，
    # 避免后台任务干扰测试里的显式同步。
    auto_push_enabled = (
        enable_dida_auto_push
        if enable_dida_auto_push is not None
        else (runtime_label == "personal" and dida_provider_factory is None)
    )
    auto_push_state: dict[str, Any] = {"task": None}

    async def _dida_auto_push() -> None:
        await asyncio.sleep(0.25)
        if not dida_service.supported:
            return
        try:
            # 如果正好有一次同步/回写在跑，等它结束再补一次；最多等 10 秒，
            # 剩下的交给后台 15 秒循环兜底。
            for _ in range(20):
                if dida_service._running:
                    await asyncio.sleep(0.5)
                    continue
                result = await dida_service.push_local_changes()
                if result.get("skipped") != "running":
                    break
                await asyncio.sleep(0.5)
            await dida_service.push_deletions()
        except Exception:
            # 失败原因已由 service 记录到 last_push_error/审计；后台任务不再抛出。
            pass

    def schedule_dida_auto_push() -> None:
        if (not auto_push_enabled or not dida_service.supported
                or not dida_service.credentials.configured()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pending = auto_push_state.get("task")
        if pending is not None and not pending.done():
            pending.cancel()
        auto_push_state["task"] = loop.create_task(_dida_auto_push())

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
        tasks_to_stop = [asyncio.create_task(scheduler_loop())]
        if dida_service.supported:
            tasks_to_stop.append(asyncio.create_task(dida_sync_loop(dida_service, data_lock)))
        try:
            yield
        finally:
            pending_push = auto_push_state.get("task")
            if pending_push is not None:
                pending_push.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending_push
            for task in tasks_to_stop:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            with contextlib.suppress(Exception):
                await dsh_proxy.close()
            with contextlib.suppress(Exception):
                await dsh_runtime.close()

    local_token = session_token or secrets.token_urlsafe(32)
    frontend = Path(__file__).resolve().parents[2] / 'frontend'
    app = FastAPI(title='薪火OS', version=APP_VERSION, lifespan=lifespan)
    app.state.database = database
    app.state.vault_dir = vault_dir
    app.state.runtime_label = runtime_label
    app.state.vault_scoped = bool(vault_scoped)
    app.state.data_lock = data_lock
    app.state.dida_sync_service = dida_service
    app.state.dsh_runtime = dsh_runtime
    app.state.dsh_proxy = dsh_proxy
    app.state.deepseek_balance = deepseek_balance
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
        # 本地任务/清单的写操作完成后立即回写滴答（后台防抖任务，不阻塞响应）。
        if (auto_push_enabled and response.status_code < 400
                and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
                and request.url.path.startswith('/api/time/')):
            schedule_dida_auto_push()
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        harness = request.url.path == '/harness' or request.url.path.startswith('/harness/')
        # 恋爱报告预览是自带内联样式的本地 HTML 文档；只放宽样式，不放脚本，
        # 也不把它并进 /api 的 JSON 响应。用窄 CSP 而不是全站放开 unsafe-inline。
        love_report_preview = (
            request.url.path.startswith('/api/love/projects/')
            and request.url.path.endswith('/report/preview')
        )
        if love_report_preview:
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Content-Security-Policy'] = (
                "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                "base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'self' http://127.0.0.1:* http://localhost:* app:")
        elif not harness:
            response.headers['Cache-Control'] = 'no-store'
            # The workbench may only be framed from this machine (user rule
            # 2026-10-05: the product lives inside dsh, whose port can vary) or
            # from the local Obsidian app (user rule 2026-10-05 late: the whole
            # product also lives inside Obsidian as an embedded view; Obsidian
            # frames pages with the `app://obsidian.md` origin, hence the `app:`
            # scheme source below).
            # X-Frame-Options cannot express an allow-list, so framing is governed
            # by CSP frame-ancestors: loopback hosts on any port, nothing else.
            # /harness is dsh's own document: it needs its inline boot script,
            # so the proxy supplies only a frame-ancestors policy there.
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
        return {'vault_dir':str(vault_dir), 'timezone':'Asia/Shanghai', 'version':APP_VERSION,
                'runtime_label':runtime_label, 'vault_name':Path(vault_dir).name,
                'vault_scoped':bool(vault_scoped), 'default_mode':'dsh',
                'capabilities':{
                    'attachment_localize':True,'attachment_plan':True,
                    'audit_summary':True,'backup_plan':True,'candidate_tidy':True,
                    'conversation_export':True,'conversation_extract':True,
                    'deepseek_balance':runtime_label == 'personal',
                    'diagnostics':True,'dida_sync':runtime_label == 'personal',
                    'dsh_web':getattr(dsh_runtime, 'enabled', False),
                    'export_bundle':True,'growth_export':True,'habits':True,
                    'history_cleanup_preview':True,'history_restore':True,'identity':True,
                    'import_history':True,'journal_export':True,'link_audit':True,
                    'love': runtime_label == 'personal',
                    'love_lijie': runtime_label == 'personal',
                    'mcp_bridge':True,'memory_batch':True,'memory_delete':True,
                    'memory_export':True,'monthly_review':True,'planning_export':True,
                    'restore_drill':True,'restored_copies':True,'scheduler':True,
                    'scheduler_weekly':True,'schema_export':True,'task_reorder':True,
                    'tasks':True,'tools':True,'vault_audit':True,
                    'vault_admin':runtime_label == 'personal',
                    'vault_connection':not vault_scoped,'weather':True,'workbench':True,
                    'workspaces':runtime_label == 'personal' and not vault_scoped,
                }}

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

    async def journal_data_guard():
        # Journal Markdown exports share the same filesystem/DB boundary as scans and imports.
        async with app.state.data_lock:
            yield

    # 笔记路由必须先于 `/journal/{day}` 注册：否则 `note` 会被当成日期参数（FastAPI 按注册顺序匹配）。
    app.include_router(create_journal_note_router(database, vault_dir, read_only=vault_read_only),
                       dependencies=[Depends(journal_data_guard)])
    app.include_router(create_todo_router(vault_dir, database, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_tasks_router(vault_dir, database, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    # 滴答同步：拉取在 data_lock 外，写入在 run_dida_sync 内手动加锁，避免网络请求阻塞任务页。
    app.include_router(create_dida_sync_router(dida_service, data_lock))
    app.include_router(create_task_import_router(database, vault_dir), dependencies=[Depends(journal_data_guard)])
    app.include_router(create_habits_router(database, vault_dir=vault_dir),
                       dependencies=[Depends(journal_data_guard)])
    # Conversation snapshots are ordinary vault notes; keep them on the same data boundary.
    app.include_router(create_conversation_memory_router(database, vault_dir, read_only=vault_read_only), dependencies=[Depends(journal_data_guard)])
    # Read-only live schema export: structure and counts only, no note text, no audit.
    app.include_router(create_schema_router(database))
    # Read-only tools for agents (Markdown goals/tasks/body + memories, habits, journal, vault).
    app.include_router(create_tools_router(database, vault_dir))
    # Identity lives in the Vault (`01-Identity/身份（identity）.md`): Markdown is the source of truth.
    app.include_router(create_identity_router(vault_dir, read_only=vault_read_only),
                       dependencies=[Depends(journal_data_guard)])
    # Workbench: one read of everything today; drafts are deterministic.
    app.include_router(create_workbench_router(database, vault_dir),
                       dependencies=[Depends(journal_data_guard)])
    app.include_router(create_dsh_router(dsh_runtime, dsh_proxy))

    # 时钟天气卡：白名单出站 + 30 分钟内存缓存，纯读不落盘。
    app.include_router(create_weather_router())
    # DeepSeek 余额：官方余额接口的白名单代理；Key 不返回前端、不写日志，QA 模式不联网。
    app.include_router(create_deepseek_balance_router(deepseek_balance))
    # 恋爱系统：原生读写原有 love_analysis.db，不启动独立服务；AI 只走用户配置的 DeepSeek。
    # QA 是隔离的虚构工作区，不注册、也不触碰真实恋爱数据；配置只在个人模式下读取 .env。
    if runtime_label == 'personal':
        from .config import (
            LOVE_API_BASE_URL, LOVE_API_MODEL, LOVE_APP_DIR, LOVE_DB_PATH,
            LOVE_EXCEL_DIR, LOVE_STOPWORDS_PATH, LOVE_TIMETABLE_PATH,
        )
        love_settings = LoveSettings(
            db_path=LOVE_DB_PATH, app_dir=LOVE_APP_DIR,
            stopwords_path=LOVE_STOPWORDS_PATH, excel_dir=LOVE_EXCEL_DIR,
            timetable_path=LOVE_TIMETABLE_PATH, api_base_url=LOVE_API_BASE_URL,
            api_model=LOVE_API_MODEL,
        )
        app.state.love_settings = love_settings
        app.include_router(create_love_router(settings=love_settings))
        app.include_router(create_lijie_router(
            love_settings.excel_dir, love_settings.timetable_path))
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
    # 设置页「个人数据库」：按四库模板创建新库（含 .obsidian 预设与插件），只写新文件夹。
    app.include_router(create_vault_admin_router(
        database=database, vault_dir=vault_dir, runtime_label=runtime_label,
        vault_scoped=bool(vault_scoped), registry_path=workspace_registry))
    # Preview locks internally around scan+retrieval; do not add the shared guard here.
    app.include_router(create_scheduler_router(
        database, status_provider=lambda: dict(scheduler_state), vault_dir=vault_dir))
    # Read-only export of the user's own memories; no vault or review writes.
    app.include_router(create_memory_export_router(database))
    # Read-only self-check: counts and integrity only, no content and no audit.
    app.include_router(create_diagnostics_router(database, vault_dir, APP_VERSION))
    # Read-only export of Markdown goals/tasks; no state changes.
    app.include_router(create_planning_export_router(database, vault_dir))
    # Read-only export of habits and check-ins; no state changes.
    app.include_router(create_growth_export_router(database))
    # Read-only export of saved conversations and citations; no state changes.
    app.include_router(create_conversation_export_router(database))
    # 四库布局迁移：预览只读，执行/回滚需要显式确认并持有 data_lock。
    app.include_router(create_migration_router(
        database, vault_dir, app.state.data_lock, read_only=vault_read_only))
    # 四库只读浏览：新建、修改与捕获都在 Obsidian 里完成。
    app.include_router(create_pillars_router(
        database, vault_dir, read_only=vault_read_only))
    # 薪火管家：确定性提案与健康检查。
    app.include_router(create_steward_router(
        database, vault_dir, read_only=vault_read_only))
    # 生活运营：体重/账目月度 Markdown 表。
    app.include_router(create_ops_router(vault_dir, read_only=vault_read_only))
    # Read-only Vault structure audit; no writes, no restricted partitions.
    app.include_router(create_vault_audit_router(vault_dir))
    # Read-only audit summary: event types and counts, no content.
    app.include_router(create_audit_summary_router(database))
    # Read-only export of every saved daily record.
    app.include_router(create_journal_export_router(database))
    # One ZIP with every human-readable export (not a restore package).
    app.include_router(create_export_bundle_router(database, vault_dir))

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

    return app
