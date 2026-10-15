from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from backend.app.db import Database, _explicitly_expired
from backend.app.vault import scan_vault


def setup_vault(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    database = Database(tmp_path / 'test.sqlite3')
    database.initialize()
    return vault, database


def write_note(path: Path, source_id: str | None, content='我的目标是学习法语。', **metadata):
    values = {'type': 'goal', 'title': '学习目标', **metadata}
    if source_id is not None:
        values['id'] = source_id
    path.write_text('---\n' + yaml.safe_dump(values, allow_unicode=True) + '---\n\n' + content, encoding='utf-8')


def confirm_all(database):
    result = {}
    for memory in database.list_memories('candidate'):
        result[memory['source_path']] = database.update_memory_status(memory['id'], 'active')
    return result


def test_move_and_reuse_old_path_preserve_confirmation_and_correction(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    scan_vault(vault, database)
    original = database.list_memories('candidate')[0]
    database.edit_memory(original['id'], '我的目标是学习法语和德语。')
    database.update_memory_status(original['id'], 'active')
    history = database.memory_history(original['id'])
    (vault / 'a.md').rename(vault / 'z.md')
    write_note(vault / 'a.md', 'source-C', '我的目标是坚持骑行。')

    result = scan_vault(vault, database)

    retained = database.list_memories('active')
    assert len(retained) == 1
    assert retained[0]['id'] == original['id']
    assert retained[0]['document_id'] == original['document_id']
    assert retained[0]['source_path'] == 'z.md'
    assert retained[0]['content'] == '我的目标是学习法语和德语。'
    assert database.memory_history(original['id']) == history
    assert result['changed'] == 1 and result['skipped'] == 1
    fresh = database.list_memories('candidate')[0]
    assert fresh['document_id'] != original['document_id']
    assert fresh['source_path'] == 'a.md'


def test_two_notes_can_exchange_paths_without_changing_identity_or_history(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A', '我的目标是学习法语。')
    write_note(vault / 'b.md', 'source-B', '我的目标是坚持骑行。')
    scan_vault(vault, database)
    original = confirm_all(database)
    histories = {item['id']: database.memory_history(item['id']) for item in original.values()}
    first, second = (vault / 'a.md').read_bytes(), (vault / 'b.md').read_bytes()
    (vault / 'a.md').write_bytes(second)
    (vault / 'b.md').write_bytes(first)

    result = scan_vault(vault, database)

    current = {item['id']: item for item in database.list_memories('active')}
    assert result['changed'] == 0 and result['skipped'] == 2
    assert current[original['a.md']['id']]['source_path'] == 'b.md'
    assert current[original['b.md']['id']]['source_path'] == 'a.md'
    for item in original.values():
        assert current[item['id']]['document_id'] == item['document_id']
        assert database.memory_history(item['id']) == histories[item['id']]
    assert all(not item['path'].startswith('.source-history/') for item in database.list_documents())


def test_three_way_rename_cycle_preserves_every_confirmed_memory(tmp_path):
    vault, database = setup_vault(tmp_path)
    for name in ('a', 'b', 'c'):
        write_note(vault / f'{name}.md', name, f'我的目标是学习项目{name}。')
    scan_vault(vault, database)
    original = confirm_all(database)
    raw = {name: (vault / f'{name}.md').read_bytes() for name in ('a', 'b', 'c')}
    for source, target in [('a', 'b'), ('b', 'c'), ('c', 'a')]:
        (vault / f'{target}.md').write_bytes(raw[source])
    scan_vault(vault, database)
    current = {item['id']: item for item in database.list_memories('active')}
    for source, target in [('a', 'b'), ('b', 'c'), ('c', 'a')]:
        assert current[original[f'{source}.md']['id']]['source_path'] == f'{target}.md'


def test_new_stable_id_at_same_path_does_not_reassign_old_document(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    scan_vault(vault, database)
    original = confirm_all(database)['a.md']
    write_note(vault / 'a.md', 'source-C', '我的目标是坚持骑行。')
    scan_vault(vault, database)
    fresh = database.list_memories('candidate')[0]
    archived = database.list_memories('archived')[0]
    assert fresh['document_id'] != original['document_id']
    assert archived['id'] == original['id']
    assert archived['source_path'] == 'a.md'
    assert database.get_document(original['document_id']) is None
    assert database.search_memories('我的目标') == []
    with pytest.raises(ValueError):
        database.update_memory_status(original['id'], 'active')


def test_move_to_previously_deleted_path_does_not_steal_old_identity(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    write_note(vault / 'b.md', 'source-B', '我的目标是坚持骑行。')
    scan_vault(vault, database)
    original = confirm_all(database)
    (vault / 'b.md').unlink()
    scan_vault(vault, database)
    (vault / 'a.md').rename(vault / 'b.md')
    scan_vault(vault, database)
    current = database.list_memories('active')
    assert len(current) == 1
    assert current[0]['id'] == original['a.md']['id']
    assert current[0]['document_id'] == original['a.md']['document_id']
    assert current[0]['source_path'] == 'b.md'
    assert database.list_memories('archived')[0]['id'] == original['b.md']['id']


def test_unchanged_rejected_memory_stays_rejected_after_move(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    scan_vault(vault, database)
    memory = database.list_memories('candidate')[0]
    database.update_memory_status(memory['id'], 'rejected')
    history = database.memory_history(memory['id'])
    (vault / 'a.md').rename(vault / 'z.md')
    write_note(vault / 'a.md', 'source-C')
    scan_vault(vault, database)
    rejected = database.list_memories('rejected')[0]
    assert rejected['id'] == memory['id'] and rejected['source_path'] == 'z.md'
    assert database.memory_history(memory['id']) == history
    assert database.search_memories('我的目标') == []


def test_duplicate_live_ids_invalidate_previously_confirmed_source(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    scan_vault(vault, database)
    memory = confirm_all(database)['a.md']
    (vault / 'b.md').write_bytes((vault / 'a.md').read_bytes())
    result = scan_vault(vault, database)
    assert len(result['errors']) == 2
    assert not database.list_documents()
    assert not database.search_memories('我的目标')
    assert database.list_memories('archived')[0]['id'] == memory['id']


def test_path_only_notes_keep_path_fallback_without_claiming_a_moved_id(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'plain.md', None)
    write_note(vault / 'a.md', 'source-A')
    scan_vault(vault, database)
    original = confirm_all(database)
    (vault / 'a.md').rename(vault / 'z.md')
    write_note(vault / 'a.md', None, '我的目标是坚持骑行。')
    scan_vault(vault, database)
    active = {item['source_path']: item for item in database.list_memories('active')}
    assert active['plain.md']['id'] == original['plain.md']['id']
    assert active['z.md']['id'] == original['a.md']['id']
    assert database.list_memories('candidate')[0]['document_id'] != original['a.md']['document_id']


def test_path_only_rename_requires_fresh_confirmation(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', None)
    scan_vault(vault, database)
    original = confirm_all(database)['a.md']
    (vault / 'a.md').rename(vault / 'b.md')
    scan_vault(vault, database)
    assert not database.list_memories('active')
    assert database.list_memories('archived')[0]['id'] == original['id']
    assert database.list_memories('candidate')[0]['document_id'] != original['document_id']


def test_deleted_identity_restored_later_requires_fresh_confirmation(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    raw = (vault / 'a.md').read_bytes()
    scan_vault(vault, database)
    original = confirm_all(database)['a.md']
    write_note(vault / 'a.md', 'source-C')
    scan_vault(vault, database)
    (vault / 'z.md').write_bytes(raw)
    scan_vault(vault, database)
    candidates = [item for item in database.list_memories('candidate') if item['source_path'] == 'z.md']
    assert candidates[0]['document_id'] == original['document_id']
    assert candidates[0]['id'] != original['id']
    assert not database.list_memories('active')


def test_ambiguous_historical_ids_fail_closed(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'a.md', 'source-A')
    write_note(vault / 'b.md', 'source-B')
    scan_vault(vault, database)
    confirm_all(database)
    with database.connect() as connection:
        connection.execute("UPDATE documents SET source_key='source-A' WHERE path='b.md'")
    (vault / 'b.md').unlink()
    result = scan_vault(vault, database)
    assert len(result['errors']) == 1
    assert not database.list_documents()
    assert not database.search_memories('我的目标')


def test_direct_upsert_uses_stable_id_before_occupied_path(tmp_path):
    _, database = setup_vault(tmp_path)
    first, _ = database.upsert_document('a.md', 'A', 'A', 'hash-a', {'id': 'A'})
    second, _ = database.upsert_document('b.md', 'B', 'B', 'hash-b', {'id': 'B'})
    result, changed = database.upsert_document('b.md', 'A', 'A', 'hash-a', {'id': 'A'})
    assert result == first and changed is False
    assert database.get_document(first)['path'] == 'b.md'
    assert database.get_document(second) is None


def test_source_dates_are_retained_and_old_experiences_do_not_expire(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'history.md', 'history', '我曾经学习法语。', type='experience',
               event_date='2001-01-02', created_at='2020-02-03T04:05:06+08:00',
               valid_from='2000-01-01')
    scan_vault(vault, database)
    confirm_all(database)
    result = database.search_memories('学习法语')[0]
    assert result['source_date'] == '2001-01-02'
    assert result['valid_from'] == '2000-01-01'
    assert result['valid_until'] is None
    assert 'source_metadata_json' not in result


def test_source_date_falls_back_to_created_at(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'history.md', 'history', created_at='2001-01-02')
    scan_vault(vault, database)
    confirm_all(database)
    assert database.search_memories('我的目标')[0]['source_date'] == '2001-01-02'


def test_only_explicit_expired_validity_bounds_are_filtered(tmp_path):
    vault, database = setup_vault(tmp_path)
    write_note(vault / 'expired.md', 'expired', valid_until='2000-01-01')
    write_note(vault / 'current.md', 'current', event_date='2000-01-01', valid_until='2999-01-01')
    write_note(vault / 'invalid.md', 'invalid', valid_until='after graduation')
    scan_vault(vault, database)
    confirm_all(database)
    result = database.search_memories('我的目标')
    assert {item['source_path'] for item in result} == {'current.md', 'invalid.md'}
    assert next(item for item in result if item['source_path'] == 'invalid.md')['valid_until'] == 'after graduation'
    # Retrieval expiry does not destroy the confirmed record or its history.
    assert len(database.list_memories('active')) == 3


@pytest.mark.parametrize(
    ('value', 'expired'),
    [('2026-10-02', True), ('2026-10-03', False), ('2026-10-04', False),
     ('2026-10-03T11:59:59+08:00', True), ('2026-10-03T12:00:00+08:00', True),
     ('2026-10-03T12:00:01+08:00', False), ('2026-10-03T12:00:00', True),
     ('2026-10-03T04:00:00Z', True), ('2026-02-30', False), (None, False)],
)
def test_valid_until_uses_shanghai_days_and_explicit_instant(value, expired):
    now = datetime(2026, 10, 3, 12, tzinfo=timezone(timedelta(hours=8)))
    assert _explicitly_expired(value, now) is expired
