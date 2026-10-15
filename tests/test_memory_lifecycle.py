from pathlib import Path
import pytest
from backend.app.db import Database
from backend.app.vault import parse_markdown, scan_vault


def setup_vault(tmp_path):
    vault=tmp_path/'vault'
    vault.mkdir()
    db=Database(tmp_path/'test.sqlite3')
    db.initialize()
    return vault,db


def write_note(path, content='我的目标是学习法语。', note_id='one', extra=''):
    path.write_text(f'---\nid: {note_id}\ntype: goal\ntitle: 学习目标\n{extra}---\n\n# 学习\n\n{content}',encoding='utf-8')


def test_confirmed_memory_is_preserved_on_unchanged_scan_and_rename(tmp_path):
    vault,db=setup_vault(tmp_path)
    path=vault/'one.md'
    write_note(path)
    scan_vault(vault,db)
    m=db.list_memories('candidate')[0]
    db.update_memory_status(m['id'],'active')
    scan_vault(vault,db)
    assert db.search_memories('我的目标')[0]['id']==m['id']
    path.rename(vault/'renamed.md')
    scan_vault(vault,db)
    result=db.search_memories('我的目标')
    assert result[0]['id']==m['id'] and result[0]['path']=='renamed.md'
    assert len(db.list_documents())==1


def test_changed_source_archives_old_confirmation_and_keeps_history(tmp_path):
    vault,db=setup_vault(tmp_path)
    path=vault/'one.md'
    write_note(path)
    scan_vault(vault,db)
    mid=db.list_memories()[0]['id']
    db.update_memory_status(mid,'active')
    write_note(path,'我的目标是学习西班牙语。')
    scan_vault(vault,db)
    assert db.search_memories('我的目标')==[]
    assert db.list_memories('archived')[0]['id']==mid
    assert any(e['event_type']=='source_changed' for e in db.memory_history(mid))
    with pytest.raises(ValueError):
        db.update_memory_status(mid,'active')
    new=db.list_memories('candidate')[0]
    db.update_memory_status(new['id'],'active')
    assert '西班牙语' in db.search_memories('我的目标')[0]['content']


def test_invalid_yaml_and_deleted_source_invalidate_previous_memory(tmp_path):
    vault,db=setup_vault(tmp_path)
    path=vault/'one.md'
    write_note(path)
    scan_vault(vault,db)
    db.update_memory_status(db.list_memories()[0]['id'],'active')
    path.write_text('---\nid: [unclosed\n---\n正文',encoding='utf-8')
    result=scan_vault(vault,db)
    assert result['errors'] and result['invalidated']==1
    assert db.search_memories('我的目标')==[]


def test_private_relationship_and_system_notes_are_not_indexed(tmp_path):
    vault,db=setup_vault(tmp_path)
    write_note(vault/'sensitive.md',extra='scope: relationship\n')
    (vault/'relationships').mkdir()
    write_note(vault/'relationships'/'private.md',note_id='rel')
    (vault/'00-System').mkdir()
    write_note(vault/'00-System'/'constitution.md',note_id='sys')
    result=scan_vault(vault,db)
    assert db.list_documents()==[] and db.list_memories()==[]
    assert result['excluded']==1


def test_duplicate_ids_fail_closed(tmp_path):
    vault,db=setup_vault(tmp_path)
    write_note(vault/'one.md')
    write_note(vault/'two.md')
    result=scan_vault(vault,db)
    assert len(result['errors'])==2
    assert not db.list_documents()


def test_yaml_dates_and_bom_are_supported(tmp_path):
    vault,db=setup_vault(tmp_path)
    path=vault/'date.md'
    path.write_text('\ufeff---\nid: date\ncreated_at: 2026-10-03\n---\n\n今天的记录。',encoding='utf-8')
    assert scan_vault(vault,db)['changed']==1
    assert db.list_documents()[0]['metadata_json']


def test_unknown_query_does_not_match_common_single_characters(tmp_path):
    vault,db=setup_vault(tmp_path)
    write_note(vault/'one.md')
    scan_vault(vault,db)
    db.update_memory_status(db.list_memories()[0]['id'],'active')
    assert db.search_memories('我的生日是什么时候？')==[]
    assert db.search_documents('" : NOT * ( )')==[]
