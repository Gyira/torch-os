"""Markdown task import uses synthetic files and temporary SQLite databases."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import pillar_notes, task_notes
from backend.app.db import Database
from backend.app.task_import import TaskImportApply, TaskImportService, create_task_import_router, parse_checkboxes
from backend.app.vault import MAX_NOTE_BYTES, parse_text, scan_vault


def write_source(vault: Path, body: str, name: str = "tasks.md", **metadata) -> Path:
    path = vault / name
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {"id": "synthetic-checklist", "type": "note", "title": "合成任务清单", **metadata}
    path.write_text("---\n" + yaml.safe_dump(values, allow_unicode=True) + "---\n\n" + body + "\n", encoding="utf-8")
    return path


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "tasks.sqlite3")
    vault = tmp_path / "vault"
    service = TaskImportService(database, vault)
    source = write_source(vault, "# 学习\n\n- [ ] 阅读第二章\n- [x] 写下读书笔记\n* [X] 核对引用\n1. [ ] 周六复习")
    scan_vault(vault, database)
    document_id = database.list_documents()[0]["id"]
    app = FastAPI()
    app.include_router(create_task_import_router(database, vault))
    return service, TestClient(app), source, document_id


def preview(client, document_id):
    response = client.get(f"/api/documents/{document_id}/tasks/preview")
    assert response.status_code == 200, response.text
    return response.json()


def request_body(source_preview, **overrides):
    return {"revision": source_preview["revision"], "sha256": source_preview["sha256"],
            "selected_keys": [item["key"] for item in source_preview["items"]],
            "goal_path": "", "confirm": True, **overrides}


def apply(client, document_id, source_preview, **overrides):
    return client.post(f"/api/documents/{document_id}/tasks/apply", json=request_body(source_preview, **overrides))


def notes(service):
    return task_notes.scan_task_notes(service.vault_dir)[0]


def old_task_counts(service):
    with service.database.connect() as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM task_import_ledger").fetchone()[0],
        )


def test_preview_is_read_only_preserves_lines_and_heading(setup):
    service, client, source, document_id = setup
    before = (old_task_counts(service), len(notes(service)))
    memories = service.database.list_memories()
    original = source.read_bytes()
    result = preview(client, document_id)
    assert set(result) == {"document_id", "revision", "sha256", "items", "warnings"}
    assert result["document_id"] == document_id and result["revision"] == 1
    assert result["sha256"] == hashlib.sha256(original).hexdigest()
    assert [item["line"] for item in result["items"]] == [3, 4, 5, 6]
    assert [item["checked"] for item in result["items"]] == [False, True, True, False]
    assert result["items"][0]["title"] == "阅读第二章"
    assert result["items"][0]["description"] == "- [ ] 阅读第二章"
    assert all(item["heading"] == "学习" for item in result["items"])
    assert len({item["key"] for item in result["items"]}) == 4
    assert any("第 1 行" in warning for warning in result["warnings"])
    assert (old_task_counts(service), len(notes(service))) == before
    assert source.read_bytes() == original and service.database.list_memories() == memories
    assert preview(client, document_id) == result


def test_parser_ignores_fences_and_html_comments_but_keeps_inert_html():
    body = "\n".join([
        "# 一级", "- [ ] 可见一", "```markdown", "- [ ] 代码伪待办", "<!-- 未闭合但仅为代码",
        "```", "<!--", "* [X] 注释伪待办", "```", "-->", "## 二级 ##",
        "1) [X] 可见二 <!-- 隐藏备注 -->", "  ~~~~", "2. [ ] 代码伪待办二", "~~~", "~~~~",
        '- [ ] <img src=x onerror="alert(1)"> 保留为纯数据', "# 新章节", "+ [ ] 可见三",
    ])
    items, warnings = parse_checkboxes(body)
    assert [item["line"] for item in items] == [2, 12, 17, 19]
    assert items[1]["heading"] == "一级 / 二级"
    assert items[1]["title"] == "可见二"
    assert items[1]["description"] == "1) [X] 可见二 <!-- 隐藏备注 -->"
    assert items[2]["title"] == '<img src=x onerror="alert(1)"> 保留为纯数据'
    assert items[3]["heading"] == "新章节"
    assert any("围栏代码块" in warning for warning in warnings)


@pytest.mark.parametrize("tail", ["```\n- [ ] 隐藏", "<!--\n- [ ] 隐藏"])
def test_unclosed_blocks_are_ignored_with_warning(tail):
    items, warnings = parse_checkboxes("- [ ] 唯一待办\n\n" + tail)
    assert len(items) == 1
    assert any("未闭合" in warning for warning in warnings)


def test_list_contained_code_fences_and_unicode_line_separator():
    body = "- ```markdown\n  - [ ] 列表中代码伪待办\n  ```\n- [ ] 标题含\u2028Unicode分隔符\n- [ ] 下一行"
    items, warnings = parse_checkboxes(body)
    assert [item["line"] for item in items] == [4, 5]
    assert items[0]["title"] == "标题含\u2028Unicode分隔符"
    assert items[0]["description"] == "- [ ] 标题含\u2028Unicode分隔符"
    assert any("围栏" in warning for warning in warnings)


@pytest.mark.parametrize("code", ['`<!--`', '``<!-- ` literal``', '```<!-- `` literal```'])
def test_inline_code_comment_literal_keeps_title_and_following_tasks(code):
    title = f"Read {code} syntax"
    body = f"- [ ] {title}\n- [ ] Ordinary second task"
    items, warnings = parse_checkboxes(body)
    assert [item["title"] for item in items] == [title, "Ordinary second task"]
    assert items[0]["description"] == body.split("\n")[0]
    assert not any("未闭合" in warning for warning in warnings)


def test_real_comments_outside_inline_code_are_still_masked():
    body = '- [ ] Read `<!--` syntax <!-- hidden -->\n<!--\n- [ ] Hidden task\n-->\n- [ ] Visible task'
    items, warnings = parse_checkboxes(body)
    assert [item["title"] for item in items] == ['Read `<!--` syntax', 'Visible task']
    assert [item["line"] for item in items] == [1, 5]
    assert any("HTML 注释" in warning for warning in warnings)


def test_escaped_backtick_does_not_hide_an_actual_html_comment():
    body = r'- [ ] Read \`literal <!-- actual hidden --> suffix`' + '\n- [ ] Next task'
    items, _ = parse_checkboxes(body)
    assert len(items) == 2
    assert 'actual hidden' not in items[0]['title']
    assert items[1]['title'] == 'Next task'


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_top_level_indented_code_never_becomes_a_task_or_comment_state(indent):
    body = f'# Example\n\n{indent}- [ ] Code example\n{indent}<!-- literal code\n\n- [ ] Real task'
    items, warnings = parse_checkboxes(body)
    assert [item['title'] for item in items] == ['Real task']
    assert items[0]['line'] == 6
    assert any('缩进代码' in warning for warning in warnings)
    assert not any('未闭合' in warning for warning in warnings)


@pytest.mark.parametrize("parent,indent", [('- Parent', '  '), ('- Parent', '    '), ('1. Parent', '   '), ('10. Parent', '    ')])
def test_nested_list_tasks_keep_parent_context_across_blank_lines(parent, indent):
    body = f'{parent}\n\n{indent}- [ ] Child\n{indent}- [x] Sibling\n- [ ] Root task'
    items, warnings = parse_checkboxes(body)
    assert [item['title'] for item in items] == ['Child', 'Sibling', 'Root task']
    assert [item['checked'] for item in items] == [False, True, False]
    assert not any('缩进代码' in warning for warning in warnings)


def test_nested_indented_code_is_excluded_without_losing_nested_siblings():
    body = '- Parent\n\n      - [ ] Code example\n      <!-- Code comment opener\n\n    - [ ] Child\n        - [ ] Grandchild\n    - [ ] Child sibling\n- [ ] Root'
    items, warnings = parse_checkboxes(body)
    assert [item['title'] for item in items] == ['Child', 'Grandchild', 'Child sibling', 'Root']
    assert any('缩进代码' in warning for warning in warnings)
    assert not any('未闭合' in warning for warning in warnings)


def test_top_level_paragraph_or_heading_ends_prior_list_indentation_context():
    body = '- Parent\n\nOutside paragraph\n\n    - [ ] Code example\n\n# New heading\n\n    - [ ] Another code example\n\n- [ ] Real task'
    items, warnings = parse_checkboxes(body)
    assert [item['title'] for item in items] == ['Real task']
    assert items[0]['heading'] == 'New heading'
    assert any('缩进代码' in warning for warning in warnings)


def test_excessive_list_padding_is_not_treated_as_a_checkbox():
    items, warnings = parse_checkboxes('-     [ ] Code within list\n- [ ] Real task')
    assert [item['title'] for item in items] == ['Real task']
    assert any('缩进代码' in warning for warning in warnings)


