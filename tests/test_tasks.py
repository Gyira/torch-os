"""时间管理模块的任务本体（2026-10-08）：`00-System/任务.md` 的解析与外科写入。

钉子：只改目标行 / 文件在别处改过 409 / 只读 409 而读 200 / 标记解析不吞用户写的字 /
清单不存在 422 / 清单与标签定义来自 front-matter。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

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
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "tasks.sqlite3"), tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def tasks_file(tmp_path):
    return tmp_path / "vault" / "00-System" / "任务.md"


def seed(tmp_path, text=SAMPLE):
    path = tasks_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def board(client):
    response = client.get("/api/time/tasks")
    assert response.status_code == 200, response.text
    return response.json()


def find(payload, title):
    return next(task for task in payload["tasks"] if task["title"] == title)


def test_read_parses_markers_lists_and_tags(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    assert payload["exists"] is True
    assert [entry["name"] for entry in payload["lists"]] == ["收集箱", "心愿清单", "客户A"]
    assert next(e for e in payload["lists"] if e["name"] == "客户A")["folder"] == "工作"
    assert next(e for e in payload["lists"] if e["name"] == "心愿清单")["pinned"] is True
    assert [tag["name"] for tag in payload["tags"]] == ["重要"]

    shopping = find(payload, "买菜")
    assert shopping["list_name"] == "收集箱" and shopping["date"] == "2026-10-06"
    assert shopping["priority"] == "high" and shopping["tags"] == ["重要"]
    assert shopping["created"] == "2026-10-01" and shopping["done"] is False

    weekly = find(payload, "写周报")
    assert weekly["done"] is True and weekly["done_on"] == "2026-10-05"

    plain = find(payload, "没标记的任务")
    assert plain["priority"] == "none" and plain["tags"] == [] and plain["date"] == ""

    beach = find(payload, "去一次海边")
    assert beach["list_name"] == "心愿清单" and beach["pinned"] is True
    assert all(task["title"] != "围栏里的不算" for task in payload["tasks"])  # 围栏里的不算


def test_unknown_symbols_stay_in_the_title(tmp_path):
    """不认识的符号留在标题里，绝不吞用户写的字。"""
    client = make_client(tmp_path)
    seed(tmp_path, SAMPLE.replace("- [ ] 没标记的任务", "- [ ] 给 #1 issue 加个 🐛 标记"))
    payload = board(client)
    assert any(task["title"] == "给 #1 issue 加个 🐛 标记" for task in payload["tasks"])


def test_create_appends_to_the_section_top_and_keeps_the_rest(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    before = path.read_bytes()

    created = client.post("/api/time/tasks", json={
        "title": "写方案", "list_name": "收集箱", "date": TODAY, "priority": "medium",
        "tags": ["重要", "工作"], "revision": board(client)["revision"]})
    assert created.status_code == 200, created.text
    text = path.read_text(encoding="utf-8")
    assert "## 收集箱\n- [ ] 写方案 🔼 " in text
    assert f"📅 {TODAY}" in text and "#重要 #工作" in text and f"➕ {TODAY}" in text
    assert "## 心愿清单" in text and "去一次海边" in text       # 其它小节原样
    assert len(path.read_bytes()) > len(before)

    # 没写清单 → 落到收集箱；日期/优先级缺省 = 空/无
    client.post("/api/time/tasks", json={"title": "随手记一条", "revision": board(client)["revision"]})
    payload = board(client)
    assert find(payload, "随手记一条")["list_name"] == "收集箱"
    assert find(payload, "随手记一条")["priority"] == "none"


def test_create_makes_the_file_when_missing(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/time/tasks", json={"title": "第一条任务"})
    assert created.status_code == 200, created.text
    text = tasks_file(tmp_path).read_text(encoding="utf-8")
    assert "type: tasks" in text and "## 收集箱" in text and "第一条任务" in text
    assert board(client)["tasks"][0]["title"] == "第一条任务"


def test_update_only_touches_that_line(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    payload = board(client)
    task = find(payload, "买菜")

    other_line = [line for line in path.read_text(encoding="utf-8").splitlines()
                  if line == "- [ ] 没标记的任务"][0]

    updated = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "title": "买菜和水果",
        "date": "2026-10-09", "priority": "low", "tags": ["家务"],
        "revision": payload["revision"]})
    assert updated.status_code == 200, updated.text
    payload = board(client)
    changed = find(payload, "买菜和水果")
    assert changed["date"] == "2026-10-09" and changed["priority"] == "low"
    assert changed["tags"] == ["家务"] and changed["created"] == "2026-10-01"  # 创建日保留
    assert [line for line in path.read_text(encoding="utf-8").splitlines()
            if line == other_line]  # 别的行一个字节都没动


def test_toggle_stamps_and_clears_the_done_date(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    task = find(payload, "买菜")

    done = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "done": True, "revision": payload["revision"]})
    assert done.status_code == 200, done.text
    payload = board(client)
    task = find(payload, "买菜")
    assert task["done"] is True and task["done_on"] == TODAY
    assert f"✅ {TODAY}" in tasks_file(tmp_path).read_text(encoding="utf-8")

    undone = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "done": False, "revision": payload["revision"]})
    assert undone.status_code == 200, undone.text
    payload = board(client)
    assert find(payload, "买菜")["done_on"] == ""
    assert "✅" not in [line for line in tasks_file(tmp_path).read_text(encoding="utf-8").splitlines()
                        if "买菜" in line][0]


def test_move_between_lists_and_duplicate(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    task = find(payload, "买菜")

    moved = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "list_name": "心愿清单",
        "revision": payload["revision"]})
    assert moved.status_code == 200, moved.text
    assert find(board(client), "买菜")["list_name"] == "心愿清单"

    payload = board(client)
    task = find(payload, "买菜")
    copied = client.post("/api/time/tasks/duplicate", json={
        "key": task["key"], "line": task["line"], "revision": payload["revision"]})
    assert copied.status_code == 200, copied.text
    titles = [item["title"] for item in board(client)["tasks"]]
    assert titles.count("买菜") == 2
    clone = [item for item in board(client)["tasks"] if item["title"] == "买菜" and not item["done"]]
    # 副本插在清单最上面（新加的都排最前），所以 created=今天 的那条在前面
    assert len(clone) == 2 and clone[0]["created"] == TODAY
    assert clone[1]["created"] == "2026-10-01"  # 原任务不动


def test_delete_removes_only_that_line(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    task = find(payload, "没标记的任务")
    removed = client.post("/api/time/tasks/delete", json={
        "key": task["key"], "line": task["line"], "revision": payload["revision"]})
    assert removed.status_code == 200, removed.text
    titles = [item["title"] for item in board(client)["tasks"]]
    assert "没标记的任务" not in titles and "买菜" in titles


def test_conflicts_and_unknown_lists_are_rejected(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    payload = board(client)
    task = find(payload, "买菜")

    # 文件在别处被改过 → 409
    path.write_text(path.read_text(encoding="utf-8") + "\n- [ ] 别处加的\n", encoding="utf-8")
    stale = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "title": "改名",
        "revision": payload["revision"]})
    assert stale.status_code == 409

    # 未知清单 → 422；未知 key → 409；空标题 → 422
    fresh = board(client)
    bad_list = client.post("/api/time/tasks", json={
        "title": "x", "list_name": "不存在的清单", "revision": fresh["revision"]})
    assert bad_list.status_code == 422 and "清单" in bad_list.json()["detail"]
    ghost = client.post("/api/time/tasks/delete", json={"key": "0" * 64, "revision": fresh["revision"]})
    assert ghost.status_code == 409
    empty = client.post("/api/time/tasks", json={"title": "   ", "revision": fresh["revision"]})
    assert empty.status_code == 422


def test_read_only_vault_refuses_writes_but_still_reads(tmp_path):
    client = make_client(tmp_path, read_only=True)
    seed(tmp_path)
    assert board(client)["exists"] is True
    payload = board(client)
    task = find(payload, "买菜")
    blocked = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "title": "改名", "revision": payload["revision"]})
    assert blocked.status_code == 409
    assert client.post("/api/time/tasks", json={"title": "新任务", "revision": payload["revision"]}).status_code == 409


def test_writes_land_in_the_audit_log(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    task = find(payload, "买菜")
    client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "title": "买菜（改）", "revision": payload["revision"]})
    client.post("/api/time/tasks", json={"title": "新任务", "revision": board(client)["revision"]})
    with Database(tmp_path / "tasks.sqlite3").connect() as connection:
        events = [row[0] for row in connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id")]
    assert "task_updated" in events and "task_added" in events


def test_create_and_edit_lists(tmp_path):
    """清单：新增（可挂文件夹）、重命名（定义与小节标题一起改）、改色、置顶。"""
    client = make_client(tmp_path)
    seed(tmp_path)
    revision = board(client)["revision"]

    created = client.post("/api/time/lists", json={
        "name": "客户C", "color": "#ef4444", "folder": "工作", "revision": revision})
    assert created.status_code == 200, created.text
    payload = board(client)
    entry = next(item for item in payload["lists"] if item["name"] == "客户C")
    assert entry["color"] == "#ef4444" and entry["folder"] == "工作"
    assert payload["folders"] == ["工作"]
    assert "## 客户C" in tasks_file(tmp_path).read_text(encoding="utf-8")

    assert client.post("/api/time/lists", json={
        "name": "客户C", "revision": payload["revision"]}).status_code == 422

    renamed = client.post("/api/time/lists/update", json={
        "name": "客户C", "new_name": "客户D", "color": "#22c55e", "pinned": True,
        "folder": "", "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    payload = board(client)
    entry = next(item for item in payload["lists"] if item["name"] == "客户D")
    assert entry["color"] == "#22c55e" and entry["pinned"] is True and entry["folder"] == ""
    text = tasks_file(tmp_path).read_text(encoding="utf-8")
    assert "## 客户D" in text and "## 客户C" not in text


def test_duplicate_list_scopes_and_placement(tmp_path):
    """复制清单：三个范围；副本叫「XX 副本」并插在原清单上方（定义与正文都如此）。"""
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    task = find(payload, "没标记的任务")
    client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "done": True, "revision": payload["revision"]})

    payload = board(client)
    copied = client.post("/api/time/lists/duplicate", json={
        "name": "收集箱", "scope": "undone", "revision": payload["revision"]})
    assert copied.status_code == 200, copied.text
    payload = board(client)
    names = [item["name"] for item in payload["lists"]]
    assert names.index("收集箱 副本") < names.index("收集箱")  # 定义插在原清单前面
    copy_tasks = [item for item in payload["tasks"] if item["list_name"] == "收集箱 副本"]
    assert copy_tasks and all(item["done"] is False for item in copy_tasks)
    text = tasks_file(tmp_path).read_text(encoding="utf-8")
    # 比完整标题（带换行）：'## 收集箱' 是 '## 收集箱 副本' 的前缀，不比整行会假相等
    assert text.index("## 收集箱 副本\n") < text.index("## 收集箱\n")

    payload = board(client)
    client.post("/api/time/lists/duplicate", json={
        "name": "收集箱", "scope": "all_keep", "revision": payload["revision"]})
    kept = [item for item in board(client)["tasks"] if item["list_name"] == "收集箱 副本 2"]
    assert any(item["done"] for item in kept)

    payload = board(client)
    client.post("/api/time/lists/duplicate", json={
        "name": "收集箱", "scope": "all_reset", "revision": payload["revision"]})
    reset = [item for item in board(client)["tasks"] if item["list_name"] == "收集箱 副本 3"]
    assert reset and all(item["done"] is False and item["done_on"] == "" for item in reset)

    bad = client.post("/api/time/lists/duplicate", json={
        "name": "收集箱", "scope": "whatever", "revision": board(client)["revision"]})
    assert bad.status_code == 422


def test_delete_list_removes_its_tasks_but_never_the_inbox(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    client.post("/api/time/lists", json={"name": "临时清单", "revision": payload["revision"]})
    payload = board(client)
    client.post("/api/time/tasks", json={"title": "临时任务", "list_name": "临时清单",
                                        "revision": payload["revision"]})

    payload = board(client)
    removed = client.post("/api/time/lists/delete", json={
        "name": "临时清单", "revision": payload["revision"]})
    assert removed.status_code == 200 and removed.json()["tasks"] == 1
    payload = board(client)
    assert all(item["name"] != "临时清单" for item in payload["lists"])
    assert all(item["title"] != "临时任务" for item in payload["tasks"])
    assert "临时清单" not in tasks_file(tmp_path).read_text(encoding="utf-8")

    assert client.post("/api/time/lists/delete", json={
        "name": "收集箱", "revision": payload["revision"]}).status_code == 422


def test_folders_can_be_created_renamed_and_dissolved(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    client.post("/api/time/folders", json={"name": "工作", "revision": payload["revision"]})
    payload = board(client)
    client.post("/api/time/lists", json={"name": "客户D", "folder": "工作",
                                        "revision": payload["revision"]})

    payload = board(client)
    renamed = client.post("/api/time/folders/update", json={
        "name": "工作", "new_name": "事业", "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    payload = board(client)
    assert payload["folders"] == ["事业"]
    assert next(e for e in payload["lists"] if e["name"] == "客户D")["folder"] == "事业"

    dissolved = client.post("/api/time/folders/delete", json={
        "name": "事业", "revision": payload["revision"]})
    assert dissolved.status_code == 200
    payload = board(client)
    assert payload["folders"] == []
    assert next(e for e in payload["lists"] if e["name"] == "客户D")["folder"] == ""
    assert any(item["title"] for item in payload["tasks"])  # 清单没被连坐删掉


def test_tags_create_rename_and_delete_rewrite_task_lines(tmp_path):
    """标签：建定义、重命名同步改任务行里的 #旧名、删除把任务行里的标签去掉。"""
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    created = client.post("/api/time/tags", json={
        "name": "工作", "color": "#3b82f6", "revision": payload["revision"]})
    assert created.status_code == 200, created.text

    payload = board(client)
    renamed = client.post("/api/time/tags/update", json={
        "name": "工作", "new_name": "事业", "revision": payload["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert any(tag["name"] == "事业" for tag in board(client)["tags"])

    payload = board(client)
    updated = client.post("/api/time/tags/update", json={
        "name": "重要", "new_name": "要紧", "pinned": True, "revision": payload["revision"]})
    assert updated.status_code == 200 and updated.json()["tag"]["renamed_tasks"] == 1
    payload = board(client)
    assert find(payload, "买菜")["tags"] == ["要紧"]
    assert next(tag for tag in payload["tags"] if tag["name"] == "要紧")["pinned"] is True

    payload = board(client)
    deleted = client.post("/api/time/tags/delete", json={
        "name": "要紧", "revision": payload["revision"]})
    assert deleted.status_code == 200 and deleted.json()["tasks"] == 1
    payload = board(client)
    assert find(payload, "买菜")["tags"] == []
    assert all(tag["name"] != "要紧" for tag in payload["tags"])
    assert "#要紧" not in tasks_file(tmp_path).read_text(encoding="utf-8")


def test_structure_writes_respect_revision_and_read_only(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    assert client.post("/api/time/lists", json={
        "name": "X", "revision": "deadbeefdeadbeef"}).status_code == 409

    readonly = make_client(tmp_path / "ro", read_only=True)
    seed(tmp_path / "ro")
    payload = board(readonly)
    assert readonly.post("/api/time/lists", json={
        "name": "X", "revision": payload["revision"]}).status_code == 409
    assert readonly.post("/api/time/tags", json={
        "name": "X", "revision": payload["revision"]}).status_code == 409


def test_bulk_delete_move_and_tag(tmp_path):
    """批量：删除 / 移动到别的清单 / 打标签——一次写回处理多条。"""
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    keys = [find(payload, "买菜")["key"], find(payload, "没标记的任务")["key"]]

    tagged = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "tag", "tags": ["批量", "重要"], "revision": payload["revision"]})
    assert tagged.status_code == 200, tagged.text
    assert tagged.json()["count"] == 2
    payload = board(client)
    assert find(payload, "买菜")["tags"] == ["重要", "批量"]      # 原有标签保留，新的追加
    assert find(payload, "没标记的任务")["tags"] == ["批量", "重要"]  # 两个标签都打上（顺序按传参）

    # 键 = 行内容 + 行号算出来的：写过一次就变了，所以每次操作前重新取
    keys = [find(payload, "买菜")["key"], find(payload, "没标记的任务")["key"]]
    moved = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "move", "list_name": "心愿清单", "revision": payload["revision"]})
    assert moved.status_code == 200, moved.text
    payload = board(client)
    assert find(payload, "买菜")["list_name"] == "心愿清单"
    assert find(payload, "没标记的任务")["list_name"] == "心愿清单"
    assert find(payload, "写周报")["list_name"] == "收集箱"        # 没选的不动

    payload = board(client)
    keys = [find(payload, "买菜")["key"], find(payload, "没标记的任务")["key"]]
    deleted = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "delete", "revision": payload["revision"]})
    assert deleted.status_code == 200 and deleted.json()["count"] == 2
    titles = [task["title"] for task in board(client)["tasks"]]
    assert "买菜" not in titles and "没标记的任务" not in titles
    assert "写周报" in titles


