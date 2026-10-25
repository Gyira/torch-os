"""Candidate tidying tests use temporary data; raw quotes stay untouched."""
import json

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.vault import _is_memory_like, clean_fragment


def make_client(tmp_path):
    database = Database(tmp_path / 'app.sqlite3')
    app = create_app(database, tmp_path / 'vault', security_environ={})
    client = TestClient(app)
    client.get('/')
    return client, app, database


def test_clean_fragment_makes_readable_statements():
    assert clean_fragment('# 标题词') == '标题词'
    assert clean_fragment('- [ ] 明天去买菜') == '明天去买菜'
    assert clean_fragment('> 引用内容') == '引用内容'
    assert clean_fragment('看 [这个链接](https://example.com/a) 的内容') == '看 这个链接 的内容'
    assert clean_fragment('| a | b |') == 'a b'
    assert clean_fragment('| --- | --- |') == ''
    assert clean_fragment('**加粗**和`代码`') == '加粗和代码'
    assert clean_fragment('<div>标签</div>正文') == '标签正文'
    assert _is_memory_like('这是人话。')
    assert not _is_memory_like('ab')
    assert not _is_memory_like('https://example.com/page')


def test_candidate_tidy_preview_then_apply(tmp_path):
    client, app, database = make_client(tmp_path)
    created = client.post('/api/notes', json={
        'title': '杂记', 'type': 'note',
        'content': '第一句正文。\n\n第二句正文。',
    }).json()
    first_id = created['memories'][0]['id']
    second_id = created['memories'][1]['id']
    # Simulate candidates produced by the older paragraph extractor.
    with database.connect() as connection:
        connection.execute(
            "UPDATE memories SET title=?, content=?, source_quote=? WHERE id=?",
            ('杂记', '# 标题词\n- [ ] 我计划明天去图书馆借书。', '# 标题词\n- [ ] 我计划明天去图书馆借书。', first_id),
        )
        connection.execute(
            "UPDATE memories SET title=?, content=?, source_quote=? WHERE id=?",
            ('杂记', '---', '---', second_id),
        )
    before = {row['id']: row for row in client.get('/api/memories?status=candidate').json()}

    preview = client.post('/api/memories/tidy-candidates', json={'dry_run': True}).json()
    assert preview['dry_run'] is True
    assert preview['total'] == 2 and preview['updated'] == 1 and preview['not_memory_like'] == 1
    assert client.get('/api/memories?status=candidate').json() == list(before.values())

    assert client.post('/api/memories/tidy-candidates', json={'dry_run': False, 'confirm': False}).status_code == 422
    applied = client.post('/api/memories/tidy-candidates', json={'dry_run': False, 'confirm': True}).json()
    assert applied['updated'] == 1 and applied['not_memory_like'] == 1

    after = {row['id']: row for row in client.get('/api/memories?status=candidate').json()}
    assert after[first_id]['content'] == '我计划明天去图书馆借书。'
    assert after[first_id]['title'] == '我计划明天去图书馆借书。'
    assert after[first_id]['status'] == 'candidate'
    assert after[first_id]['revision'] == before[first_id]['revision'] + 1
    assert after[first_id]['source_quote'] == '# 标题词\n- [ ] 我计划明天去图书馆借书。'
    assert after[second_id] == before[second_id]

    with database.connect() as connection:
        events = [json.loads(row['after_json']) for row in connection.execute(
            "SELECT after_json FROM memory_events WHERE event_type='tidied'")]
        audits = [json.loads(row['detail_json']) for row in connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='candidates_tidied'")]
    assert any(item['content'] == '我计划明天去图书馆借书。' for item in events)
    assert audits == [{'updated': 1, 'unchanged': 0, 'not_memory_like': 1}]
    assert '我计划' not in json.dumps(audits, ensure_ascii=False)
