"""Write a generated Markdown note and let the normal extraction make candidates.

Shared by "conversation → candidates" (0.46.0) and "share link → candidates"
(0.47.0): both produce an ordinary vault note whose sentences become *candidate*
memories. Notes are rewritten in place per stable source key, so re-running an
import updates one note instead of stacking duplicates.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from .db import Database
from .vault import MAX_NOTE_BYTES, _memory_from_document, safe_note_path


def bullet_body(sentences: list[dict[str, Any]]) -> str:
    return '\n'.join(f'- {item["text"]}' for item in sentences)


def write_candidate_note(
    database: Database,
    vault_dir: Path,
    *,
    source_key: str,
    relative_path: str,
    title: str,
    source_kind: str,
    created_at: str,
    sentences: list[dict[str, Any]],
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = bullet_body(sentences)
    metadata = {
        'id': source_key,
        'type': 'note',
        'title': title,
        'created_at': created_at,
        'source_kind': source_kind,
        'visibility': 'private',
        **(extra_metadata or {}),
    }
    raw = ('---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n\n' + body + '\n').encode('utf-8')
    if len(raw) > MAX_NOTE_BYTES:
        raise ValueError('提炼内容超出单篇大小限制')
    path = safe_note_path(vault_dir, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as handle:
        handle.write(raw)
    document_id, changed = database.upsert_document(
        relative_path, title, body, hashlib.sha256(raw).hexdigest(), metadata,
    )
    if changed:
        database.replace_document_memories(document_id, _memory_from_document(metadata, body, path))
    candidates = [item for item in database.list_memories('candidate') if item['document_id'] == document_id]
    return {
        'document_id': document_id,
        'changed': bool(changed),
        'memory_count': len(candidates),
        'candidates': candidates,
        'note_path': relative_path,
    }
