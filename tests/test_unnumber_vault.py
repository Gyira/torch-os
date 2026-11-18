import json
from pathlib import Path
import sqlite3

import pytest

from scripts.unnumber_vault import apply, plan, rollback
from backend.app.directory_names import canonical_path
from backend.app.pillar_notes import read_note


def write(root, relative, text=''):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_rename_links_files_dates_recycle_and_rollback(tmp_path):
    old = '00_人生路/01_日记/2026-11/01_笔记.md'
    original = '![[03_知识库/00_收件箱/image.png]]\n[附件](../../../03_知识库/00_收件箱/image.png)\n'
    write(tmp_path, old, original)
    image = tmp_path / '03_知识库/00_收件箱/image.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(bytes(range(256)))
    trash = '.torch-os/本子回收站/batch/notebooks/life/reflection/02_反思复盘/01_反思'
    write(tmp_path, trash + '/原文.md', 'unchanged')
    manifest_text = json.dumps({'original_paths':['00_人生路/02_反思复盘/01_反思']})
    write(tmp_path, '.torch-os/本子回收站/batch/manifest.json', manifest_text)
    write(tmp_path, '.obsidian/workspace.json', json.dumps({'file':old}))
    manifest = apply(tmp_path)
    assert not (tmp_path / '00_人生路').exists()
    current = tmp_path / '人生路/日记/2026-11/01_笔记.md'
    assert current.is_file() and '知识库/收件箱/image.png' in current.read_text(encoding='utf-8')
    assert (tmp_path / '知识库/收件箱/image.png').read_bytes() == bytes(range(256))
    assert (tmp_path / trash.replace('02_反思复盘','反思复盘').replace('01_反思','反思') / '原文.md').is_file()
    assert json.loads((tmp_path / '.obsidian/workspace.json').read_text(encoding='utf-8'))['file'] == '人生路/日记/2026-11/01_笔记.md'
    assert read_note(tmp_path, old)['path'] == '人生路/日记/2026-11/01_笔记.md'
    assert apply(tmp_path) is None
    rollback(tmp_path, manifest)
    assert (tmp_path / old).read_text(encoding='utf-8') == original
    assert image.read_bytes() == bytes(range(256))


def test_duplicate_empty_directories_block_before_mutation(tmp_path):
    (tmp_path/'00_人生路/01_测试').mkdir(parents=True)
    (tmp_path/'00_人生路/02_测试').mkdir()
    with pytest.raises(ValueError, match='重名'): plan(tmp_path)
    assert (tmp_path/'00_人生路/01_测试').is_dir()


def test_existing_destination_is_never_merged(tmp_path):
    write(tmp_path, '00_人生路/01_日记/原文.md', 'a')
    write(tmp_path, '人生路/日记/原文.md', 'b')
    with pytest.raises(ValueError, match='同名'): apply(tmp_path)
    assert (tmp_path/'人生路/日记/原文.md').read_text() == 'b'


def test_sql_paths_preserve_document_identity_and_rollback(tmp_path):
    old = '00_人生路/01_日记/note.md'
    write(tmp_path, old, 'same')
    db = tmp_path/'.torch-os/torch_os.sqlite3'
    db.parent.mkdir()
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE documents (id TEXT, path TEXT UNIQUE, metadata_json TEXT)')
        c.execute('INSERT INTO documents VALUES (?,?,?)', ('stable-id', old, json.dumps({'source':old})))
        c.execute('CREATE TABLE dida_habit_links (uid TEXT, relpath TEXT)')
        c.execute('INSERT INTO dida_habit_links VALUES (?,?)', ('stable-habit', old))
    manifest = apply(tmp_path, db)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT id,path FROM documents').fetchone() == ('stable-id','人生路/日记/note.md')
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('SELECT uid,relpath FROM dida_habit_links').fetchone() == ('stable-habit','人生路/日记/note.md')
    rollback(tmp_path, manifest)
    with sqlite3.connect(db) as c: assert c.execute('SELECT path FROM documents').fetchone()[0] == old


def test_rollback_refuses_new_edits(tmp_path):
    write(tmp_path,'00_人生路/01_日记/note.md','before')
    manifest = apply(tmp_path)
    write(tmp_path,'人生路/日记/note.md','after-user-edit')
    with pytest.raises(ValueError, match='已编辑'): rollback(tmp_path,manifest)
    assert (tmp_path/'人生路/日记/note.md').read_text() == 'after-user-edit'


def test_canonical_alias_does_not_rename_numbered_note():
    assert canonical_path('00_人生路/01_日记/01_日记.md') == '人生路/日记/01_日记.md'


def test_numbered_note_titles_and_links_are_preserved(tmp_path):
    write(tmp_path, '00_人生路/01_日记/01_日记.md', 'original note')
    write(tmp_path, '00_人生路/01_日记/ref.md', '[[01_日记]]\n[[00_人生路/01_日记/01_日记]]\n[[01_日记/01_日记]]')
    apply(tmp_path)
    text = (tmp_path/'人生路/日记/ref.md').read_text(encoding='utf-8')
    assert '[[01_日记]]' in text
    assert text.count('[[人生路/日记/01_日记.md]]') == 2
