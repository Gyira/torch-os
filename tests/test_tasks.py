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
from backend.app.model_gateway import ModelGateway

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
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def config_file(tmp_path):
    return tmp_path / "vault" / "00-System" / "任务（tasks）.md"


def legacy_file(tmp_path):
    return tmp_path / "vault" / "00-System" / "任务.md"


def note_dir(tmp_path):
    return tmp_path / "vault" / "00-System" / "任务"


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
    assert payload["path"] == "00-System/任务（tasks）.md"
    assert payload["vault_name"] == "vault"
    assert payload["task_dir"] == "00-System/任务"
    shopping = find(payload, "买菜", legacy=True)
    assert shopping["path"] == "00-System/任务（tasks）.md"
    assert shopping["list_name"] == "收集箱" and shopping["date"] == "2026-10-06"
    assert shopping["priority"] == "high" and shopping["tags"] == ["重要"]
    assert find(payload, "写周报", legacy=True)["done"] is True
    assert all(task["title"] != "围栏里的不算" for task in payload["tasks"])

    created = post(client, "/api/time/tasks", {
        "title": "写方案", "date": TODAY, "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    body = created.json()["task"]
    assert body["legacy"] is False
    assert body["path"] == "00-System/任务/写方案.md"
    assert (note_dir(tmp_path) / "写方案.md").is_file()

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
    path = note_dir(tmp_path) / "第一条任务.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "type: task" in text
    assert f'日期: "{TODAY}"' in text and '时间: "07:05"' in text
    assert "优先级: medium" in text and "标签:" in text
    assert "标题" not in text

    payload = board(client)
    second = post(client, "/api/time/tasks", {"title": "第一条任务", "revision": payload["revision"]})
    assert second.status_code == 200
    assert (note_dir(tmp_path) / "第一条任务 2.md").is_file()
    payload = board(client)
    duplicates = [task for task in payload["tasks"] if task["title"] == "第一条任务"]
    assert len(duplicates) == 2

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
    path = note_dir(tmp_path) / "晨跑.md"
    path.write_text(path.read_text(encoding="utf-8") + "我的训练计划\n", encoding="utf-8")

    payload = board(client)
    task = find(payload, "晨跑")
    renamed = post(client, "/api/time/tasks/update", {
        "key": task["key"], "title": "晨跑训练", "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert not path.exists()
    renamed_path = note_dir(tmp_path) / "晨跑训练.md"
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


def test_duplicate_and_delete_are_soft(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    created = post(client, "/api/time/tasks", {"title": "写方案", "tags": ["重要"]})
    assert created.status_code == 200
    path = note_dir(tmp_path) / "写方案.md"
    path.write_text(path.read_text(encoding="utf-8") + "正文要留下\n", encoding="utf-8")
    payload = board(client)
    task = find(payload, "写方案")
    copied = post(client, "/api/time/tasks/duplicate", {
        "key": task["key"], "revision": payload["revision"]})
    assert copied.status_code == 200, copied.text
    copy_path = note_dir(tmp_path) / "写方案 2.md"
    assert copy_path.is_file() and "正文要留下" in copy_path.read_text(encoding="utf-8")
    payload = board(client)
    assert find(payload, "写方案", legacy=False)["created"] == TODAY

    copy = next(item for item in payload["tasks"] if item["path"] == "00-System/任务/写方案 2.md")
    removed = post(client, "/api/time/tasks/delete", {
        "key": copy["key"], "revision": payload["revision"]})
    assert removed.status_code == 200, removed.text
    assert not copy_path.exists()
    trash = list((tmp_path / "vault" / ".torch-os" / "task-trash").rglob("*.md"))
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
    blocked = post(client, "/api/time/lists/delete", {
        "name": "临时清单", "revision": payload["revision"]})
    assert blocked.status_code == 409

    (folder / "附件.txt").unlink()
    payload = board(client)
    removed = post(client, "/api/time/lists/delete", {
        "name": "临时清单", "revision": payload["revision"]})
    assert removed.status_code == 200, removed.text
    data = removed.json()
    assert data["tasks"] == 1 and data["trash"].startswith(".torch-os/task-trash/")
    assert not folder.exists()
    payload = board(client)
    assert all(item["name"] != "临时清单" for item in payload["lists"])
    assert all(task["title"] != "临时任务" for task in payload["tasks"])
    trash = tmp_path / "vault" / data["trash"]
    assert any(item.name == "任务（tasks）.md" for item in trash.rglob("*"))
    assert any(item.name == "临时任务.md" for item in trash.rglob("*"))

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
    note_text = (note_dir(tmp_path) / "新任务.md").read_text(encoding="utf-8")
    assert "要紧" in note_text and "重要" not in note_text

    payload = board(client)
    deleted = post(client, "/api/time/tags/delete", {
        "name": "要紧", "revision": payload["revision"]})
    assert deleted.status_code == 200 and deleted.json()["tasks"] == 2
    payload = board(client)
    assert find(payload, "新任务")["tags"] == []
    assert find(payload, "买菜", legacy=True)["tags"] == []
    assert "#要紧" not in config_file(tmp_path).read_text(encoding="utf-8")


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
    assert (note_dir(tmp_path) / "旧文件里的新任务.md").is_file()
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