def test_bulk_rejects_bad_actions_stale_keys_and_empty_tags(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    task = find(payload, "买菜")

    bad = client.post("/api/time/tasks/bulk", json={
        "keys": [task["key"]], "action": "explode", "revision": payload["revision"]})
    assert bad.status_code == 422
    empty = client.post("/api/time/tasks/bulk", json={
        "keys": [task["key"]], "action": "tag", "tags": [], "revision": payload["revision"]})
    assert empty.status_code == 422
    ghost = client.post("/api/time/tasks/bulk", json={
        "keys": ["0" * 64], "action": "delete", "revision": payload["revision"]})
    assert ghost.status_code == 409
    stale = client.post("/api/time/tasks/bulk", json={
        "keys": [task["key"]], "action": "delete", "revision": "deadbeefdeadbeef"})
    assert stale.status_code == 409
    unknown_list = client.post("/api/time/tasks/bulk", json={
        "keys": [task["key"]], "action": "move", "list_name": "没这个清单",
        "revision": payload["revision"]})
    assert unknown_list.status_code == 422


def test_bulk_date_priority_pin_and_duplicate(tmp_path):
    """批量：改日期 / 改优先级 / 置顶与取消置顶 / 创建副本。"""
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = board(client)
    keys = [find(payload, "买菜")["key"], find(payload, "去一次海边")["key"]]

    dated = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "date", "date": "2026-11-11", "revision": payload["revision"]})
    assert dated.status_code == 200, dated.text
    payload = board(client)
    assert find(payload, "买菜")["date"] == "2026-11-11"
    assert find(payload, "去一次海边")["date"] == "2026-11-11"
    assert find(payload, "写周报")["date"] == ""          # 没选的不动

    keys = [find(payload, "买菜")["key"], find(payload, "去一次海边")["key"]]
    cleared = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "date", "date": "", "revision": payload["revision"]})
    assert cleared.status_code == 200
    payload = board(client)
    assert find(payload, "买菜")["date"] == ""            # 空 = 清除

    keys = [find(payload, "买菜")["key"], find(payload, "写周报")["key"]]
    prioritized = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "priority", "priority": "medium", "revision": payload["revision"]})
    assert prioritized.status_code == 200
    payload = board(client)
    assert find(payload, "买菜")["priority"] == "medium"
    assert find(payload, "写周报")["priority"] == "medium"
    assert find(payload, "没标记的任务")["priority"] == "none"

    keys = [find(payload, "买菜")["key"]]
    pinned = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "pin", "pinned": True, "revision": payload["revision"]})
    assert pinned.status_code == 200
    payload = board(client)
    assert find(payload, "买菜")["pinned"] is True
    assert find(payload, "去一次海边")["pinned"] is True   # 它本来就置顶，没被连累

    keys = [find(payload, "买菜")["key"]]
    unpinned = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "pin", "pinned": False, "revision": payload["revision"]})
    assert unpinned.status_code == 200
    assert find(board(client), "买菜")["pinned"] is False

    payload = board(client)
    keys = [find(payload, "买菜")["key"], find(payload, "去一次海边")["key"]]
    copied = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "duplicate", "revision": payload["revision"]})
    assert copied.status_code == 200 and copied.json()["count"] == 2
    payload = board(client)
    assert [t["title"] for t in payload["tasks"]].count("买菜") == 2
    clones = [t for t in payload["tasks"] if t["title"] == "买菜" and not t["done"]]
    assert clones[0]["created"] == TODAY                  # 副本是新造的
    assert any(t["title"] == "去一次海边" and t["list_name"] == "心愿清单" for t in payload["tasks"])

    # 优先级缺参数要 422，别静悄悄清成"无"
    bad = client.post("/api/time/tasks/bulk", json={
        "keys": [find(payload, "买菜")["key"]], "action": "priority", "revision": payload["revision"]})
    assert bad.status_code == 422


