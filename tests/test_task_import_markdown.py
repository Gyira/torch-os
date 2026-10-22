"""Task import writes Markdown task notes, not the retired SQLite task table."""

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
from backend.app.task_import import TaskImportApply, TaskImportService, create_task_import_router
from backend.app.vault import parse_text, scan_vault


def write_source(vault: Path, body: str, name: str = "tasks.md", **metadata) -> Path:
    path = vault / name
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {"id": "synthetic-checklist", "type": "note", "title": "合成任务清单", **metadata}
    path.write_text("---\n" + yaml.safe_dump(values, allow_unicode=True) + "---\n\n" + body + "\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "tasks.sqlite3")
    vault = tmp_path / "vault"
    service = TaskImportService(database, vault)
    source = write_source(
        vault, "# 学习\n\n- [ ] 阅读第二章\n- [x] 写下读书笔记\n* [X] 核对引用\n1. [ ] 周六复习")
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
    return {
        "revision": source_preview["revision"], "sha256": source_preview["sha256"],
        "selected_keys": [item["key"] for item in source_preview["items"]],
        "goal_path": "", "confirm": True, **overrides,
    }


def apply(client, document_id, source_preview, **overrides):
    return client.post(f"/api/documents/{document_id}/tasks/apply",
                       json=request_body(source_preview, **overrides))


def notes(service):
    return task_notes.scan_task_notes(service.vault_dir)[0]


def old_task_counts(service):
    with service.database.connect() as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM task_import_ledger").fetchone()[0],
        )


def edit_values(note, **changes):
    values = {key: note.get(key) for key in (
        "date", "time", "priority", "tags", "done", "done_on", "pinned",
        "created", "sort_order", "dida_id", "goal", "import_key",
    )}
    values.update(changes)
    return values


def test_import_creates_markdown_notes_with_goal_and_source_evidence(setup):
    service, client, source, document_id = setup
    goal = pillar_notes.create_note(service.vault_dir, pillar="life", chapter="goal_year",
                                    title="学习", note_type="goal")
    source_preview = preview(client, document_id)
    selected = [source_preview["items"][0]["key"], source_preview["items"][1]["key"]]
    result = apply(client, document_id, source_preview, selected_keys=selected,
                   goal_path=goal["path"])
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["created_count"] == 2 and payload["existing_count"] == 0
    todo, completed = payload["tasks"]
    assert todo["status"] == "todo" and completed["status"] == "completed"
    assert completed["completed_at"] is None and todo["completed_at"] is None
    assert todo["goal"] == goal["path"]
    assert todo["tags"] == ["markdown-import"] and todo["priority"] == "none"
    assert "来源：tasks.md 第 3 行" in todo["description"]

    created = {note["title"]: note for note in notes(service)}
    assert set(created) == {"阅读第二章", "写下读书笔记"}
    for note in created.values():
        assert note["goal"] == goal["path"]
        assert len(note["import_key"]) == 64
        assert task_notes.note_body(note).startswith("- [")
    assert old_task_counts(service) == (0, 0)
    with service.database.connect() as connection:
        audit = [json.loads(row[0]) for row in connection.execute(
            "SELECT detail_json FROM audit_events "
            "WHERE event_type IN ('task_created','markdown_tasks_imported')")]
    assert audit == [{"document_id": document_id, "created": 2, "existing": 0}]
    assert "阅读第二章" not in json.dumps(audit, ensure_ascii=False)


def test_repeat_selection_returns_existing_notes_after_rename_and_edit(setup):
    service, client, source, document_id = setup
    source_preview = preview(client, document_id)
    selected = [source_preview["items"][0]["key"]]
    first = apply(client, document_id, source_preview, selected_keys=selected).json()
    note = notes(service)[0]
    edited = task_notes.rewrite_task_note(
        service.vault_dir, note, folder="", title="我修改的标题",
        values=edit_values(note, done=True, done_on="2026-10-20"))
    assert edited["relpath"] != note["relpath"]

    result = apply(client, document_id, source_preview, selected_keys=selected).json()
    assert result["created_count"] == 0 and result["existing_count"] == 1
    assert result["tasks"][0]["title"] == "我修改的标题"
    assert result["tasks"][0]["status"] == "completed"
    assert len(notes(service)) == 1
    assert old_task_counts(service) == (0, 0)
    assert first["tasks"][0]["key"] != result["tasks"][0]["key"]


def test_goal_path_must_resolve_to_a_goal_note(setup):
    service, client, source, document_id = setup
    journal = pillar_notes.create_note(service.vault_dir, pillar="life", chapter="journal",
                                       title="日记", note_type="journal")
    source_preview = preview(client, document_id)
    for goal_path in (journal["path"], "00_人生路/06_目标与计划/01_年度目标/不存在.md",
                      "03_知识库/00_收件箱/任意.md"):
        response = apply(client, document_id, source_preview, goal_path=goal_path)
        assert response.status_code == 404, response.text
        assert notes(service) == []


@pytest.mark.parametrize("overrides", [
    {"selected_keys": []}, {"selected_keys": ["f" * 64]}, {"selected_keys": None},
    {"selected_keys": ["f" * 64, "f" * 64]}, {"selected_keys": [123]},
    {"revision": 0}, {"revision": "1"}, {"revision": True}, {"revision": None},
    {"sha256": ""}, {"sha256": None}, {"sha256": "x" * 64},
    {"goal_path": "x" * 501},
    {"confirm": False}, {"confirm": 1}, {"confirm": "true"}, {"confirm": None},
    {"unexpected": "field"},
])
def test_invalid_apply_requests_are_rejected_without_writes(setup, overrides):
    service, client, source, document_id = setup
    source_preview = preview(client, document_id)
    result = apply(client, document_id, source_preview, **overrides)
    assert result.status_code == 422, result.text
    assert notes(service) == []
    assert old_task_counts(service) == (0, 0)


