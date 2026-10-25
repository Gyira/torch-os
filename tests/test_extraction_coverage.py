import hashlib
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import SCHEMA, Database, utc_now
from backend.app.vault import _memory_from_document, create_note, parse_text, scan_vault
from backend.app.wolai_import import _ImportTransaction, import_selected_notes


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / 'index.sqlite3')
    database.initialize()
    return database


def extract(body: str):
    return _memory_from_document({}, body, Path('synthetic-note.md'))


def test_long_paragraph_is_chunked_without_losing_its_tail():
    body = '甲' * 2000 + '乙' * 2000 + '末尾证据'
    candidates = extract(body)
    # Identical 400-character chunks collapse into one candidate and are reported
    # as duplicates instead of flooding the review list.
    assert [len(item['content']) for item in candidates] == [400, 400, 4]
    assert candidates.coverage_summary['candidate_count'] == 3
    assert candidates.coverage_summary['total_candidate_segments'] == 11
    assert candidates.coverage_summary['duplicated_segments'] == 8
    assert candidates.coverage_summary['extractable_segments'] == 3
    assert candidates.coverage_summary['coverage_complete'] is True
    assert candidates.coverage_summary['truncated'] is False
    assert any('重复' in warning for warning in candidates.coverage_summary['warnings'])


def test_candidate_limit_is_explicit_and_complete_source_is_preserved(tmp_path):
    body = '\n\n'.join(f'第 {number} 段明确事实' for number in range(1, 46))
    database = make_database(tmp_path)
    result = create_note(tmp_path / 'vault', database, '长文', body)
    document = result['document']
    coverage = document['coverage_summary']
    assert document['content'] == body
    assert len(result['memories']) == 20
    assert coverage['candidate_count'] == 20
    assert coverage['total_candidate_segments'] == 45
    assert coverage['extractable_segments'] == 45
    assert coverage['omitted_segments'] == 25
    assert coverage['truncated'] is True
    assert coverage['coverage_complete'] is False
    assert any('25 个未提取' in warning for warning in coverage['warnings'])
    assert database.list_documents()[0]['coverage_summary'] == coverage


def test_long_paragraph_also_reports_segments_beyond_the_limit():
    candidates = extract('甲' * (2000 * 40) + '最后一段不能静默丢失')
    assert len(candidates) == 2
    assert candidates.coverage_summary['total_candidate_segments'] == 201
    assert candidates.coverage_summary['duplicated_segments'] == 199
    assert candidates.coverage_summary['omitted_segments'] == 0
    assert candidates.coverage_summary['coverage_complete'] is True


@pytest.mark.parametrize('fence', ['```', '~~~~'])
def test_fenced_code_is_not_extracted_and_following_prose_survives(fence):
    body = f'开头正文\n{fence}python\n代码内容\n\n# 代码注释\n{fence}\n结尾正文'
    candidates = extract(body)
    assert [item['content'] for item in candidates] == ['开头正文', '结尾正文']
    assert candidates[1]['source_start_line'] == 7
    assert candidates.coverage_summary['excluded_code_blocks'] == 1
    assert candidates.coverage_summary['excluded_code_lines'] == 5
    assert candidates.coverage_summary['excluded_heading_lines'] == 0
    assert candidates.coverage_summary['warnings']


def test_fence_type_and_length_must_match_before_prose_resumes():
    body = '````text\n内层代码\n```\n~~~\n仍是代码\n````\n真正正文'
    assert [item['content'] for item in extract(body)] == ['真正正文']


def test_unclosed_fence_does_not_leak_code_into_candidates():
    candidates = extract('第一句正文。\n```python\n未闭合代码\n\n仍是代码')
    assert [item['content'] for item in candidates] == ['第一句正文。']
    assert candidates.coverage_summary['excluded_code_blocks'] == 1


def test_heading_without_blank_line_does_not_swallow_body():
    candidates = extract('# 个人标题\n第一段正文\n## 次级标题\n第二段正文\n#hashtag 是正文')
    assert [item['content'] for item in candidates] == ['第一段正文', '第二段正文', '#hashtag 是正文']
    assert candidates.coverage_summary['excluded_heading_lines'] == 2
    assert candidates[0]['source_start_line'] == 2
    assert candidates[1]['source_start_line'] == 4
    assert candidates[2]['source_start_line'] == 5


def test_quotes_and_offsets_are_exact_for_multiline_and_repeated_text():
    body = '# 标题\n  相同正文\n第二行  \n\n相同正文\n'
    candidates = extract(body)
    assert [item['source_quote'] for item in candidates] == ['相同正文', '第二行']
    assert [(item['source_start_line'], item['source_end_line']) for item in candidates] == [(2, 2), (3, 3)]
    assert candidates.coverage_summary['duplicated_segments'] == 1
    for item in candidates:
        assert body[item['source_start_offset']:item['source_end_offset']] == item['source_quote']
        assert item['source_location_basis'] == 'document_body'
    assert candidates[0]['source_start_offset'] != candidates[1]['source_start_offset']


def test_frontmatter_locations_are_explicitly_relative_to_normalized_body(tmp_path):
    raw = '\ufeff---\r\ntitle: 定位测试\r\n---\r\n\r\n# 标题\r\n正文第一行\r\n正文第二行\r\n'
    metadata, body = parse_text(raw)
    assert body == '# 标题\n正文第一行\n正文第二行'
    candidates = _memory_from_document(metadata, body, Path('note.md'))
    database = make_database(tmp_path)
    document_id, _ = database.upsert_document('note.md', '定位测试', body, 'synthetic-sha', metadata)
    database.replace_document_memories(document_id, candidates)
    memories = database.list_memories()
    assert [(item['source_start_line'], item['source_end_line']) for item in memories] == [(2, 2), (3, 3)]
    for memory in memories:
        assert memory['source_location_basis'] == 'document_body'
        assert body[memory['source_start_offset']:memory['source_end_offset']] == memory['source_quote']
    assert '移除 YAML' in database.get_document(document_id)['coverage_summary']['location_note']


