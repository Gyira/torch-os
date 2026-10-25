"""今天的笔记（0.98.0）：日记文件化——模板骨架 + 当天事实汇总，文件优先，只读拒绝。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(SHANGHAI).date().isoformat()
WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[date.fromisoformat(TODAY).weekday()]


def make_client(tmp_path, *, read_only=False):
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "journal-note.sqlite3"), tmp_path / "vault",
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client


def note_file(tmp_path):
    return tmp_path / "vault" / "00_人生路" / "01_日记" / f"{TODAY}.md"


def seed_day(client, tmp_path):
    """一条完成的待办（写在 00-System/待办（todos）.md）+ 一条习惯打卡，让自动汇总有事实可写。"""
    path = tmp_path / "vault" / "00-System" / "待办（todos）.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: todo\n---\n\n# 待办\n\n## 待办\n"
        f"- [x] 写下第一条记录 ✅ {TODAY}\n\n## 每日必做\n- [ ] 早起喝水\n",
        encoding="utf-8")
    habit = client.post("/api/habits", json={
        "title": "睡前阅读", "minimum_action": "读一页", "weekdays": list(range(7)),
    }).json()
    client.put(f"/api/habits/{habit['id']}/entries/{TODAY}", json={"revision": 0, "status": "done"})


def test_generate_creates_the_note_with_template_and_facts(tmp_path):
    client = make_client(tmp_path)
    seed_day(client, tmp_path)

    response = client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "create"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created"] is True and payload["exists"] is True
    assert payload["relpath"] == f"00_人生路/01_日记/{TODAY}.md"

    content = note_file(tmp_path).read_text(encoding="utf-8")
    week = date.fromisoformat(TODAY).isocalendar()[1]
    assert f"# {TODAY.replace('-', '.')}（第{week}周，{WEEKDAY}）" in content
    assert "每日三问" in content
    assert "## 自动汇总" in content
    assert "写下第一条记录" in content
    assert "每日必做：完成 0 / 1" in content
    assert "睡前阅读（已完成）" in content
    assert content == payload["content"]

    # 只留计数审计、不含正文。
    with Database(tmp_path / "journal-note.sqlite3").connect() as connection:
        rows = connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='journal_note_created' ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert rows and TODAY in rows[0]["detail_json"] and "睡前阅读" not in rows[0]["detail_json"]


def test_existing_note_is_never_overwritten_and_can_be_appended(tmp_path):
    client = make_client(tmp_path)
    path = note_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("# 我手写的今天\n\n这句是我自己写的。\n", encoding="utf-8")

    created = client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "create"})
    assert created.status_code == 200
    payload = created.json()
    assert payload["created"] is False and payload["exists"] is True
    assert "## 自动汇总" in payload["append_preview"]
    assert path.read_text(encoding="utf-8") == "# 我手写的今天\n\n这句是我自己写的。\n"

    appended = client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "append"})
    assert appended.status_code == 200, appended.text
    assert appended.json()["appended"] is True
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# 我手写的今天\n\n这句是我自己写的。\n")
    assert "## 自动汇总" in content


def test_read_returns_state_and_rejects_future_days(tmp_path):
    client = make_client(tmp_path)
    missing = client.get("/api/journal/note", params={"day": TODAY}).json()
    assert missing["exists"] is False and missing["content"] == ""
    assert missing["relpath"] == f"00_人生路/01_日记/{TODAY}.md"

    client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "create"})
    present = client.get("/api/journal/note", params={"day": TODAY}).json()
    assert present["exists"] is True and "每日三问" in present["content"]
    assert present["updated_at"]

    assert client.get("/api/journal/note", params={"day": "2099-01-01"}).status_code == 422
    assert client.get("/api/journal/note", params={"day": "not-a-day"}).status_code == 422
    assert client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "explode"}).status_code == 422


def test_read_only_vault_still_reads_but_refuses_writes(tmp_path):
    client = make_client(tmp_path, read_only=True)
    assert client.get("/api/journal/note", params={"day": TODAY}).json()["exists"] is False
    blocked = client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "create"})
    assert blocked.status_code == 409
    assert "只读" in blocked.json()["detail"]
    assert not note_file(tmp_path).exists()


def test_vault_template_file_overrides_the_builtin_skeleton(tmp_path):
    client = make_client(tmp_path)
    template = tmp_path / "vault" / "00-System" / "templates" / "日记模板（journal-template）.md"
    template.parent.mkdir(parents=True)
    template.write_text(
        "---\ntype: template\nname: 日记模板\n---\n\n# {{date}}（{{weekday}}）· 我的模板\n\n## 我的固定段落\n",
        encoding="utf-8",
    )

    client.post("/api/journal/note/generate", json={"day": TODAY, "mode": "create"})
    content = note_file(tmp_path).read_text(encoding="utf-8")
    # 模板自己的 front matter 不被继承，笔记固定用 journal 的。
    assert content.startswith(f"---\ntype: journal\ndate: {TODAY}\n---\n")
    assert "type: template" not in content and "name: 日记模板" not in content
    assert f"# {TODAY}（{WEEKDAY}）· 我的模板" in content
    assert "## 我的固定段落" in content
    assert "每日三问" not in content
