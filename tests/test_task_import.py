"""Markdown task import uses synthetic files and temporary SQLite databases."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.db import Database
from backend.app.goals import GoalCreate, GoalTaskService, TaskUpdate
from backend.app.journal import JournalService
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
    goals = GoalTaskService(database)
    source = write_source(vault, "# 学习\n\n- [ ] 阅读第二章\n- [x] 写下读书笔记\n* [X] 核对引用\n1. [ ] 周六复习")
    scan_vault(vault, database)
    document_id = database.list_documents()[0]["id"]
    app = FastAPI()
    app.include_router(create_task_import_router(database, vault))
    return service, goals, TestClient(app), source, document_id


def preview(client, document_id):
    response = client.get(f"/api/documents/{document_id}/tasks/preview")
    assert response.status_code == 200, response.text
    return response.json()


def request_body(source_preview, **overrides):
    return {"revision": source_preview["revision"], "sha256": source_preview["sha256"],
            "selected_keys": [item["key"] for item in source_preview["items"]],
            "goal_id": None, "confirm": True, **overrides}


def apply(client, document_id, source_preview, **overrides):
    return client.post(f"/api/documents/{document_id}/tasks/apply", json=request_body(source_preview, **overrides))


def counts(database):
    with database.connect() as connection:
        return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("tasks", "task_import_ledger", "audit_events"))


def test_preview_is_read_only_preserves_lines_and_heading(setup):
    service, goals, client, source, document_id = setup
    before = counts(service.database)
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
    assert counts(service.database) == before
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


def test_parser_fixes_are_used_by_preview_and_apply_without_source_changes(setup):
    service, goals, client, source, document_id = setup
    body = '# Tasks\n\n    - [ ] Code only\n\n- [ ] Read `<!--` syntax\n- Parent\n    - [ ] Nested action'
    write_source(service.vault_dir, body)
    scan_vault(service.vault_dir, service.database)
    original = source.read_bytes()
    source_preview = preview(client, document_id)
    assert [item['title'] for item in source_preview['items']] == ['Read `<!--` syntax', 'Nested action']
    response = apply(client, document_id, source_preview)
    assert response.status_code == 200
    assert response.json()['created_count'] == 2
    assert source.read_bytes() == original
    assert {task['title'] for task in goals.list_tasks()} == {'Read `<!--` syntax', 'Nested action'}


def test_duplicates_long_titles_empty_rows_and_continuations_are_explicit():
    long_text = "很长的任务" * 2300
    body = "- [ ] 重复\n- [ ] 重复\n- [ ] " + long_text + "\n  这是未自动合并的续行\n- [ ]  \n- [ ] <!-- 只有注释 -->"
    items, warnings = parse_checkboxes(body)
    assert len(items) == 3 and len({item["key"] for item in items}) == 3
    assert len(items[2]["title"]) == 200
    assert items[2]["description"] == "- [ ] " + long_text
    assert any("续行" in warning for warning in warnings)
    assert any("200 字符" in warning for warning in warnings)
    assert any("可见标题" in warning for warning in warnings)


def test_import_only_selected_items_and_keep_completed_date_unknown(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    goal = goals.create_goal(GoalCreate(title="学习"))
    selected = [source_preview["items"][0]["key"], source_preview["items"][1]["key"]]
    result = apply(client, document_id, source_preview, selected_keys=selected, goal_id=goal["id"])
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["created_count"] == 2 and result["existing_count"] == 0
    todo, completed = result["tasks"]
    assert todo["status"] == "todo" and completed["status"] == "completed"
    assert completed["completed_at"] is None and todo["completed_at"] is None
    assert todo["due_date"] is None and completed["due_date"] is None
    assert all(task["goal_id"] == goal["id"] for task in result["tasks"])
    assert todo["description"] == source_preview["items"][0]["description"]
    assert todo["tags"] == ["markdown-import"] and todo["priority"] == "medium"
    assert len(goals.list_tasks()) == 2
    with service.database.connect() as connection:
        ledger = [dict(row) for row in connection.execute("SELECT * FROM task_import_ledger ORDER BY source_line")]
        audit = [json.loads(row[0]) for row in connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type IN ('task_created','markdown_tasks_imported')")]
    assert [(row["source_line"], row["task_id"]) for row in ledger] == [(3, todo["id"]), (4, completed["id"])]
    assert all(row["source_hash"] == source_preview["sha256"] and row["source_revision"] == 1 for row in ledger)
    assert all(set(event) <= {"task_id", "task_ids", "document_id", "goal_id"} for event in audit)
    assert "阅读第二章" not in json.dumps(audit, ensure_ascii=False)
    journal = JournalService(service.database, service.vault_dir)
    today = date.fromisoformat(completed["created_at"][:10]).isoformat()
    assert completed["id"] not in [task["id"] for task in journal.review(today)["completed_tasks"]]


def test_exactly_twenty_items_and_private_personal_metadata_are_supported(setup):
    service, goals, client, source, document_id = setup
    write_source(service.vault_dir, "\n".join(f"- [ ] 任务 {number}" for number in range(21)), visibility="private")
    scan_vault(service.vault_dir, service.database)
    source_preview = preview(client, document_id)
    assert len(source_preview["items"]) == 21
    assert apply(client, document_id, source_preview).status_code == 422
    result = apply(client, document_id, source_preview, selected_keys=[item["key"] for item in source_preview["items"][:20]])
    assert result.status_code == 200
    assert result.json()["created_count"] == 20
    assert len(goals.list_tasks()) == 20


def test_empty_document_preview_does_not_invent_tasks(setup):
    service, goals, client, source, document_id = setup
    write_source(service.vault_dir, "# 说明\n\n只有普通说明和未声明的计划。")
    scan_vault(service.vault_dir, service.database)
    source_preview = preview(client, document_id)
    assert source_preview["items"] == []
    assert goals.list_tasks() == []


def test_repeat_selection_returns_current_user_edited_tasks(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    selected = [source_preview["items"][0]["key"]]
    first = apply(client, document_id, source_preview, selected_keys=selected).json()
    task_id = first["tasks"][0]["id"]
    goal = goals.create_goal(GoalCreate(title="另一个目标"))
    edited = goals.update_task(task_id, TaskUpdate(title="我修改的标题", description="我的说明", status="completed", goal_id=goal["id"]))
    before = counts(service.database)
    result = apply(client, document_id, source_preview, selected_keys=selected).json()
    assert result["created_count"] == 0 and result["existing_count"] == 1
    assert result["tasks"] == [edited]
    assert counts(service.database) == before


def test_repeat_checked_task_never_fills_missing_completion_timestamp(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    selected = [source_preview["items"][1]["key"]]
    first = apply(client, document_id, source_preview, selected_keys=selected).json()
    repeated = apply(client, document_id, source_preview, selected_keys=selected).json()
    assert first["tasks"] == repeated["tasks"]
    assert repeated["tasks"][0]["completed_at"] is None


@pytest.mark.parametrize("overrides", [
    {"selected_keys": []}, {"selected_keys": ["f" * 64] * 21}, {"selected_keys": None},
    {"selected_keys": ["f" * 64, "f" * 64]}, {"selected_keys": [123]}, {"selected_keys": ["unknown"]},
    {"revision": 0}, {"revision": "1"}, {"revision": True}, {"revision": None},
    {"sha256": ""}, {"sha256": None}, {"sha256": "x" * 64},
    {"goal_id": 0}, {"goal_id": True}, {"goal_id": "1"},
    {"confirm": False}, {"confirm": 1}, {"confirm": "true"}, {"confirm": None},
    {"unexpected": "field"},
])
def test_invalid_apply_requests_are_rejected_without_writes(setup, overrides):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    before = counts(service.database)
    result = apply(client, document_id, source_preview, **overrides)
    assert result.status_code == 422, result.text
    assert counts(service.database) == before


def test_missing_confirmation_and_unknown_selected_key_are_rejected(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    payload = request_body(source_preview)
    del payload["confirm"]
    before = counts(service.database)
    assert client.post(f"/api/documents/{document_id}/tasks/apply", json=payload).status_code == 422
    assert apply(client, document_id, source_preview, selected_keys=[source_preview["items"][0]["key"], "f" * 64]).status_code == 422
    assert counts(service.database) == before


def test_missing_goal_is_checked_even_for_idempotent_retries(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    assert apply(client, document_id, source_preview).status_code == 200
    before = counts(service.database)
    assert apply(client, document_id, source_preview, goal_id=99999).status_code == 404
    assert counts(service.database) == before


def test_goal_deleted_after_preview_prevents_whole_import(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    goal = goals.create_goal(GoalCreate(title="临时目标"))
    assert goals.delete_goal(goal["id"])
    before = counts(service.database)
    assert apply(client, document_id, source_preview, goal_id=goal["id"]).status_code == 404
    assert counts(service.database) == before


def test_deleted_prior_task_rolls_back_even_new_items_before_it(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    first_key, second_key = [item["key"] for item in source_preview["items"][:2]]
    task = apply(client, document_id, source_preview, selected_keys=[second_key]).json()["tasks"][0]
    assert goals.delete_task(task["id"])
    before = counts(service.database)
    response = apply(client, document_id, source_preview, selected_keys=[first_key, second_key])
    assert response.status_code == 409
    assert "已删除" in response.json()["detail"]
    assert counts(service.database) == before


@pytest.mark.parametrize("mutation", ["body", "metadata", "deleted", "invalid-yaml", "oversized", "unavailable", "privacy", "indexed-body", "indexed-metadata", "identity"])
def test_stale_or_ineligible_sources_never_use_old_index(setup, mutation):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    if mutation == "body":
        source.write_text(source.read_text(encoding="utf-8") + "\n- [ ] 新条目", encoding="utf-8")
    elif mutation == "metadata":
        source.write_text(source.read_text(encoding="utf-8").replace("type: note", "type: constitution"), encoding="utf-8")
    elif mutation == "deleted":
        source.unlink()
    elif mutation == "invalid-yaml":
        source.write_text("---\n[not a mapping]\n---\n- [ ] 待办", encoding="utf-8")
    elif mutation == "oversized":
        source.write_bytes(b"x" * (MAX_NOTE_BYTES + 1))
    else:
        column, value = {
            "unavailable": ("available", 0), "privacy": ("privacy", "restricted"),
            "indexed-body": ("content", "- [ ] 被篡改的旧索引"),
            "indexed-metadata": ("metadata_json", "[]"), "identity": ("source_key", "replaced-id"),
        }[mutation]
        with service.database.connect() as connection:
            connection.execute(f"UPDATE documents SET {column}=? WHERE id=?", (value, document_id))
    before = counts(service.database)
    assert client.get(f"/api/documents/{document_id}/tasks/preview").status_code == 409
    assert apply(client, document_id, source_preview).status_code == 409
    assert counts(service.database) == before


@pytest.mark.parametrize("metadata", [
    {"scope": "relationship"}, {"memory_scope": "relationship"}, {"visibility": "secret"},
    {"sensitivity": "restricted"}, {"type": "constitution"},
])
def test_restricted_metadata_rejected_even_with_matching_index(tmp_path, metadata):
    database = Database(tmp_path / "restricted.sqlite3")
    vault = tmp_path / "vault"
    service = TaskImportService(database, vault)
    source = write_source(vault, "- [ ] 隐私待办", **metadata)
    raw = source.read_bytes()
    values, body = parse_text(raw.decode("utf-8"))
    document_id, _ = database.upsert_document("tasks.md", "合成任务清单", body, hashlib.sha256(raw).hexdigest(), values)
    before = counts(database)
    with pytest.raises(HTTPException) as exc:
        service.preview(document_id)
    assert exc.value.status_code == 409 and counts(database) == before


@pytest.mark.parametrize("relative", ["relationships/tasks.md", ".hidden/tasks.md", "00-System/tasks.md", "../escape.md", "tasks.txt"])
def test_restricted_or_escaping_paths_rejected(tmp_path, relative):
    database = Database(tmp_path / "paths.sqlite3")
    vault = tmp_path / "vault"
    service = TaskImportService(database, vault)
    source = write_source(vault, "- [ ] 源待办", name=relative)
    raw = source.read_bytes()
    metadata, body = parse_text(raw.decode("utf-8"))
    document_id, _ = database.upsert_document(relative, "合成任务清单", body, hashlib.sha256(raw).hexdigest(), metadata)
    with pytest.raises(HTTPException) as exc:
        service.preview(document_id)
    assert exc.value.status_code == 409


def test_changed_index_requires_new_preview_and_selection(setup):
    service, goals, client, source, document_id = setup
    old = preview(client, document_id)
    write_source(service.vault_dir, "# 新清单\n- [ ] 新的任务")
    scan_vault(service.vault_dir, service.database)
    current = preview(client, document_id)
    assert current["revision"] == old["revision"] + 1
    before = counts(service.database)
    assert apply(client, document_id, old).status_code == 409
    assert counts(service.database) == before
    response = apply(client, document_id, current)
    assert response.status_code == 200
    assert response.json()["tasks"][0]["title"] == "新的任务"


def test_source_changed_during_batch_rolls_back_everything(setup, monkeypatch):
    service, goals, client, source, document_id = setup
    source_preview = service.preview(document_id)
    original_read = service._current_source
    calls = 0

    def change_at_final_check(connection, selected_document_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            source.write_text(source.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
        return original_read(connection, selected_document_id)

    monkeypatch.setattr(service, "_current_source", change_at_final_check)
    before = counts(service.database)
    with pytest.raises(HTTPException) as exc:
        service.apply(document_id, TaskImportApply(**request_body(source_preview)))
    assert exc.value.status_code == 409 and calls == 2
    assert counts(service.database) == before


def test_database_write_failure_rolls_back_tasks_ledger_and_audit(setup):
    service, goals, client, source, document_id = setup
    source_preview = service.preview(document_id)
    with service.database.connect() as connection:
        connection.execute("""CREATE TRIGGER reject_second_task BEFORE INSERT ON tasks
            WHEN NEW.title='写下读书笔记' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    before = counts(service.database)
    with pytest.raises(sqlite3.IntegrityError):
        service.apply(document_id, TaskImportApply(**request_body(source_preview)))
    assert counts(service.database) == before


