"""The workbench is one read of the day: todos (Markdown), habits, journal, progress, draft."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()
WEEKDAY = date.fromisoformat(TODAY).weekday()


def make_client(tmp_path):
    app = create_app(
        Database(tmp_path / "workbench.sqlite3"), tmp_path / "vault",
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def write_todo_note(tmp_path, *, todos=(), daily=()):
    """Seed 00-System/待办（todos）.md（数据本体）。todos 项为 (title, done, 完成日期|None)。"""
    lines = ["---", "type: todo", "---", "", "# 待办", "", "## 待办", ""]
    for title, done, on in todos:
        suffix = f" ✅ {on}" if done and on else ""
        lines.append(f"- [{'x' if done else ' '}] {title}{suffix}")
    lines += ["", "## 每日必做", ""]
    for title in daily:
        lines.append(f"- [ ] {title}")
    path = tmp_path / "vault" / "00-System" / "待办（todos）.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_today_payload_carries_greeting_identity_and_empty_state(tmp_path):
    client = make_client(tmp_path)
    payload = client.get("/api/workbench/today").json()
    assert payload["day"] == TODAY
    assert payload["greeting"] in {"早上好", "中午好", "下午好", "晚上好"}
    assert payload["name"] == "" and payload["motto"] == ""
    assert payload["todos"] == [] and payload["habits"] == []
    assert payload["journal"]["recorded"] is False
    # 日记本身也是一项：还没写就是 0/1。
    assert payload["progress"] == {"done": 0, "total": 1, "percent": 0}

    client.put("/api/identity", json={"name": "阿泽", "motto": "先完成，再完美", "worldview": "",
                                      "lifeview": "", "values": "", "notes": "", "revision": ""})
    named = client.get("/api/workbench/today").json()
    assert named["name"] == "阿泽" and named["motto"] == "先完成，再完美"


def test_progress_is_the_plain_average_of_every_item(tmp_path):
    client = make_client(tmp_path)
    write_todo_note(tmp_path, todos=[("第一件事", True, TODAY), ("第二件事", False, None)])

    payload = client.get("/api/workbench/today").json()
    assert [todo["title"] for todo in payload["todos"]] == ["第二件事", "第一件事"]
    # 两项待办（完成 1）+ 还没写日记 → 1/3
    assert payload["progress"] == {"done": 1, "total": 3, "percent": 33}

    # 补齐成 9 项待办：完成 8 项 → 8/(9+1) = 80%
    write_todo_note(tmp_path, todos=[("第一件事", True, TODAY), ("第二件事", False, None)]
                    + [(f"批量 {index}", True, TODAY) for index in range(7)])
    payload = client.get("/api/workbench/today").json()
    assert payload["progress"] == {"done": 8, "total": 10, "percent": 80}

    # 十项里完成九项 = 90%（用户给的口径）
    write_todo_note(tmp_path, todos=[("第一件事", True, TODAY), ("第二件事", True, TODAY)]
                    + [(f"批量 {index}", True, TODAY) for index in range(7)])
    payload = client.get("/api/workbench/today").json()
    assert payload["progress"] == {"done": 9, "total": 10, "percent": 90}


def test_habits_scheduled_today_join_the_loop_and_count_when_done(tmp_path):
    client = make_client(tmp_path)
    habit = client.post("/api/habits", json={
        "title": "睡前阅读", "minimum_action": "读一页", "weekdays": list(range(7)),
    }).json()
    off_today = (WEEKDAY + 1) % 7
    client.post("/api/habits", json={
        "title": "只在别的日子", "minimum_action": "动一下", "weekdays": [off_today],
    })

    payload = client.get("/api/workbench/today").json()
    assert [item["title"] for item in payload["habits"]] == ["睡前阅读"]
    assert payload["habits"][0]["planned"] is True and payload["habits"][0]["done"] is False
    # 没有当天记录时，工作台回传的必须是**条目**版本号 0——UI 直接拿它打卡。
    # （2026-10-05 修复：曾经回传习惯定义的版本号，第一次勾选就 409“当天记录已发生变化”。）
    assert payload["habits"][0]["revision"] == 0

    saved = client.put(f"/api/habits/{habit['id']}/entries/{TODAY}",
                       json={"revision": payload["habits"][0]["revision"], "status": "done"})
    assert saved.status_code in (200, 201), saved.text
    assert saved.json()["revision"] == 1
    payload = client.get("/api/workbench/today").json()
    assert payload["habits"][0]["done"] is True
    assert payload["habits"][0]["revision"] == 1
    assert payload["progress"] == {"done": 1, "total": 2, "percent": 50}

    # 再取消勾选：用工作台刚回传的新版本号同样应该成功；过期版本号仍被冲突保护挡住。
    undone = client.put(f"/api/habits/{habit['id']}/entries/{TODAY}",
                        json={"revision": payload["habits"][0]["revision"], "status": "unrecorded"})
    assert undone.status_code in (200, 201), undone.text
    stale = client.put(f"/api/habits/{habit['id']}/entries/{TODAY}",
                       json={"revision": 0, "status": "done"})
    assert stale.status_code == 409


def test_journal_completes_the_loop_and_keeps_the_new_template_fields(tmp_path):
    client = make_client(tmp_path)
    saved = client.put(f"/api/journal/{TODAY}", json={
        "revision": 0, "content": "今天把工作台做完了", "weather": "晴", "location": "家",
        "notes": "下午效率高", "reflection": "明天先做难的事", "next_action": "继续重构",
    })
    assert saved.status_code == 200, saved.text
    record = saved.json()
    assert record["weather"] == "晴" and record["location"] == "家"
    assert record["notes"] == "下午效率高" and record["reflection"] == "明天先做难的事"

    # 进度里的"日记"以**文件**为准（md-first）：今天日记存在才算完成闭环。
    note = tmp_path / "vault" / "02-Life" / "journal" / f"{TODAY}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# 今天的日记\n", encoding="utf-8")
    payload = client.get("/api/workbench/today").json()
    assert payload["journal"]["recorded"] is True
    assert payload["progress"] == {"done": 1, "total": 1, "percent": 100}


def test_draft_is_local_and_polish_falls_back_without_a_model(tmp_path):
    client = make_client(tmp_path)
    write_todo_note(tmp_path, todos=[("写完工作台", True, TODAY)], daily=["阅读 30 分钟"])

    draft = client.get("/api/workbench/draft").json()
    assert draft["mode"] == "local"
    assert "写完工作台" in draft["text"]
    assert draft["parts"]["done_todos"] == ["写完工作台"]
    assert draft["parts"]["open_daily"] == ["阅读 30 分钟"]
    assert "每日必做还差：阅读 30 分钟" in draft["text"]

    polished = client.post("/api/workbench/summarize", json={"day": TODAY, "use_model": True}).json()
    assert polished["mode"] == "local"
    assert "未配置云端模型" in polished["note"]
    assert "写完工作台" in polished["text"]

    empty = client.post("/api/workbench/summarize", json={"day": TODAY}).json()
    assert empty["mode"] == "local" and "note" not in empty


def test_the_todo_file_drives_the_day_list(tmp_path):
    """清单只看 00-System/待办（todos）.md：未完成 + 今天勾完的；更早勾完的不占列表；
    手动打勾（没有 ✅ 日期）视为今天完成；SQLite 任务不再上工作台。"""
    client = make_client(tmp_path)
    old_task = client.post("/api/tasks", json={"title": "只在目标页出现的旧任务"}).json()
    assert isinstance(old_task["id"], int)
    yesterday = (datetime.now(SHANGHAI).date() - timedelta(days=1)).isoformat()
    write_todo_note(tmp_path, todos=[
        ("很久以前就完成的", True, yesterday),
        ("手动打勾没有日期的", True, None),
        ("今天的待办", False, None),
    ])

    payload = client.get("/api/workbench/today").json()
    assert [todo["title"] for todo in payload["todos"]] == ["今天的待办", "手动打勾没有日期的"]
    # 文件是本体：更早完成的那条还在文件里，只是不进今日列表。
    text = (tmp_path / "vault" / "00-System" / "待办（todos）.md").read_text(encoding="utf-8")
    assert "很久以前就完成的" in text


def test_future_days_are_rejected(tmp_path):
    client = make_client(tmp_path)
    future = (datetime.now(SHANGHAI).date() + timedelta(days=1)).isoformat()
    assert client.get(f"/api/workbench/today?day={future}").status_code == 422
    assert client.get("/api/workbench/today?day=2026-13-01").status_code == 422
    assert client.post("/api/workbench/summarize", json={"day": future}).status_code == 422