def test_candidates_saved_in_the_same_clock_tick_keep_a_stable_order(tmp_path):
    # A batch saved together shares one timestamp; without an id tiebreaker the
    # list order was decided by SQLite and could flip between runs.
    body = '第一条并列候选。\n第二条并列候选。'
    candidates = _memory_from_document({}, body, Path('tie.md'))
    database = make_database(tmp_path)
    document_id, _ = database.upsert_document('tie.md', '并列时间', body, 'synthetic-tie', {})
    database.replace_document_memories(document_id, candidates)
    with database.connect() as connection:
        connection.execute('UPDATE memories SET updated_at=?', (utc_now(),))
    memories = database.list_memories()
    assert [item['source_start_line'] for item in memories] == [1, 2]
    assert [item['source_start_line'] for item in database.list_memories(status='candidate')] == [1, 2]


def test_unchanged_scan_preserves_confirmed_correction_rejection_and_locations(tmp_path):
    database = make_database(tmp_path)
    vault = tmp_path / 'vault'
    result = create_note(vault, database, '决定', '第一项资料\n\n第二项资料')
    first, second = sorted(result['memories'], key=lambda item: item['id'])
    database.edit_memory(first['id'], '用户修订后的第一项资料')
    database.update_memory_status(first['id'], 'active')
    database.update_memory_status(second['id'], 'rejected')
    before = database.list_memories()
    coverage = database.get_document(result['document']['id'])['coverage_summary']
    database.initialize()
    assert scan_vault(vault, database)['changed'] == 0
    assert database.list_memories() == before
    assert database.get_document(result['document']['id'])['coverage_summary'] == coverage


def test_old_database_migration_and_rescan_preserve_user_decisions(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    raw = '第一段资料\n\n第二段资料'
    (vault / 'legacy.md').write_text(raw, encoding='utf-8')
    database = Database(tmp_path / 'old.sqlite3')
    with sqlite3.connect(database.path) as connection:
        connection.executescript(SCHEMA)
        connection.execute('INSERT INTO documents(path,title,content,sha256,indexed_at) VALUES (?,?,?,?,?)',
                           ('legacy.md', '旧资料', raw, hashlib.sha256((vault / 'legacy.md').read_bytes()).hexdigest(), utc_now()))
        for content, status in [('已手动修订并确认', 'active'), ('第二段资料', 'rejected')]:
            connection.execute('INSERT INTO memories(document_id,kind,title,content,status,source_quote,created_at,updated_at) VALUES (1,?,?,?,?,?,?,?)',
                               ('note', '旧资料', content, status, '原始引用', utc_now(), utc_now()))
    database.initialize()
    before = database.list_memories()
    assert scan_vault(vault, database)['changed'] == 0
    assert database.list_memories() == before
    assert {item['status'] for item in before} == {'active', 'rejected'}
    assert all(item['source_start_line'] is None for item in before)
    coverage = database.get_document(1)['coverage_summary']
    assert coverage['extraction_version'] == 'legacy'
    assert coverage['coverage_complete'] is None
    assert coverage['total_candidate_segments'] is None
    assert coverage['warnings']


def test_transactional_wolai_import_preserves_coverage_and_exact_quotes(tmp_path):
    member = '资料/说明.md'
    body = '# 说明\n' + '甲' * 4001 + '\n\n结尾资料'
    archive = tmp_path / 'synthetic.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(member, body)
    database = make_database(tmp_path)
    report = import_selected_notes(archive, [member], tmp_path / 'vault', database)
    document = database.get_document(report['notes'][0]['document_id'])
    coverage = document['coverage_summary']
    assert coverage['candidate_count'] == 2  # one readable chunk + the final sentence
    assert coverage['duplicated_segments'] == 9
    assert coverage['not_memory_like_segments'] == 1
    assert document['coverage_summary']['coverage_complete'] is True
    for item in database.list_memories():
        assert document['content'][item['source_start_offset']:item['source_end_offset']] == item['source_quote']
        assert item['confidence'] == 'inferred'
        assert item['status'] == 'candidate'
    before = database.list_memories()
    import_selected_notes(archive, [member], tmp_path / 'vault', database)
    assert database.list_memories() == before


def test_import_transaction_rolls_back_coverage_and_candidates_together(tmp_path):
    database = make_database(tmp_path)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            transaction = _ImportTransaction(database, connection)
            document_id, _ = transaction.upsert_document('note.md', '资料', '正文', 'hash', {})
            transaction.replace_document_memories(document_id, extract('第一句正文。'))
            assert transaction.get_document(document_id)['coverage_summary']['candidate_count'] == 1
            raise RuntimeError('synthetic failure')
    assert database.list_documents() == []
    assert database.list_memories() == []


def test_document_api_exposes_coverage_without_new_endpoint(tmp_path):
    database = make_database(tmp_path)
    app = create_app(database, tmp_path / 'vault', security_environ={})
    with TestClient(app) as client:
        client.get('/')
        result = client.post('/api/notes', json={'title': 'API 验证', 'content': '# 标题\n第一句正文。'}).json()
        document_id = result['document']['id']
        detail = client.get(f'/api/documents/{document_id}').json()
        listing = client.get('/api/documents').json()
        assert detail['coverage_summary'] == listing[0]['coverage_summary']
        assert detail['coverage_summary']['candidate_count'] == 1
        assert result['memories'][0]['source_start_line'] == 2