def test_concurrent_same_selection_creates_one_copy_per_item(setup):
    service, goals, client, source, document_id = setup
    source_preview = service.preview(document_id)
    payload = TaskImportApply(**request_body(source_preview))
    barrier = Barrier(4)

    def import_once():
        barrier.wait()
        return service.apply(document_id, payload)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: import_once(), range(4)))
    assert sorted(result["created_count"] for result in results) == [0, 0, 0, 4]
    assert sorted(result["existing_count"] for result in results) == [0, 4, 4, 4]
    assert all(result["tasks"] == results[0]["tasks"] for result in results)
    assert counts(service.database)[:2] == (4, 4)


def test_repeated_initialization_retains_existing_ledger(setup):
    service, goals, client, source, document_id = setup
    source_preview = service.preview(document_id)
    payload = TaskImportApply(**request_body(source_preview))
    created = service.apply(document_id, payload)
    reopened = TaskImportService(service.database, service.vault_dir)
    repeated = reopened.apply(document_id, payload)
    assert repeated["created_count"] == 0 and repeated["existing_count"] == 4
    assert repeated["tasks"] == created["tasks"]


def test_scanned_rename_with_stable_identity_keeps_task_deduplication(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    first = apply(client, document_id, source_preview).json()
    source.rename(source.with_name("renamed.md"))
    scan_vault(service.vault_dir, service.database)
    current = preview(client, document_id)
    assert current["sha256"] == source_preview["sha256"] and current["revision"] == source_preview["revision"]
    repeated = apply(client, document_id, current).json()
    assert repeated["created_count"] == 0 and repeated["tasks"] == first["tasks"]


def test_unknown_document_returns_404(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    assert client.get("/api/documents/999999/tasks/preview").status_code == 404
    assert apply(client, 999999, source_preview).status_code == 404


def test_uppercase_source_digest_is_accepted_without_changing_idempotency(setup):
    service, goals, client, source, document_id = setup
    source_preview = preview(client, document_id)
    first = apply(client, document_id, source_preview, sha256=source_preview["sha256"].upper()).json()
    repeated = apply(client, document_id, source_preview).json()
    assert first["created_count"] == 4 and repeated["created_count"] == 0
    assert repeated["tasks"] == first["tasks"]


def test_html_is_stored_verbatim_and_never_executed(setup):
    service, goals, client, source, document_id = setup
    text = '<script>alert("synthetic")</script> 阅读'
    write_source(service.vault_dir, "- [ ] " + text)
    scan_vault(service.vault_dir, service.database)
    source_preview = preview(client, document_id)
    result = apply(client, document_id, source_preview).json()
    assert result["tasks"][0]["title"] == text
    assert result["tasks"][0]["description"] == "- [ ] " + text