def test_time_marker_parses_normalizes_and_stays_in_line(tmp_path):
    """⏰ 时间（2026-10-11，日历视图那一轮）：解析两种写法；认不出来的留在标题里。"""
    client = make_client(tmp_path)
    seed(tmp_path, SAMPLE
         .replace("- [x] 写周报 ✅ 2026-10-05 ➕ 2026-09-28",
                  "- [x] 写周报 ⏰ 09:00-10:30 ✅ 2026-10-05 ➕ 2026-09-28")
         .replace("- [ ] 没标记的任务", "- [ ] 没标记的任务\n- [ ] 晚上散步 ⏰ 9:05\n- [ ] 怪时间 ⏰ 25:99"))
    payload = board(client)
    assert find(payload, "写周报")["time"] == "09:00-10:30"
    assert find(payload, "晚上散步")["time"] == "09:05"          # 1 位小时补零
    assert find(payload, "买菜")["time"] == ""                   # 没写时间的还是空
    # 认不出来的时间不吞：整段留在标题里，行一个字节不动
    assert find(payload, "怪时间 ⏰ 25:99")["time"] == ""
    assert "- [ ] 怪时间 ⏰ 25:99" in tasks_file(tmp_path).read_text(encoding="utf-8")


def test_time_create_update_clear_and_validate(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    payload = board(client)

    created = client.post("/api/time/tasks", json={
        "title": "晨跑", "list_name": "收集箱", "date": TODAY, "time": "7:05",
        "revision": payload["revision"]})
    assert created.status_code == 200, created.text
    assert "📅 " + TODAY + " ⏰ 07:05" in path.read_text(encoding="utf-8")

    payload = board(client)
    task = find(payload, "晨跑")
    assert task["time"] == "07:05"

    # 改时间（区间）→ 只动这一行
    updated = client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "time": "07:05-08:00",
        "revision": payload["revision"]})
    assert updated.status_code == 200, updated.text
    payload = board(client)
    assert find(payload, "晨跑")["time"] == "07:05-08:00"
    assert "- [ ] 没标记的任务" in path.read_text(encoding="utf-8")   # 别的行没动

    # 只改日期不乱动时间（没传 time 的字段就不碰）
    task = find(payload, "晨跑")
    client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "date": "2026-10-20",
        "revision": payload["revision"]})
    assert find(board(client), "晨跑")["time"] == "07:05-08:00"

    # 清除时间：显式传空串
    payload = board(client)
    task = find(payload, "晨跑")
    client.post("/api/time/tasks/update", json={
        "key": task["key"], "line": task["line"], "time": "",
        "revision": payload["revision"]})
    assert find(board(client), "晨跑")["time"] == ""
    assert "⏰" not in [line for line in path.read_text(encoding="utf-8").splitlines()
                        if "晨跑" in line][0]

    # 非法写法 422
    payload = board(client)
    for bad in ["25:00", "14:60", "abc", "14:00-14:00", "15:00-14:00"]:
        response = client.post("/api/time/tasks", json={
            "title": "坏时间", "time": bad, "revision": payload["revision"]})
        assert response.status_code == 422, f"{bad} 应该 422：{response.text}"


