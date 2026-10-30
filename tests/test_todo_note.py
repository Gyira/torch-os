"""Markdown 待办（0.99.x）：`00_人生路/07_任务与待办/_系统/待办（todos）.md` 是数据本体——解析、外科写入。

钉子：只改目标行、其余字节不变；文件在别处改过就 409；只读一律 409 而读取 200；
每日必做勾选只写 `.torch-os/todos/daily.json`，**绝不写日记**；`daily/sync` 不依赖 collect_day（无递归）。
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app import workbench
from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()

SAMPLE = """---
type: todo
---

# 待办

## 待办
- [ ] 买菜
- [x] 写周报

```text
- [ ] 围栏里的不算
```

<!-- - [ ] 注释里的也不算 -->

## 每日必做
<!-- 每天都要做的事，一行一条；勾选状态记在当天日记里 -->
- [ ] 早起喝水
- [ ] 阅读 30 分钟
"""


def make_client(tmp_path, *, read_only=False):
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "todo.sqlite3"), tmp_path / "vault",
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def todos_file(tmp_path):
    return tmp_path / "vault" / "00_人生路" / "07_任务与待办" / "_系统" / "待办（todos）.md"


def journal_file(tmp_path, day=TODAY):
    return tmp_path / "vault" / "00_人生路" / "01_日记" / f"{day}.md"


def seed(tmp_path, text=SAMPLE):
    path = todos_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def todo_items(client):
    return client.get("/api/todos").json()["todos"]


def test_parsing_classifies_sections_and_skips_fences_and_comments(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    payload = client.get("/api/todos").json()
    assert [item["title"] for item in payload["todos"]] == ["买菜", "写周报"]
    assert [item["title"] for item in payload["daily_master"]] == ["早起喝水", "阅读 30 分钟"]
    assert payload["todos"][0]["checked"] is False and payload["todos"][1]["checked"] is True


def test_completed_suffix_is_read_and_stripped(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path, SAMPLE.replace("- [x] 写周报", "- [x] 写周报 ✅ 2026-10-04"))
    item = todo_items(client)[1]
    assert item["title"] == "写周报" and item["completed_on"] == "2026-10-04"


def test_add_is_surgical_and_tracks_revision(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    revision = client.get("/api/todos").json()["revision"]
    before = path.read_text(encoding="utf-8")

    created = client.post("/api/todos", json={"title": "新的一条", "revision": revision})
    assert created.status_code == 200, created.text
    after = path.read_text(encoding="utf-8")
    # 只有新增的那一行是变化；其余行原样。
    before_lines, after_lines = before.splitlines(), after.splitlines()
    assert len(after_lines) == len(before_lines) + 1
    index = after_lines.index("- [ ] 新的一条")
    assert after_lines[:index] == before_lines[:index]
    assert after_lines[index + 1:] == before_lines[index:]
    assert not (tmp_path / "vault" / "00_人生路" / "07_任务与待办" / "_系统" / "待办（todos）.md.tmp").exists()

    stale = client.post("/api/todos", json={"title": "再一条", "revision": revision})
    assert stale.status_code == 409


def test_toggle_appends_date_and_keeps_the_rest_of_the_line(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    item = todo_items(client)[0]

    ok = client.post("/api/todos/toggle", json={
        "key": item["key"], "line": item["line"], "done": True, "day": TODAY})
    assert ok.status_code == 200, ok.text
    line = [line for line in path.read_text(encoding="utf-8").splitlines() if "买菜" in line][0]
    assert line == f"- [x] 买菜 ✅ {TODAY}"

    undone = client.post("/api/todos/toggle", json={
        "key": todo_items(client)[0]["key"], "line": todo_items(client)[0]["line"], "done": False})
    assert undone.status_code == 200, undone.text
    line = [line for line in path.read_text(encoding="utf-8").splitlines() if "买菜" in line][0]
    assert line == "- [ ] 买菜"


def test_stale_key_and_missing_file_are_409(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)
    gone = client.post("/api/todos/toggle", json={"key": "0" * 64, "line": 1, "done": True})
    assert gone.status_code == 409

    (tmp_path / "empty").mkdir(exist_ok=True)
    no_file = make_client(tmp_path / "empty")
    missing = no_file.post("/api/todos", json={"title": "x", "revision": "deadbeefdeadbeef"})
    assert missing.status_code == 409
    created = no_file.post("/api/todos", json={"title": "x", "revision": ""})
    assert created.status_code == 200
    assert todos_file(tmp_path / "empty").is_file()


def test_delete_removes_exactly_one_line(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    item = todo_items(client)[1]
    response = client.post("/api/todos/delete", json={"key": item["key"], "line": item["line"]})
    assert response.status_code == 200, response.text
    text = path.read_text(encoding="utf-8")
    assert "写周报" not in text and "买菜" in text and "早起喝水" in text


def test_crlf_is_preserved(tmp_path):
    client = make_client(tmp_path)
    path = tmp_path / "vault" / "00_人生路" / "07_任务与待办" / "_系统" / "待办（todos）.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))
    item = todo_items(client)[0]
    ok = client.post("/api/todos/toggle", json={
        "key": item["key"], "line": item["line"], "done": True, "day": TODAY})
    assert ok.status_code == 200, ok.text
    raw = path.read_bytes()
    # 全文件仍是 CRLF：任何把 CRLF 去掉后的裸 \n 都不应存在。
    assert raw.count(b"\r\n") > 0 and raw.replace(b"\r\n", b"").count(b"\n") == 0
    assert f"买菜 ✅ {TODAY}".encode() in raw


def test_daily_check_never_writes_the_journal(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)

    synced = client.post("/api/todos/daily/sync", json={"day": TODAY})
    assert synced.status_code == 200, synced.text
    assert synced.json()["created_note"] is False and synced.json()["changed"] is False

    toggled = client.post("/api/todos/daily/toggle", json={
        "day": TODAY, "title": "早起喝水", "done": True})
    assert toggled.status_code == 200, toggled.text

    # 关键钉子：勾选每日必做不会建日记、也不改日记。
    assert not journal_file(tmp_path).exists()

    state = client.get(f"/api/todos/daily?day={TODAY}").json()
    assert [item["title"] for item in state["items"]] == ["早起喝水", "阅读 30 分钟"]
    assert state["items"][0]["done"] is True and state["items"][1]["done"] is False

    marks = json.loads(
        (tmp_path / "vault" / ".torch-os" / "todos" / "daily.json").read_text(encoding="utf-8"))
    assert marks[TODAY]["早起喝水"] is True

    # 取消勾选同样只动状态文件。
    client.post("/api/todos/daily/toggle", json={
        "day": TODAY, "title": "早起喝水", "done": False})
    assert client.get(f"/api/todos/daily?day={TODAY}").json()["items"][0]["done"] is False
    assert not journal_file(tmp_path).exists()


def test_daily_toggle_rejects_unknown_item(tmp_path):
    client = make_client(tmp_path)
    seed(tmp_path)

    unknown = client.post("/api/todos/daily/toggle", json={
        "day": TODAY, "title": "不存在的事", "done": True})
    assert unknown.status_code == 409
    assert not journal_file(tmp_path).exists()


def test_read_only_vault_rejects_writes_but_reads_finely(tmp_path):
    client = make_client(tmp_path, read_only=True)
    seed(tmp_path)
    assert client.get("/api/todos").status_code == 200
    assert client.get(f"/api/todos/daily?day={TODAY}").status_code == 200
    assert client.post("/api/todos", json={"title": "x", "revision": ""}).status_code == 409
    item = todo_items(client)[0]
    assert client.post("/api/todos/toggle", json={
        "key": item["key"], "line": item["line"], "done": True}).status_code == 409
    assert client.post("/api/todos/delete", json={"key": item["key"], "line": item["line"]}).status_code == 409
    assert client.post("/api/todos/daily/sync", json={"day": TODAY}).status_code == 409
    assert client.post("/api/todos/daily/toggle", json={
        "day": TODAY, "title": "早起喝水", "done": True}).status_code == 409
    assert not journal_file(tmp_path).exists()


def test_daily_sync_never_calls_collect_day(tmp_path, monkeypatch):
    """镜像不依赖日报聚合——collect_day 是 journal_note 的单向调用，反过来会成环。"""
    client = make_client(tmp_path)
    seed(tmp_path)

    def boom(*_args, **_kwargs):
        raise AssertionError("collect_day 不应被 daily/sync 触达")

    monkeypatch.setattr(workbench, "collect_day", boom)
    assert client.post("/api/todos/daily/sync", json={"day": TODAY}).status_code == 200


def test_workbench_reads_never_write_files(tmp_path):
    client = make_client(tmp_path)
    path = seed(tmp_path)
    journal = journal_file(tmp_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("# 我的手写日记\n", encoding="utf-8")
    before_todos = path.read_bytes()
    before_journal = journal.read_bytes()

    assert client.get("/api/workbench/today").status_code == 200
    assert client.get("/api/workbench/draft").status_code == 200
    assert path.read_bytes() == before_todos
    assert journal.read_bytes() == before_journal
