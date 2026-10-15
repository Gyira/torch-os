"""Turn the user's own chat statements into reviewable candidate memories.

Local only: the sentences come from the user's messages already stored in this
database, the snapshot becomes a normal Markdown note in the vault (so it is
visible and editable in Obsidian), and the existing sentence extraction makes
the candidates. Nothing is confirmed automatically, nothing is sent to a model,
and the assistant's own words are never treated as facts about the user.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .db import Database, utc_now
from .note_pipeline import write_candidate_note
from .vault_connection import VaultReadOnlyError, ensure_vault_writable
from .vault import _memory_from_document

SHANGHAI = ZoneInfo('Asia/Shanghai')
SOURCE_KIND = 'conversation-extract'
MAX_SENTENCES = 20
MIN_CHARS = 6
CHATTER = {
    '你好', '您好', '在吗', '在么', '谢谢', '谢谢你', '好的', '好', '嗯', '嗯嗯',
    '哈哈', '收到', '没事', '晚安', '早安', '辛苦了',
}
SKIPPED_KEYS = ('question', 'chatter', 'short', 'duplicate', 'over_limit')


def _source_key(conversation_id: int) -> str:
    return f'conversation-extract-{conversation_id}'


def collect_sentences(messages: list[dict[str, Any]], include_assistant: bool = False) -> dict[str, Any]:
    """Keep statements; questions, chatter and duplicates are counted.

    This app's own conversations keep only the user's words. dsh session
    harvesting passes `include_assistant=True` (the user asked for both sides);
    nothing becomes a fact until the candidate is confirmed.
    """
    kept: list[dict[str, Any]] = []
    skipped = {key: 0 for key in SKIPPED_KEYS}
    seen: set[str] = set()
    for message in messages:
        role = message.get('role')
        if role != 'user' and not (include_assistant and role == 'assistant'):
            continue
        content = str(message.get('content') or '').strip()
        if not content:
            continue
        for candidate in _memory_from_document({}, content, Path('conversation.md')):
            text = str(candidate.get('content') or '').strip()
            if not text:
                continue
            if text.rstrip().endswith(('？', '?')):
                skipped['question'] += 1
                continue
            if text.rstrip('。！!') in CHATTER:
                skipped['chatter'] += 1
                continue
            if len(text) < MIN_CHARS:
                skipped['short'] += 1
                continue
            key = text.casefold()
            if key in seen:
                skipped['duplicate'] += 1
                continue
            seen.add(key)
            if len(kept) >= MAX_SENTENCES:
                skipped['over_limit'] += 1
                continue
            kept.append({'text': text, 'message_id': message.get('id')})
    return {'sentences': kept, 'skipped': skipped}


class ConversationExtractRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: StrictBool = True
    confirm: StrictBool = False


class DshSessionTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')

    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=20_000)


class DshSessionIngestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str = Field(min_length=1, max_length=200)
    turns: list[DshSessionTurn] = Field(min_length=1, max_length=500)


class ConversationMemoryService:
    """Preview and write one snapshot note per conversation, in place when re-run."""

    def __init__(self, database: Database, vault_dir: Path, read_only: bool = False):
        self.database = database
        self.vault_dir = Path(vault_dir)
        self.read_only = bool(read_only)

    def _relative_path(self, conversation_id: int) -> str:
        return f'90-Inbox/conversation-extract-{conversation_id}.md'

    def preview(self, conversation_id: int) -> dict[str, Any]:
        stored = self.database.get_conversation(conversation_id)
        if stored is None:
            raise LookupError('对话不存在')
        collected = collect_sentences(stored['messages'])
        existing = self.database.find_document_by_source_key(_source_key(conversation_id))
        user_messages = [message for message in stored['messages'] if message.get('role') == 'user']
        return {
            'conversation_id': conversation_id,
            'title': stored['conversation'].get('title'),
            'message_count': len(user_messages),
            'sentences': collected['sentences'],
            'kept': len(collected['sentences']),
            'skipped': collected['skipped'],
            'source_kind': SOURCE_KIND,
            'note_path': self._relative_path(conversation_id),
            'existing_document_id': existing['id'] if existing else None,
            'notice': '只提炼你说的话，逐条仍要在“记忆”页确认后才参与回答；不会自动确认，也不会发送给模型。',
        }

    def apply(self, conversation_id: int) -> dict[str, Any]:
        preview = self.preview(conversation_id)
        if preview['kept'] == 0:
            return {**preview, 'written': False, 'reason': 'empty', 'memory_count': 0, 'candidates': []}
        ensure_vault_writable(self.read_only, '把对话提炼成候选记忆')
        stored = self.database.get_conversation(conversation_id) or {}
        created_at = str(stored.get('conversation', {}).get('created_at') or '')
        title = f"对话提炼 {created_at[:10] or datetime.now(SHANGHAI).strftime('%Y-%m-%d')} #{conversation_id}"
        written = write_candidate_note(
            self.database, self.vault_dir,
            source_key=_source_key(conversation_id),
            relative_path=self._relative_path(conversation_id),
            title=title,
            source_kind=SOURCE_KIND,
            created_at=created_at or utc_now(),
            sentences=preview['sentences'],
            extra_metadata={'conversation_id': conversation_id},
        )
        return {
            **preview,
            'written': True,
            **written,
        }

    def ingest_dsh_session(self, payload: DshSessionIngestRequest) -> dict[str, Any]:
        """Write one reviewable snapshot note for a dsh session (candidates only)."""
        messages = [{'role': turn.role, 'content': turn.content} for turn in payload.turns]
        collected = collect_sentences(messages, include_assistant=True)
        safe = re.sub(r'[^A-Za-z0-9._-]+', '-', payload.session_id).strip('-')[:80] or 'session'
        source_key = f'dsh-session-{safe}'
        relative_path = f'90-Inbox/dsh-session-{safe}.md'
        if len(collected['sentences']) == 0:
            return {'written': False, 'reason': 'empty', 'kept': 0,
                    'skipped': collected['skipped'], 'note_path': relative_path,
                    'memory_count': 0, 'candidates': []}
        ensure_vault_writable(self.read_only, '把 dsh 会话收进候选记忆')
        written = write_candidate_note(
            self.database, self.vault_dir,
            source_key=source_key,
            relative_path=relative_path,
            title=f"dsh 会话提炼 {datetime.now(SHANGHAI).strftime('%Y-%m-%d')} #{safe}",
            source_kind='dsh-session-extract',
            created_at=utc_now(),
            sentences=collected['sentences'],
            extra_metadata={'dsh_session_id': payload.session_id},
        )
        return {'written': True, 'kept': len(collected['sentences']),
                'skipped': collected['skipped'], 'note_path': relative_path, **written}


def create_conversation_memory_router(
    database: Database, vault_dir: Path, prefix: str = '/api', read_only: bool = False,
) -> APIRouter:
    service = ConversationMemoryService(database, vault_dir, read_only=read_only)
    router = APIRouter(prefix=prefix, tags=['conversation-memory'])

    @router.post('/conversations/{conversation_id}/extract-candidates')
    def extract_candidates(conversation_id: int, payload: ConversationExtractRequest):
        if not payload.dry_run and payload.confirm is not True:
            raise HTTPException(422, '请先预览，然后明确确认把提炼结果写成候选记忆')
        try:
            result = service.preview(conversation_id) if payload.dry_run else service.apply(conversation_id)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        if not payload.dry_run and result.get('written'):
            database.audit('conversation_extracted', {
                'conversation_id': conversation_id,
                'document_id': result['document_id'],
                'candidates': result['memory_count'],
            })
        return result

    @router.post('/dsh-sessions/ingest')
    def ingest_dsh_session(payload: DshSessionIngestRequest):
        try:
            result = service.ingest_dsh_session(payload)
        except VaultReadOnlyError as error:
            raise HTTPException(409, str(error)) from error
        if result.get('written'):
            database.audit('dsh_session_ingested', {
                'session_id': payload.session_id,
                'document_id': result['document_id'],
                'candidates': result['memory_count'],
            })
        return result

    return router