def test_time_survives_duplicate_move_and_bulk(tmp_path):
    """时间字段在复制、移动、批量改写里都不能丢（这些路径都会重写整行）。"""
    client = make_client(tmp_path)
    seed(tmp_path, SAMPLE.replace("- [ ] 买菜 📅 2026-10-06 ⏫ #重要 ➕ 2026-10-01",
                                  "- [ ] 买菜 📅 2026-10-06 ⏰ 18:30 ⏫ #重要 ➕ 2026-10-01"))
    payload = board(client)
    task = find(payload, "买菜")
    assert task["time"] == "18:30"

    # 单个副本
    duplicated = client.post("/api/time/tasks/duplicate", json={
        "key": task["key"], "line": task["line"], "revision": payload["revision"]})
    assert duplicated.status_code == 200, duplicated.text
    payload = board(client)
    assert [t["time"] for t in payload["tasks"] if t["title"] == "买菜"] == ["18:30", "18:30"]

    # 批量置顶（会重写整行）
    keys = [t["key"] for t in payload["tasks"] if t["title"] == "买菜"]
    pinned = client.post("/api/time/tasks/bulk", json={
        "keys": keys, "action": "pin", "pinned": True, "revision": payload["revision"]})
    assert pinned.status_code == 200, pinned.text
    payload = board(client)
    assert [t["time"] for t in payload["tasks"] if t["title"] == "买菜"] == ["18:30", "18:30"]

    # 批量移动（跨清单重写）
    keys = [t["key"] for t in payload["tasks"] if t["title"] == "买菜"]
    moved = client.post("/api/time/tasks/bulk", json={
        "keys": keys[:1], "action": "move", "list_name": "心愿清单", "revision": payload["revision"]})
    assert moved.status_code == 200, moved.text
    payload = board(client)
    assert all(t["time"] == "18:30" for t in payload["tasks"] if t["title"] == "买菜")

    # 复制清单也带上时间
    copied = client.post("/api/time/lists/duplicate", json={
        "name": "收集箱", "scope": "all_keep", "revision": payload["revision"]})
    assert copied.status_code == 200, copied.text
    payload = board(client)
    clone = [t for t in payload["tasks"] if t["title"] == "买菜" and t["list_name"] == "收集箱 副本"]
    assert clone and clone[0]["time"] == "18:30"
