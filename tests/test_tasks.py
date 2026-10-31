"""时间管理任务：新任务一任务一笔记，旧复选框任务原地只读共存。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import task_notes, tasks
from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()

SAMPLE = """---
type: tasks
清单:
  - 名称: 收集箱
    颜色: "#3b82f6"
  - 名称: 心愿清单
    颜色: "#8b5cf6"
    置顶: true
  - 名称: 客户A
    颜色: "#ef4444"
    文件夹: 工作
标签:
  - 名称: 重要
    颜色: "#ef4444"
---

# 任务

## 收集箱
- [ ] 买菜 📅 2026-10-06 ⏫ #重要 ➕ 2026-10-01
- [x] 写周报 ✅ 2026-10-05 ➕ 2026-09-28
- [ ] 没标记的任务

```text
- [ ] 围栏里的不算
```

## 心愿清单
- [ ] 去一次海边 📌
"""


def make_client(tmp_path, *, read_only=False):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "tasks.sqlite3"), vault,
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def config_file(tmp_path):
    return tmp_path / "vault" / "02_生活运营" / "00_待办事项" / "_系统" / "任务（tasks）.md"


def legacy_file(tmp_path):
    return tmp_path / "vault" / "00-System" / "任务.md"


def note_dir(tmp_path):
    return tmp_path / "vault" / "02_生活运营" / "00_待办事项"


def inbox_dir(tmp_path):
    return note_dir(tmp_path) / "收集箱"


def seed(tmp_path, text=SAMPLE, *, legacy_name=False):
    path = legacy_file(tmp_path) if legacy_name else config_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def board(client):
    response = client.get("/api/time/tasks")
    assert response.status_code == 200, response.text
    return response.json()


def find(payload, title, *, legacy=None):
    rows = [task for task in payload["tasks"] if task["title"] == title]
    if legacy is not None:
        rows = [task for task in rows if task["legacy"] is legacy]
    assert rows, f"没有找到任务：{title}"
    return rows[0]


def post(client, path, payload):
    return client.post(path, json=payload)


def test_read_merges_legacy_readonly_and_new_notes(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)

    assert payload["legacy_tasks"] == 4
    assert payload["path"] == "02_生活运营/00_待办事项/_系统/任务（tasks）.md"
    assert payload["vault_name"] == "vault"
    assert payload["task_dir"] == "02_生活运营/00_待办事项"
    shopping = find(payload, "买菜", legacy=True)
    assert shopping["path"] == "02_生活运营/00_待办事项/_系统/任务（tasks）.md"
    assert shopping["list_name"] == "收集箱" and shopping["date"] == "2026-10-06"
    assert shopping["priority"] == "high" and shopping["tags"] == ["重要"]
    assert find(payload, "写周报", legacy=True)["done"] is True
    assert all(task["title"] != "围栏里的不算" for task in payload["tasks"])

    created = post(client, "/api/time/tasks", {
        "title": "写方案", "date": TODAY, "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    body = created.json()["task"]
    assert body["legacy"] is False
    assert body["path"] == "02_生活运营/00_待办事项/收集箱/写方案.md"
    assert (inbox_dir(tmp_path) / "写方案.md").is_file()

    payload = board(client)
    assert payload["legacy_tasks"] == 4
    assert find(payload, "写方案")["legacy"] is False

    stale = post(client, "/api/time/tasks/update", {
        "key": shopping["key"], "title": "不能改", "revision": payload["revision"]})
    assert stale.status_code == 409 and "旧任务" in stale.json()["detail"]
    deleted = post(client, "/api/time/tasks/delete", {
        "key": shopping["key"], "revision": payload["revision"]})
    assert deleted.status_code == 409
    bulk = post(client, "/api/time/tasks/bulk", {
        "keys": [shopping["key"]], "action": "delete", "revision": payload["revision"]})
    assert bulk.status_code == 409


def test_create_notes_folders_and_validation(tmp_path):
    client = make_client(tmp_path)
    payload = board(client)
    created = post(client, "/api/time/tasks", {
        "title": "第一条任务", "priority": "medium", "tags": ["重要", "工作"],
        "date": TODAY, "time": "7:05", "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    path = inbox_dir(tmp_path) / "第一条任务.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "type: task" in text
    assert f'日期: "{TODAY}"' in text and '时间: "07:05"' in text
    assert "优先级: medium" in text and "标签:" in text
    assert "标题" not in text

    payload = board(client)
    second = post(client, "/api/time/tasks", {"title": "第一条任务", "revision": payload["revision"]})
    assert second.status_code == 200
    assert (inbox_dir(tmp_path) / "第一条任务 2.md").is_file()
    payload = board(client)
    duplicates = [task for task in payload["tasks"] if task["title"] == "第一条任务"]
    assert len(duplicates) == 2
    # 文件名因重名带编号时，人生路阅读器仍显示 frontmatter 里的文档标题。
    second_doc = client.get("/api/notes", params={
        "path": "02_生活运营/00_待办事项/收集箱/第一条任务 2.md"})
    assert second_doc.status_code == 200, second_doc.text
    assert second_doc.json()["title"] == "第一条任务"

    payload = board(client)
    listed = post(client, "/api/time/lists", {"name": "工作", "color": "#22c55e",
                                              "revision": payload["revision"]})
    assert listed.status_code == 200, listed.text
    assert (note_dir(tmp_path) / "工作").is_dir()
    payload = board(client)
    assert next(item for item in payload["lists"] if item["name"] == "工作")["synthetic"] is False
    in_list = post(client, "/api/time/tasks", {
        "title": "清单任务", "list_name": "工作", "revision": payload["revision"]})
    assert in_list.status_code == 200, in_list.text
    assert (note_dir(tmp_path) / "工作" / "清单任务.md").is_file()

    payload = board(client)
    assert post(client, "/api/time/tasks", {
        "title": "x", "list_name": "不存在", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks", {
        "title": "坏时间", "time": "25:99", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks", {
        "title": "坏日期", "date": "2026-02-30", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks", {
        "title": "坏优先级", "priority": "urgent", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks", {
        "title": "   ", "revision": payload["revision"]}).status_code == 422


def test_update_rename_move_done_and_conflicts(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    created = post(client, "/api/time/tasks", {
        "title": "晨跑", "date": "2026-10-20", "time": "07:05", "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    path = inbox_dir(tmp_path) / "晨跑.md"
    path.write_text(path.read_text(encoding="utf-8") + "我的训练计划\n", encoding="utf-8")

    payload = board(client)
    task = find(payload, "晨跑")
    renamed = post(client, "/api/time/tasks/update", {
        "key": task["key"], "title": "晨跑训练", "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert not path.exists()
    renamed_path = inbox_dir(tmp_path) / "晨跑训练.md"
    assert renamed_path.is_file() and "我的训练计划" in renamed_path.read_text(encoding="utf-8")

    payload = board(client)
    task = find(payload, "晨跑训练")
    moved = post(client, "/api/time/tasks/update", {
        "key": task["key"], "list_name": "心愿清单", "revision": payload["revision"]})
    assert moved.status_code == 200, moved.text
    moved_path = note_dir(tmp_path) / "心愿清单" / "晨跑训练.md"
    assert moved_path.is_file() and not renamed_path.exists()
    payload = board(client)
    assert find(payload, "晨跑训练")["list_name"] == "心愿清单"

    task = find(payload, "晨跑训练")
    done = post(client, "/api/time/tasks/update", {
        "key": task["key"], "done": True, "revision": payload["revision"]})
    assert done.status_code == 200, done.text
    payload = board(client)
    task = find(payload, "晨跑训练")
    assert task["done"] is True and task["done_on"] == TODAY
    undone = post(client, "/api/time/tasks/update", {
        "key": task["key"], "done": False, "revision": payload["revision"]})
    assert undone.status_code == 200
    assert find(board(client), "晨跑训练")["done_on"] == ""

    payload = board(client)
    task = find(payload, "晨跑训练")
    stale = post(client, "/api/time/tasks/update", {
        "key": task["key"], "title": "x", "revision": "deadbeefdeadbeef"})
    assert stale.status_code == 409
    # 别处改了正文 → 复合 revision 变化，写被拒。
    moved_path.write_text(moved_path.read_text(encoding="utf-8") + "外部修改\n", encoding="utf-8")
    external = post(client, "/api/time/tasks/update", {
        "key": task["key"], "title": "x", "revision": payload["revision"]})
    assert external.status_code == 409

    payload = board(client)
    task = find(payload, "晨跑训练")
    cleared = post(client, "/api/time/tasks/update", {
        "key": task["key"], "date": "", "time": "", "revision": payload["revision"]})
    assert cleared.status_code == 200
    task = find(board(client), "晨跑训练")
    assert task["date"] == "" and task["time"] == ""


def test_task_document_read_save_and_title_rename(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    created = post(client, "/api/time/tasks", {
        "title": "写方案", "date": "2026-10-20", "time": "09:00",
        "priority": "high", "tags": ["重要"], "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    path = inbox_dir(tmp_path) / "写方案.md"
    path.write_text(path.read_text(encoding="utf-8") + "初始正文\n", encoding="utf-8")

    doc = client.get("/api/time/tasks/document", params={"key": task["key"]})
    assert doc.status_code == 200, doc.text
    document = doc.json()
    assert document["title"] == "写方案"
    assert document["body"].strip() == "初始正文"
    assert document["path"] == task["path"]

    # 人生路任务章节：通用阅读接口与章节列表都能看到这篇文档。
    reading = client.get("/api/notes", params={"path": task["path"]})
    assert reading.status_code == 200, reading.text
    reading_note = reading.json()
    assert reading_note["chapter"] == "task" and reading_note["type"] == "task"
    assert reading_note["title"] == "写方案"
    listed = client.get("/api/pillars/ops/notes", params={"chapter": "task", "limit": 500})
    assert listed.status_code == 200, listed.text
    assert any(item["path"] == task["path"] for item in listed.json()["items"])

    # 人生路阅读器只有路径：按 path 也能读回同一篇文档。
    by_path = client.get("/api/time/tasks/document", params={"path": task["path"]})
    assert by_path.status_code == 200, by_path.text
    assert by_path.json()["key"] == task["key"]

    # 任务板 revision 是复合 revision，不能当文档 revision 用。
    stale = post(client, "/api/time/tasks/document", {
        "key": task["key"], "expected_revision": board(client)["revision"],
        "title": "写方案", "body": "不能写"})
    assert stale.status_code == 409

    saved = post(client, "/api/time/tasks/document", {
        "key": task["key"], "expected_revision": document["revision"],
        "title": "写方案 v2", "body": "# 方案\n\n正文内容\n"})
    assert saved.status_code == 200, saved.text
    saved_doc = saved.json()
    assert saved_doc["title"] == "写方案 v2"
    assert saved_doc["path"].endswith("/写方案 v2.md")
    assert saved_doc["key"] != task["key"]
    renamed_path = inbox_dir(tmp_path) / "写方案 v2.md"
    assert renamed_path.is_file() and "正文内容" in renamed_path.read_text(encoding="utf-8")

    row = find(board(client), "写方案 v2")
    assert row["path"] == saved_doc["path"]
    assert row["done"] is False and row["priority"] == "high" and row["tags"] == ["重要"]
    assert row["date"] == "2026-10-20" and row["time"] == "09:00"

    # 标题改名会轮换 key：旧 key 失效，新 key + 新 revision 可继续保存。
    gone = client.get("/api/time/tasks/document", params={"key": task["key"]})
    assert gone.status_code == 409
    second = post(client, "/api/time/tasks/document", {
        "key": saved_doc["key"], "expected_revision": saved_doc["revision"],
        "title": "写方案 v2", "body": "第二版\n"})
    assert second.status_code == 200, second.text
    assert "第二版" in renamed_path.read_text(encoding="utf-8")

    with Database(tmp_path / "tasks.sqlite3").connect() as connection:
        rows = connection.execute(
            "SELECT event_type, detail_json FROM audit_events").fetchall()
    details = [detail for event, detail in rows if event == "task_document_saved"]
    assert details
    assert all("写方案" not in detail and "正文内容" not in detail for detail in details)


def test_task_document_keeps_done_state_and_rejects_legacy_or_read_only(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    created = post(client, "/api/time/tasks", {
        "title": "完成也要能写", "revision": payload["revision"]})
    task = created.json()["task"]
    payload = board(client)
    done = post(client, "/api/time/tasks/update", {
        "key": task["key"], "done": True, "revision": payload["revision"]})
    assert done.status_code == 200, done.text
    task = find(board(client), "完成也要能写")
    assert task["done"] is True
    doc = client.get("/api/time/tasks/document", params={"key": task["key"]})
    assert doc.status_code == 200
    saved = post(client, "/api/time/tasks/document", {
        "key": task["key"], "expected_revision": doc.json()["revision"],
        "title": "完成也要能写", "body": "完成后补一条记录\n"})
    assert saved.status_code == 200, saved.text
    done_dir = note_dir(tmp_path) / "已完成"
    saved_file = done_dir / "完成也要能写.md"
    assert saved_file.is_file() and "完成后补一条记录" in saved_file.read_text(encoding="utf-8")
    row = find(board(client), "完成也要能写")
    assert row["done"] is True and row["path"].endswith("/已完成/完成也要能写.md")

    # 旧任务共享 任务（tasks）.md，没有独立文档。
    legacy = find(board(client), "买菜", legacy=True)
    assert client.get("/api/time/tasks/document", params={"key": legacy["key"]}).status_code == 409
    assert post(client, "/api/time/tasks/document", {
        "key": legacy["key"], "title": "买菜", "body": "x"}).status_code == 409

    # 只读库：能读文档，不能写文档。
    readonly_vault = tmp_path / "readonly"
    note = readonly_vault / "vault" / "02_生活运营" / "00_待办事项" / "收集箱" / "只读文档.md"
    note.parent.mkdir(parents=True)
    original = "---\ntype: task\n标题: 只读文档\n---\n\n正文\n"
    note.write_text(original, encoding="utf-8")
    readonly = make_client(readonly_vault, read_only=True)
    readonly_board = board(readonly)
    readonly_task = find(readonly_board, "只读文档")
    readable = readonly.get("/api/time/tasks/document", params={"key": readonly_task["key"]})
    assert readable.status_code == 200, readable.text
    denied = post(readonly, "/api/time/tasks/document", {
        "key": readonly_task["key"], "expected_revision": readable.json()["revision"],
        "title": "只读文档", "body": "不能写"})
    assert denied.status_code == 409
    assert note.read_text(encoding="utf-8") == original


def test_duplicate_and_delete_are_soft(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    created = post(client, "/api/time/tasks", {"title": "写方案", "tags": ["重要"]})
    assert created.status_code == 200
    path = inbox_dir(tmp_path) / "写方案.md"
    path.write_text(path.read_text(encoding="utf-8") + "正文要留下\n", encoding="utf-8")
    payload = board(client)
    task = find(payload, "写方案")
    copied = post(client, "/api/time/tasks/duplicate", {
        "key": task["key"], "revision": payload["revision"]})
    assert copied.status_code == 200, copied.text
    copy_path = inbox_dir(tmp_path) / "写方案 2.md"
    assert copy_path.is_file() and "正文要留下" in copy_path.read_text(encoding="utf-8")
    payload = board(client)
    assert find(payload, "写方案", legacy=False)["created"] == TODAY

    copy = next(item for item in payload["tasks"] if item["path"] == "02_生活运营/00_待办事项/收集箱/写方案 2.md")
    removed = post(client, "/api/time/tasks/delete", {
        "key": copy["key"], "revision": payload["revision"]})
    assert removed.status_code == 200, removed.text
    assert not copy_path.exists()
    trash = list((note_dir(tmp_path) / "回收站").rglob("*.md"))
    assert any(item.name == "写方案 2.md" for item in trash)

    payload = board(client)
    legacy = find(payload, "买菜", legacy=True)
    assert post(client, "/api/time/tasks/duplicate", {
        "key": legacy["key"], "revision": payload["revision"]}).status_code == 409


def test_list_rename_moves_folder_and_legacy_heading(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    created = post(client, "/api/time/tasks", {
        "title": "新海边", "list_name": "心愿清单", "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    assert (note_dir(tmp_path) / "心愿清单" / "新海边.md").is_file()

    payload = board(client)
    renamed = post(client, "/api/time/lists/update", {
        "name": "心愿清单", "new_name": "旅行", "color": "#0ea5e9", "pinned": False,
        "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert (note_dir(tmp_path) / "旅行" / "新海边.md").is_file()
    assert not (note_dir(tmp_path) / "心愿清单").exists()
    text = config_file(tmp_path).read_text(encoding="utf-8")
    assert "## 旅行" in text and "## 心愿清单" not in text
    payload = board(client)
    entry = next(item for item in payload["lists"] if item["name"] == "旅行")
    assert entry["color"] == "#0ea5e9"
    assert find(payload, "新海边")["list_name"] == "旅行"
    assert find(payload, "去一次海边", legacy=True)["list_name"] == "旅行"


def test_reorder_lists_within_the_same_folder_only(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    root_names = [item["name"] for item in payload["lists"] if not item["folder"]]
    assert root_names == ["收集箱", "心愿清单"]

    # 根级：把「心愿清单」拖到「收集箱」前面。
    moved = post(client, "/api/time/lists/reorder", {
        "name": "心愿清单", "before": "收集箱", "revision": payload["revision"]})
    assert moved.status_code == 200, moved.text
    payload = board(client)
    assert [item["name"] for item in payload["lists"] if not item["folder"]] == ["心愿清单", "收集箱"]
    text = config_file(tmp_path).read_text(encoding="utf-8")
    assert text.index("名称: 心愿清单") < text.index("名称: 收集箱")

    # before 为空 = 放到同一组末尾；文件夹里的清单位置不受影响。
    moved = post(client, "/api/time/lists/reorder", {
        "name": "心愿清单", "before": "", "revision": payload["revision"]})
    assert moved.status_code == 200, moved.text
    payload = board(client)
    assert [item["name"] for item in payload["lists"] if not item["folder"]] == ["收集箱", "心愿清单"]
    work = next(item for item in payload["lists"] if item["name"] == "客户A")
    assert work["folder"] == "工作"

    # 跨文件夹拖拽拒绝；未知清单、过期 revision 也拒绝。
    payload = board(client)
    assert post(client, "/api/time/lists/reorder", {
        "name": "客户A", "before": "收集箱", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/lists/reorder", {
        "name": "无此清单", "before": "收集箱", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/lists/reorder", {
        "name": "心愿清单", "before": "收集箱", "revision": "stale"}).status_code == 409
    assert [item["name"] for item in board(client)["lists"] if not item["folder"]] == ["收集箱", "心愿清单"]


def test_duplicate_list_copies_notes_and_legacy_lines(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    post(client, "/api/time/tasks", {"title": "新买菜", "list_name": "收集箱",
                                    "revision": payload["revision"]})
    payload = board(client)
    duplicated = post(client, "/api/time/lists/duplicate", {
        "name": "收集箱", "scope": "all_keep", "revision": payload["revision"]})
    assert duplicated.status_code == 200, duplicated.text
    payload = board(client)
    names = [item["name"] for item in payload["lists"]]
    assert names.index("收集箱 副本") < names.index("收集箱")
    copy_tasks = [task for task in payload["tasks"] if task["list_name"] == "收集箱 副本"]
    assert {task["title"] for task in copy_tasks} == {"买菜", "写周报", "没标记的任务", "新买菜"}
    assert any(task["done"] for task in copy_tasks)
    assert (note_dir(tmp_path) / "收集箱 副本" / "新买菜.md").is_file()
    text = config_file(tmp_path).read_text(encoding="utf-8")
    assert text.index("## 收集箱 副本\n") < text.index("## 收集箱\n")

    payload = board(client)
    reset = post(client, "/api/time/lists/duplicate", {
        "name": "收集箱", "scope": "undone", "revision": payload["revision"]})
    assert reset.status_code == 200
    payload = board(client)
    reset_tasks = [task for task in payload["tasks"] if task["list_name"] == "收集箱 副本 2"]
    assert reset_tasks and all(not task["done"] for task in reset_tasks)
    assert all(task["title"] != "写周报" for task in reset_tasks)

    invalid = post(client, "/api/time/lists/duplicate", {
        "name": "收集箱", "scope": "nope", "revision": payload["revision"]})
    assert invalid.status_code == 422


def test_delete_list_trashes_tasks_backs_up_config_and_protects_files(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    post(client, "/api/time/lists", {"name": "临时清单", "revision": payload["revision"]})
    payload = board(client)
    post(client, "/api/time/tasks", {"title": "临时任务", "list_name": "临时清单",
                                    "revision": payload["revision"]})
    folder = note_dir(tmp_path) / "临时清单"
    assert folder.is_dir()

    payload = board(client)
    (folder / "附件.txt").write_text("别删我", encoding="utf-8")
    (folder / "临时任务.md.baiduyun.uploading.cfg").write_text("{}", encoding="utf-8")
    payload = board(client)
    removed = post(client, "/api/time/lists/delete", {
        "name": "临时清单", "revision": payload["revision"]})
    assert removed.status_code == 200, removed.text
    data = removed.json()
    assert data["tasks"] == 1 and data["trash"].startswith("02_生活运营/00_待办事项/回收站/")
    assert not folder.exists()
    payload = board(client)
    assert all(item["name"] != "临时清单" for item in payload["lists"])
    assert all(task["title"] != "临时任务" for task in payload["tasks"])
    trash = tmp_path / "vault" / data["trash"]
    assert any(item.name == "任务（tasks）.md" for item in trash.rglob("*"))
    assert any(item.name == "临时任务.md" for item in trash.rglob("*"))
    assert any(item.name == "临时任务.md.baiduyun.uploading.cfg"
               for item in trash.rglob("*"))
    assert any(item.name == "附件.txt" for item in trash.rglob("*"))

    payload = board(client)
    assert post(client, "/api/time/lists/delete", {
        "name": "收集箱", "revision": payload["revision"]}).status_code == 422


def test_logical_folders_stay_separate_from_task_directories(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    post(client, "/api/time/folders", {"name": "工作", "revision": payload["revision"]})
    payload = board(client)
    post(client, "/api/time/lists", {"name": "客户D", "folder": "工作",
                                    "revision": payload["revision"]})
    payload = board(client)
    renamed = post(client, "/api/time/folders/update", {
        "name": "工作", "new_name": "事业", "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    payload = board(client)
    assert payload["folders"] == ["事业"]
    assert next(item for item in payload["lists"] if item["name"] == "客户D")["folder"] == "事业"
    assert (note_dir(tmp_path) / "客户D").is_dir()

    payload = board(client)
    dissolved = post(client, "/api/time/folders/delete", {
        "name": "事业", "revision": payload["revision"]})
    assert dissolved.status_code == 200
    payload = board(client)
    assert payload["folders"] == []
    assert next(item for item in payload["lists"] if item["name"] == "客户D")["folder"] == ""


def test_tags_update_notes_and_legacy_lines(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    post(client, "/api/time/tasks", {"title": "新任务", "tags": ["重要"],
                                    "revision": payload["revision"]})
    payload = board(client)
    renamed = post(client, "/api/time/tags/update", {
        "name": "重要", "new_name": "要紧", "pinned": True, "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["tag"]["renamed_tasks"] == 2  # 一行旧任务 + 一篇笔记
    payload = board(client)
    assert find(payload, "买菜", legacy=True)["tags"] == ["要紧"]
    assert find(payload, "新任务")["tags"] == ["要紧"]
    assert next(tag for tag in payload["tags"] if tag["name"] == "要紧")["pinned"] is True
    assert "#要紧" in config_file(tmp_path).read_text(encoding="utf-8")
    note_text = (inbox_dir(tmp_path) / "新任务.md").read_text(encoding="utf-8")
    assert "要紧" in note_text and "重要" not in note_text

    payload = board(client)
    deleted = post(client, "/api/time/tags/delete", {
        "name": "要紧", "revision": payload["revision"]})
    assert deleted.status_code == 200 and deleted.json()["tasks"] == 2
    payload = board(client)
    assert find(payload, "新任务")["tags"] == []
    assert find(payload, "买菜", legacy=True)["tags"] == []
    assert "#要紧" not in config_file(tmp_path).read_text(encoding="utf-8")


def test_tag_with_whitespace_is_matched_by_canonical_name(tmp_path):
    """配置里的“工作 标签”和任务里的“工作标签”必须按同一个标签处理。

    回归 2026-10-30：删除时只删了任务侧，配置侧还留着，界面提示“标签已删除”
    但标签仍然出现。
    """
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    created = post(client, "/api/time/tags", {
        "name": "工作 标签", "revision": payload["revision"]})
    assert created.status_code == 200, created.text

    payload = board(client)
    assert next(tag for tag in payload["tags"] if tag["name"] == "工作标签")
    task = post(client, "/api/time/tasks", {
        "title": "空格标签任务", "tags": ["工作 标签"], "revision": payload["revision"]})
    assert task.status_code == 200, task.text
    payload = board(client)
    assert find(payload, "空格标签任务")["tags"] == ["工作标签"]

    # 编辑颜色时使用界面返回的规范名；配置里的旧名字也要被认出来并写回规范名。
    updated = post(client, "/api/time/tags/update", {
        "name": "工作标签", "color": "#ff0000", "revision": payload["revision"]})
    assert updated.status_code == 200, updated.text
    payload = board(client)
    assert next(tag for tag in payload["tags"] if tag["name"] == "工作标签")["color"] == "#ff0000"

    deleted = post(client, "/api/time/tags/delete", {
        "name": "工作标签", "revision": payload["revision"]})
    assert deleted.status_code == 200 and deleted.json()["tasks"] == 1
    payload = board(client)
    assert all(tag["name"] != "工作标签" for tag in payload["tags"])
    assert find(payload, "空格标签任务")["tags"] == []
    text = config_file(tmp_path).read_text(encoding="utf-8")
    assert "工作 标签" not in text and "工作标签" not in text


def test_bulk_actions_only_touch_new_notes(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    for title in ("批量甲", "批量乙"):
        post(client, "/api/time/tasks", {"title": title, "revision": payload["revision"]})
        payload = board(client)

    keys = [find(payload, "批量甲")["key"], find(payload, "批量乙")["key"]]
    tagged = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "tag", "tags": ["批量", "重要"], "revision": payload["revision"]})
    assert tagged.status_code == 200, tagged.text
    payload = board(client)
    assert find(payload, "批量甲")["tags"] == ["批量", "重要"]

    keys = [find(payload, "批量甲")["key"], find(payload, "批量乙")["key"]]
    dated = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "date", "date": "2026-11-11", "revision": payload["revision"]})
    assert dated.status_code == 200
    payload = board(client)
    assert {find(payload, title)["date"] for title in ("批量甲", "批量乙")} == {"2026-11-11"}

    keys = [find(payload, title)["key"] for title in ("批量甲", "批量乙")]
    prioritized = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "priority", "priority": "low", "revision": payload["revision"]})
    assert prioritized.status_code == 200
    payload = board(client)
    assert {find(payload, title)["priority"] for title in ("批量甲", "批量乙")} == {"low"}

    keys = [find(payload, title)["key"] for title in ("批量甲", "批量乙")]
    pinned = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "pin", "pinned": True, "revision": payload["revision"]})
    assert pinned.status_code == 200
    payload = board(client)
    assert all(find(payload, title)["pinned"] for title in ("批量甲", "批量乙"))

    keys = [find(payload, title)["key"] for title in ("批量甲", "批量乙")]
    moved = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "move", "list_name": "心愿清单", "revision": payload["revision"]})
    assert moved.status_code == 200
    payload = board(client)
    assert {find(payload, title)["list_name"] for title in ("批量甲", "批量乙")} == {"心愿清单"}

    keys = [find(payload, title)["key"] for title in ("批量甲", "批量乙")]
    copied = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "duplicate", "revision": payload["revision"]})
    assert copied.status_code == 200 and copied.json()["count"] == 2
    payload = board(client)
    assert len([task for task in payload["tasks"] if task["title"] == "批量甲"]) == 2

    legacy = find(payload, "买菜", legacy=True)
    assert post(client, "/api/time/tasks/bulk", {
        "keys": [legacy["key"]], "action": "delete", "revision": payload["revision"]}).status_code == 409
    assert post(client, "/api/time/tasks/bulk", {
        "keys": keys + keys, "action": "delete", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "explode", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "priority", "revision": payload["revision"]}).status_code == 422
    assert post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "move", "list_name": "不存在", "revision": payload["revision"]}).status_code == 422

    payload = board(client)
    keys = [find(payload, "批量甲")["key"]]
    deleted = post(client, "/api/time/tasks/bulk", {
        "keys": keys, "action": "delete", "revision": payload["revision"]})
    assert deleted.status_code == 200


def test_read_only_and_audit(tmp_path):
    readonly = make_client(tmp_path, read_only=True)
    seed(tmp_path)
    payload = board(readonly)
    assert payload["read_only"] is True
    assert find(payload, "买菜", legacy=True)
    assert post(readonly, "/api/time/tasks", {
        "title": "新任务", "revision": payload["revision"]}).status_code == 409
    assert post(readonly, "/api/time/lists", {
        "name": "X", "revision": payload["revision"]}).status_code == 409

    client = make_client(tmp_path / "writable")
    payload = board(client)
    post(client, "/api/time/tasks", {"title": "审计任务", "revision": payload["revision"]})
    payload = board(client)
    task = find(payload, "审计任务")
    post(client, "/api/time/tasks/update", {
        "key": task["key"], "done": True, "revision": payload["revision"]})
    with Database(tmp_path / "writable" / "tasks.sqlite3").connect() as connection:
        events = [row[0] for row in connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id")]
    assert "task_added" in events and "task_updated" in events


def test_legacy_file_compatibility_and_no_auto_migration(tmp_path):
    client = make_client(tmp_path)
    legacy = seed(tmp_path, legacy_name=True)
    before = legacy.read_bytes()
    payload = board(client)
    assert payload["path"] == "00-System/任务.md"
    assert find(payload, "买菜", legacy=True)

    created = post(client, "/api/time/tasks", {
        "title": "旧文件里的新任务", "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    assert (inbox_dir(tmp_path) / "旧文件里的新任务.md").is_file()
    assert legacy.read_bytes() == before          # 不自动迁移、不改旧文件
    assert not config_file(tmp_path).exists()     # 也不凭空造新配置
    payload = board(client)
    assert find(payload, "旧文件里的新任务")["legacy"] is False
    assert find(payload, "买菜", legacy=True)


def test_synthetic_lists_and_warnings(tmp_path):
    client = make_client(tmp_path)
    root = note_dir(tmp_path)
    (root / "自由清单").mkdir(parents=True)
    (root / "自由清单" / "手工任务.md").write_text(
        "---\ntype: task\n日期: 2026-10-17\n---\n手工正文\n", encoding="utf-8")
    (root / "坏任务.md").write_text("---\ntype: task\n标题: [\n---\n", encoding="utf-8")
    (root / "随手记.md").write_text("---\ntype: note\n---\n不是任务\n", encoding="utf-8")

    payload = board(client)
    task = find(payload, "手工任务")
    assert task["legacy"] is False and task["list_name"] == "自由清单"
    entry = next(item for item in payload["lists"] if item["name"] == "自由清单")
    assert entry["synthetic"] is True
    assert any("坏任务.md" in warning["path"] for warning in payload["warnings"])
    assert all(item["title"] != "随手记" for item in payload["tasks"])

    # 用同名清单“收养”已有目录
    payload = board(client)
    adopted = post(client, "/api/time/lists", {
        "name": "自由清单", "revision": payload["revision"]})
    assert adopted.status_code == 200, adopted.text
    payload = board(client)
    assert next(item for item in payload["lists"] if item["name"] == "自由清单")["synthetic"] is False
    assert find(payload, "手工任务")["list_name"] == "自由清单"


def test_undefined_list_edit_registers_the_folder_first(tmp_path):
    """目录里在用的清单（配置缺定义）第一次编辑时补登记，右键菜单才不会点了就 422。"""
    client = make_client(tmp_path)
    root = note_dir(tmp_path)
    (root / "torchos项目管理").mkdir(parents=True)
    (root / "torchos项目管理" / "健康系统.md").write_text(
        "---\ntype: task\n日期: 2026-10-30\n---\n正文\n", encoding="utf-8")

    payload = board(client)
    entry = next(item for item in payload["lists"] if item["name"] == "torchos项目管理")
    assert entry["synthetic"] is True

    updated = post(client, "/api/time/lists/update", {
        "name": "torchos项目管理", "color": "#22c55e", "pinned": True,
        "revision": payload["revision"]})
    assert updated.status_code == 200, updated.text
    payload = board(client)
    entry = next(item for item in payload["lists"] if item["name"] == "torchos项目管理")
    assert entry["synthetic"] is False
    assert entry["color"] == "#22c55e" and entry["pinned"] is True
    assert find(payload, "健康系统")["list_name"] == "torchos项目管理"
    assert "torchos项目管理" in config_file(tmp_path).read_text(encoding="utf-8")


def test_undefined_list_can_be_renamed_copied_and_deleted(tmp_path):
    client = make_client(tmp_path)
    root = note_dir(tmp_path)
    for name in ("甲清单", "乙清单"):
        (root / name).mkdir(parents=True)
        (root / name / f"{name}任务.md").write_text(
            "---\ntype: task\n---\n正文\n", encoding="utf-8")

    # 改名：补登记 + 目录一起改名，任务跟着走。
    payload = board(client)
    renamed = post(client, "/api/time/lists/update", {
        "name": "甲清单", "new_name": "甲清单改名",
        "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert (root / "甲清单改名" / "甲清单任务.md").is_file()
    assert not (root / "甲清单").exists()

    # 复制：原清单一并登记，副本是普通清单。
    payload = board(client)
    copied = post(client, "/api/time/lists/duplicate", {
        "name": "乙清单", "scope": "all_keep", "revision": payload["revision"]})
    assert copied.status_code == 200, copied.text
    payload = board(client)
    assert next(item for item in payload["lists"] if item["name"] == "乙清单")["synthetic"] is False
    assert next(item for item in payload["lists"] if item["name"] == "乙清单 副本")["synthetic"] is False
    copies = [task["title"] for task in payload["tasks"] if task["list_name"] == "乙清单 副本"]
    assert copies == ["乙清单任务"]

    # 删除：目录进回收站，清单从看板消失（配置里本来就没有定义，不用改配置）。
    payload = board(client)
    deleted = post(client, "/api/time/lists/delete", {
        "name": "乙清单", "revision": payload["revision"]})
    assert deleted.status_code == 200, deleted.text
    assert not (root / "乙清单").exists()
    payload = board(client)
    assert all(item["name"] != "乙清单" for item in payload["lists"])


def test_broken_config_front_matter_blocks_writes_instead_of_dropping_lists(tmp_path):
    """坏 YAML 时读模型退化成空配置；写之前必须拦住，不能把清单定义整段抹掉。"""
    client = make_client(tmp_path)
    broken = seed(tmp_path, "---\ntype: tasks\n清单: [\n---\n")
    before = broken.read_bytes()

    payload = board(client)
    assert any(item["path"].endswith("任务（tasks）.md")
               and "front matter 解析失败" in item["reason"] for item in payload["warnings"])

    blocked = post(client, "/api/time/lists", {"name": "新清单"})
    assert blocked.status_code == 409, blocked.text
    assert "front matter 解析失败" in blocked.json()["detail"]
    assert broken.read_bytes() == before


def test_attach_list_dida_project_preserves_other_metadata(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    vault = tmp_path / "vault"
    database = Database(tmp_path / "tasks.sqlite3")
    assert tasks.attach_list_dida_project(vault, "心愿清单", "proj-abc", database) is True
    payload = board(client)
    entry = next(item for item in payload["lists"] if item["name"] == "心愿清单")
    assert entry["dida_project_id"] == "proj-abc"
    assert entry["pinned"] is True
    assert entry["color"] == "#8b5cf6"
    other = next(item for item in payload["lists"] if item["name"] == "客户A")
    assert other["folder"] == "工作"
    assert other["dida_project_id"] == ""


def test_attach_list_dida_project_missing_list_returns_false(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    vault = tmp_path / "vault"
    database = Database(tmp_path / "tasks.sqlite3")
    assert tasks.attach_list_dida_project(vault, "不存在的清单", "proj-abc", database) is False


def test_attach_list_dida_project_rejects_read_only(tmp_path):
    client = make_client(tmp_path, read_only=True)
    seed(tmp_path)
    vault = tmp_path / "vault"
    database = Database(tmp_path / "tasks.sqlite3")
    with pytest.raises(HTTPException):
        tasks.attach_list_dida_project(vault, "心愿清单", "proj-abc", database,
                                       read_only=True)


def _create_task(client, title):
    payload = board(client)
    response = post(client, "/api/time/tasks", {"title": title, "revision": payload["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_reorder_tasks_persists_manual_order(tmp_path):
    client = make_client(tmp_path)
    _create_task(client, "A")
    _create_task(client, "B")
    payload = board(client)
    a = find(payload, "A")
    b = find(payload, "B")
    moved = post(client, "/api/time/tasks/reorder", {
        "key": b["key"], "before": a["key"], "revision": payload["revision"],
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["reordered"] is True
    payload = board(client)
    assert find(payload, "B")["sort_order"] == 1000
    assert find(payload, "A")["sort_order"] is None

    moved = post(client, "/api/time/tasks/reorder", {
        "key": find(payload, "A")["key"], "before": find(payload, "B")["key"],
        "revision": payload["revision"],
    })
    assert moved.status_code == 200, moved.text
    payload = board(client)
    assert find(payload, "A")["sort_order"] == 0
    assert find(payload, "B")["sort_order"] == 1000


def test_reorder_without_existing_orders_assigns_even_spacing(tmp_path):
    client = make_client(tmp_path)
    _create_task(client, "A")
    _create_task(client, "B")
    _create_task(client, "C")
    payload = board(client)
    moved = post(client, "/api/time/tasks/reorder", {
        "key": find(payload, "A")["key"], "before": "", "revision": payload["revision"],
    })
    assert moved.status_code == 200, moved.text
    payload = board(client)
    assert find(payload, "B")["sort_order"] == 1000
    assert find(payload, "C")["sort_order"] == 2000
    assert find(payload, "A")["sort_order"] == 3000


def test_reorder_rejects_bad_anchor_and_stale_revision(tmp_path):
    client = make_client(tmp_path)
    created = _create_task(client, "A")
    payload = board(client)
    bad = post(client, "/api/time/tasks/reorder", {
        "key": created["task"]["key"], "before": "not-a-key", "revision": payload["revision"],
    })
    assert bad.status_code == 422
    missing = post(client, "/api/time/tasks/reorder", {
        "key": created["task"]["key"], "before": "f" * 64, "revision": payload["revision"],
    })
    assert missing.status_code == 422
    stale = post(client, "/api/time/tasks/reorder", {
        "key": created["task"]["key"], "before": "", "revision": "stale",
    })
    assert stale.status_code == 409


def test_reorder_is_rejected_in_read_only_vault(tmp_path):
    client = make_client(tmp_path)
    created = _create_task(client, "A")
    readonly = make_client(tmp_path, read_only=True)
    payload = board(readonly)
    blocked = post(readonly, "/api/time/tasks/reorder", {
        "key": created["task"]["key"], "before": "", "revision": payload["revision"],
    })
    assert blocked.status_code == 409


def test_duplicate_task_lands_before_original_in_manual_order(tmp_path):
    client = make_client(tmp_path)
    _create_task(client, "A")
    _create_task(client, "B")
    payload = board(client)
    moved = post(client, "/api/time/tasks/reorder", {
        "key": find(payload, "B")["key"], "before": find(payload, "A")["key"],
        "revision": payload["revision"],
    })
    assert moved.status_code == 200, moved.text
    payload = board(client)
    duplicated = post(client, "/api/time/tasks/duplicate", {
        "key": find(payload, "B")["key"], "revision": payload["revision"],
    })
    assert duplicated.status_code == 200, duplicated.text
    payload = board(client)
    rows = [item for item in payload["tasks"] if item["title"] == "B"]
    assert len(rows) == 2
    assert sorted(item["sort_order"] for item in rows) == [999.5, 1000]


def test_reorder_with_screen_order_preserves_the_visible_sequence(tmp_path):
    client = make_client(tmp_path)
    _create_task(client, "A")
    _create_task(client, "B")
    _create_task(client, "C")
    payload = board(client)
    wanted = [find(payload, "C")["key"], find(payload, "B")["key"], find(payload, "A")["key"]]
    seeded = post(client, "/api/time/tasks/reorder", {
        "key": wanted[0], "order": wanted, "revision": payload["revision"],
    })
    assert seeded.status_code == 200, seeded.text
    payload = board(client)
    assert find(payload, "C")["sort_order"] == 1000
    assert find(payload, "B")["sort_order"] == 2000
    assert find(payload, "A")["sort_order"] == 3000


def test_reorder_with_screen_order_rejects_duplicates_and_missing_moved_task(tmp_path):
    client = make_client(tmp_path)
    _create_task(client, "A")
    _create_task(client, "B")
    payload = board(client)
    a = find(payload, "A")
    b = find(payload, "B")
    duplicate = post(client, "/api/time/tasks/reorder", {
        "key": a["key"], "order": [a["key"], a["key"]], "revision": payload["revision"],
    })
    assert duplicate.status_code == 422
    missing = post(client, "/api/time/tasks/reorder", {
        "key": a["key"], "order": [b["key"]], "revision": payload["revision"],
    })
    assert missing.status_code == 422
