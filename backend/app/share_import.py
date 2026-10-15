"""Import one DeepSeek share link into reviewable candidate memories.

The user pastes a public share link; the server fetches exactly one allow-listed
URL (`chat.deepseek.com`, https, no redirects, timeout and size caps), keeps only
the *user's* own statements, and writes them as an ordinary Markdown note whose
sentences become candidates. Nothing is confirmed automatically, the assistant's
words are never treated as facts, and the whole path is opt-in per link.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from .conversation_memory import collect_sentences
from .db import Database
from .note_pipeline import write_candidate_note
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SHANGHAI = ZoneInfo('Asia/Shanghai')
ALLOWED_HOST = 'chat.deepseek.com'
API_URL = f'https://{ALLOWED_HOST}/api/v0/share/content'
SHARE_ID = re.compile(r'^[A-Za-z0-9_-]{4,64}$')
MAX_RESPONSE_BYTES = 4_000_000
TIMEOUT_SECONDS = 20.0
SOURCE_KIND = 'deepseek-share'


class ShareImportError(RuntimeError):
    """Upstream or transport problem; the router maps it to 502/413."""


def _source_key(share_id: str) -> str:
    return f'deepseek-share-{share_id}'


def parse_share_reference(value: str) -> str:
    """Accept a bare share id or one canonical share URL; reject everything else."""
    text = str(value or '').strip()
    if not text:
        raise ValueError('请粘贴 DeepSeek 分享链接或分享 ID')
    if SHARE_ID.match(text):
        return text
    parts = urlsplit(text)
    if parts.scheme != 'https':
        raise ValueError('只接受 https 的分享链接')
    if parts.hostname != ALLOWED_HOST or parts.port is not None:
        raise ValueError(f'只接受 {ALLOWED_HOST} 的分享链接')
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError('分享链接里不要带查询参数、锚点或账号信息')
    match = re.fullmatch(r'/share/([A-Za-z0-9_-]{4,64})/?', parts.path)
    if not match:
        raise ValueError('分享链接格式应为 https://chat.deepseek.com/share/<id>')
    return match.group(1)


def http_fetch(share_id: str) -> dict[str, Any]:
    """One allow-listed GET: no redirects, byte cap, no retry."""
    try:
        with httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS) as client:
            with client.stream('GET', API_URL, params={'share_id': share_id}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    raise ShareImportError('分享链接被重定向，已停止抓取')
                if response.status_code != 200:
                    raise ShareImportError(f'分享接口返回 {response.status_code}，请稍后再试')
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ShareImportError('分享内容超过大小上限，已停止抓取')
                    chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise ShareImportError('抓取分享内容超时，请稍后再试') from exc
    except httpx.HTTPError as exc:
        raise ShareImportError('无法连接分享服务，请检查网络后重试') from exc
    try:
        return json.loads(b''.join(chunks))
    except json.JSONDecodeError as exc:
        raise ShareImportError('分享接口返回的内容不是 JSON') from exc


def parse_shared_conversation(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the share payload to role/content messages; only user REQUEST text is kept."""
    if not isinstance(payload, dict) or payload.get('code') != 0:
        raise ShareImportError('分享内容不可用（可能已取消分享或链接已失效）')
    biz = ((payload.get('data') or {}).get('biz_data')) or {}
    if biz.get('biz_code') not in (0, None):
        raise ShareImportError('分享内容不可用（可能已取消分享或链接已失效）')
    messages: list[dict[str, Any]] = []
    stamps: list[float] = []
    for index, raw in enumerate(biz.get('messages') or [], start=1):
        role = str(raw.get('role') or '').lower()
        # Only the user's own words are read at all: the assistant's text (and any
        # thinking/tool fragments) never enters this process.
        if role != 'user':
            continue
        parts = [str(fragment.get('content') or '') for fragment in raw.get('fragments') or []
                 if str(fragment.get('type') or '').upper() == 'REQUEST']
        content = '\n'.join(part for part in parts if part.strip()).strip()
        if not content:
            continue
        try:
            stamps.append(float(raw.get('inserted_at')))
        except (TypeError, ValueError):
            pass
        messages.append({'id': raw.get('message_id') or index, 'role': role, 'content': content})
    created_at = ''
    if stamps:
        created_at = datetime.fromtimestamp(min(stamps), tz=timezone.utc).astimezone(SHANGHAI).isoformat(timespec='seconds')
    return {
        'title': str(biz.get('title') or '').strip(),
        'model_type': str(biz.get('model_type') or '').strip(),
        'messages': messages,
        'created_at': created_at,
    }


class SharePreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    url: str


class ShareApplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    url: str
    confirm: StrictBool = False


class ShareImportService:
    def __init__(self, database: Database, vault_dir: Path, fetcher: Callable[[str], dict[str, Any]] | None = None,
                 read_only: bool = False):
        self.database = database
        self.vault_dir = Path(vault_dir)
        self.fetcher = fetcher or http_fetch
        self.read_only = bool(read_only)

    def _relative_path(self, share_id: str) -> str:
        return f'90-Inbox/deepseek-share-{share_id}.md'

    def preview(self, reference: str) -> dict[str, Any]:
        share_id = parse_share_reference(reference)
        shared = parse_shared_conversation(self.fetcher(share_id))
        collected = collect_sentences(shared['messages'])
        existing = self.database.find_document_by_source_key(_source_key(share_id))
        return {
            'share_id': share_id,
            'title': shared['title'] or '未命名分享',
            'model_type': shared['model_type'],
            'created_at': shared['created_at'],
            'message_count': sum(1 for message in shared['messages'] if message['role'] == 'user'),
            'sentences': collected['sentences'],
            'kept': len(collected['sentences']),
            'skipped': collected['skipped'],
            'source_kind': SOURCE_KIND,
            'note_path': self._relative_path(share_id),
            'existing_document_id': existing['id'] if existing else None,
            'notice': '只提炼分享里你说的话；助手回答、思考与工具调用都不会进入候选。写入后仍需你在“记忆”页逐条确认。',
        }

    def apply(self, reference: str) -> dict[str, Any]:
        preview = self.preview(reference)
        if preview['kept'] == 0:
            return {**preview, 'written': False, 'reason': 'empty', 'memory_count': 0, 'candidates': []}
        ensure_vault_writable(self.read_only, '把分享链接导入为候选记忆')
        written = write_candidate_note(
            self.database, self.vault_dir,
            source_key=_source_key(preview['share_id']),
            relative_path=preview['note_path'],
            title=f"DeepSeek 分享：{preview['title'][:80]}",
            source_kind=SOURCE_KIND,
            created_at=preview['created_at'] or datetime.now(SHANGHAI).isoformat(timespec='seconds'),
            sentences=preview['sentences'],
            extra_metadata={'share_id': preview['share_id'], 'model_type': preview['model_type']},
        )
        return {**preview, 'written': True, **written}


def create_share_import_router(
    database: Database, vault_dir: Path, prefix: str = '/api',
    fetcher: Callable[[str], dict[str, Any]] | None = None,
    read_only: bool = False,
) -> APIRouter:
    service = ShareImportService(database, vault_dir, fetcher=fetcher, read_only=read_only)
    router = APIRouter(prefix=prefix, tags=['share-import'])

    def run(reference: str, apply: bool) -> dict[str, Any]:
        try:
            return service.apply(reference) if apply else service.preview(reference)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except ShareImportError as error:
            message = str(error)
            status = 413 if '大小上限' in message else 502
            raise HTTPException(status, message) from error
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error

    @router.post('/imports/share/preview')
    def preview(payload: SharePreviewRequest):
        return run(payload.url, False)

    @router.post('/imports/share/apply')
    def apply(payload: ShareApplyRequest):
        if payload.confirm is not True:
            raise HTTPException(422, '请先预览，然后明确确认导入为候选记忆')
        result = run(payload.url, True)
        if result.get('written'):
            database.audit('share_imported', {
                'share_id': result['share_id'],
                'document_id': result['document_id'],
                'candidates': result['memory_count'],
            })
        return result

    return router