def test_missing_confirmation_and_unknown_selected_key_are_rejected(setup):
    service, client, source, document_id = setup
    source_preview = preview(client, document_id)
    payload = request_body(source_preview)
    del payload["confirm"]
    assert client.post(f"/api/documents/{document_id}/tasks/apply", json=payload).status_code == 422
    assert apply(client, document_id, source_preview,
                 selected_keys=[source_preview["items"][0]["key"], "f" * 64]).status_code == 422
    assert notes(service) == []


def test_changed_source_requires_new_preview_and_selection(setup):
    service, client, source, document_id = setup
    old = preview(client, document_id)
    write_source(service.vault_dir, "# 新清单\n- [ ] 新的任务")
    scan_vault(service.vault_dir, service.database)
    current = preview(client, document_id)
    assert current["revision"] == old["revision"] + 1
    assert apply(client, document_id, old).status_code == 409
    assert notes(service) == []
    response = apply(client, document_id, current)
    assert response.status_code == 200
    assert response.json()["tasks"][0]["title"] == "新的任务"


def test_source_change_during_batch_leaves_recoverable_import_keys(setup, monkeypatch):
    service, client, source, document_id = setup
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
    with pytest.raises(HTTPException) as exc:
        service.apply(document_id, TaskImportApply(**request_body(source_preview)))
    assert exc.value.status_code == 409 and calls == 2
    assert 1 <= len(notes(service)) <= 4

    scan_vault(service.vault_dir, service.database)
    current = preview(client, document_id)
    result = apply(client, document_id, current).json()
    assert len(notes(service)) == 4
    assert result["created_count"] + result["existing_count"] == 4


def test_concurrent_same_selection_creates_one_copy_per_item(setup):
    service, client, source, document_id = setup
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
    assert len(notes(service)) == 4


def test_repeated_initialization_and_rename_keep_deduplication(setup):
    service, client, source, document_id = setup
    source_preview = service.preview(document_id)
    payload = TaskImportApply(**request_body(source_preview))
    created = service.apply(document_id, payload)
    reopened = TaskImportService(service.database, service.vault_dir)
    repeated = reopened.apply(document_id, payload)
    assert repeated["created_count"] == 0 and repeated["existing_count"] == 4
    assert repeated["tasks"] == created["tasks"]

    source.rename(source.with_name("renamed.md"))
    scan_vault(service.vault_dir, service.database)
    current = preview(client, document_id)
    assert current["sha256"] == source_preview["sha256"]
    assert current["revision"] == source_preview["revision"]
    again = apply(client, document_id, current).json()
    assert again["created_count"] == 0 and again["tasks"] == created["tasks"]


def test_restricted_metadata_rejected_even_with_matching_index(tmp_path):
    database = Database(tmp_path / "restricted.sqlite3")
    vault = tmp_path / "vault"
    service = TaskImportService(database, vault)
    source = write_source(vault, "- [ ] 隐私待办", type="constitution")
    raw = source.read_bytes()
    values, body = parse_text(raw.decode("utf-8"))
    document_id, _ = database.upsert_document("tasks.md", "合成任务清单", body,
                                              hashlib.sha256(raw).hexdigest(), values)
    with pytest.raises(HTTPException) as exc:
        service.preview(document_id)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("relative", [
    "relationships/tasks.md", ".hidden/tasks.md", "00-System/tasks.md",
    "../escape.md", "tasks.txt",
])
def test_restricted_or_escaping_paths_rejected(tmp_path, relative):
    database = Database(tmp_path / "paths.sqlite3")
    vault = tmp_path / "vault"
    service = TaskImportService(database, vault)
    source = write_source(vault, "- [ ] 源待办", name=relative)
    raw = source.read_bytes()
    metadata, body = parse_text(raw.decode("utf-8"))
    document_id, _ = database.upsert_document(relative, "合成任务清单", body,
                                              hashlib.sha256(raw).hexdigest(), metadata)
    with pytest.raises(HTTPException) as exc:
        service.preview(document_id)
    assert exc.value.status_code == 409


def test_unknown_document_returns_404(setup):
    service, client, source, document_id = setup
    source_preview = preview(client, document_id)
    assert client.get("/api/documents/999999/tasks/preview").status_code == 404
    assert apply(client, 999999, source_preview).status_code == 404


def test_uppercase_source_digest_is_accepted_without_changing_idempotency(setup):
    service, client, source, document_id = setup
    source_preview = preview(client, document_id)
    first = apply(client, document_id, source_preview,
                  sha256=source_preview["sha256"].upper()).json()
    repeated = apply(client, document_id, source_preview).json()
    assert first["created_count"] == 4 and repeated["created_count"] == 0
    assert repeated["tasks"] == first["tasks"]


def test_html_is_stored_verbatim_and_never_executed(setup):
    service, client, source, document_id = setup
    text = '<script>alert("synthetic")</script> 阅读'
    write_source(service.vault_dir, "- [ ] " + text)
    scan_vault(service.vault_dir, service.database)
    source_preview = preview(client, document_id)
    result = apply(client, document_id, source_preview).json()
    assert result["tasks"][0]["title"] == text
    assert "- [ ] " + text in result["tasks"][0]["description"]
