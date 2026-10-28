"""日记闭环：盘点 → 提问 → 草稿 → 写入。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import journal_review, journal_note
from backend.app.application import create_app
from backend.app.db import Database

TODAY = date(2026, 10, 28)
DAY = TODAY.isoformat()
JOURNAL_REL = Path("00_人生路", "01_日记", f"{DAY}.md")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_review, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    test_client = TestClient(app)
    test_client.get("/")
    return test_client, vault


def add_task(vault: Path, title: str, *, due: str | None = None,
             done_on: str | None = None) -> Path:
    folder = vault / "00_人生路" / "08_任务与待办" / "任务"
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: task"]
    if due:
        lines.append(f'日期: "{due}"')
    if done_on:
        lines.append("完成: true")
        lines.append(f'完成于: "{done_on}"')
    lines.extend(["---", "", f"# {title}", ""])
    path = folder / f"{title}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_digest_collects_overdue_and_todays_work(client):
    test_client, vault = client
    add_task(vault, "背单词", due="2026-10-22")
    add_task(vault, "写完周报", due=DAY, done_on=DAY)

    digest = test_client.get(f"/api/journal/digest?day={DAY}").json()

    assert digest["weekday"] == "周三"
    assert [item["title"] for item in digest["facts"]["tasks"]["completed"]] == ["写完周报"]
    assert [item["title"] for item in digest["facts"]["tasks"]["overdue"]] == ["背单词"]
    assert digest["facts"]["tasks"]["overdue"][0]["days"] == 6
    assert digest["headline"]
    assert any("背单词" in line for line in digest["signals"])


def test_questions_ask_about_overdue_before_empty_day(client):
    test_client, vault = client
    add_task(vault, "背单词", due="2026-10-22")

    payload = test_client.get(f"/api/journal/questions?day={DAY}").json()

    assert len(payload["questions"]) <= 3
    first = payload["questions"][0]
    assert first["kind"] == "overdue"
    assert "背单词" in first["prompt"]
    assert first["why"] and first["evidence"].endswith("背单词.md")


def test_compose_drops_empty_sections_and_keeps_next_step(client):
    test_client, vault = client
    add_task(vault, "写完周报", due=DAY, done_on=DAY)

    response = test_client.post("/api/journal/compose", json={"day": DAY, "next_step": "明天只背二十个词"})
    assert response.status_code == 200, response.text
    content = response.json()["content"]

    assert content.startswith("---\ntype: journal\n")
    assert "status: draft" in content
    assert "# 10月28日 · 周三" in content
    assert "## 今天做了什么" in content
    assert "## 下一步" in content and "- 明天只背二十个词" in content
    # 没写的段落不出现，也不再有 49 行空模板的占位符。
    assert "## 我想说的" not in content
    assert "## 反思" not in content
    assert "____" not in content
    assert "每日必做" not in content


def test_compose_puts_answers_in_collapsed_callout(client):
    test_client, _ = client
    response = test_client.post("/api/journal/compose", json={
        "day": DAY,
        "free_text": "今天有点累，但把日记这事想清楚了。",
        "answers": [{"prompt": "背单词还做吗？", "answer": "先拿掉，等考研线下一轮。"}],
        "summary": "把日记从填表改成对话",
        "mood": "清醒",
    })
    assert response.status_code == 200, response.text
    content = response.json()["content"]

    assert "## 我想说的" in content
    assert "> [!question]- 今天问过的" in content
    assert "> **背单词还做吗？**" in content
    assert "> 先拿掉，等考研线下一轮。" in content
    assert "summary: 把日记从填表改成对话" in content
    assert "mood: 清醒" in content


def test_compose_refuses_empty_day(client):
    test_client, _ = client
    response = test_client.post("/api/journal/compose", json={"day": DAY})
    assert response.status_code == 400
    assert "没有任何内容" in response.json()["detail"]


def test_write_only_today_and_only_with_confirm(client):
    test_client, vault = client
    content = test_client.post("/api/journal/compose", json={
        "day": DAY, "next_step": "先只背二十个词"}).json()["content"]

    assert test_client.post("/api/journal/write", json={
        "day": DAY, "content": content}).status_code == 400
    assert test_client.post("/api/journal/write", json={
        "day": "2026-10-27", "content": content, "confirm": True}).status_code == 409

    ok = test_client.post("/api/journal/write", json={
        "day": DAY, "content": content, "confirm": True})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["created"] is True and body["backup"] == ""
    written = vault / JOURNAL_REL
    assert written.read_text(encoding="utf-8").startswith("---\ntype: journal\n")
    # 写出来的就是成品：没有额外追加模板段落。
    assert "## 每日必做" not in written.read_text(encoding="utf-8")


def test_write_rejects_stale_preview_and_keeps_backup(client):
    test_client, vault = client
    first = test_client.post("/api/journal/compose", json={
        "day": DAY, "free_text": "第一版"}).json()
    wrote = test_client.post("/api/journal/write", json={
        "day": DAY, "content": first["content"], "confirm": True}).json()

    stale = test_client.post("/api/journal/write", json={
        "day": DAY, "content": first["content"], "expected_sha": "deadbeef", "confirm": True})
    assert stale.status_code == 409

    second = test_client.post("/api/journal/compose", json={
        "day": DAY, "free_text": "第二版"}).json()
    again = test_client.post("/api/journal/write", json={
        "day": DAY, "content": second["content"],
        "expected_sha": wrote["sha256"], "confirm": True})
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    backup = Path(again.json()["backup"])
    assert backup.is_file() and "第一版" in backup.read_text(encoding="utf-8")
    assert "第二版" in (vault / JOURNAL_REL).read_text(encoding="utf-8")


def test_read_only_vault_refuses_journal_write(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_review, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={"VAULT_READ_ONLY": "1"}, env_file=tmp_path / ".env")
    test_client = TestClient(app)
    test_client.get("/")

    content = "---\ntype: journal\ndate: 2026-10-28\n---\n\n# 10月28日 · 周三\n"
    response = test_client.post("/api/journal/write", json={
        "day": DAY, "content": content, "confirm": True})
    assert response.status_code == 409
    assert not (vault / JOURNAL_REL).exists()


def test_review_reads_existing_note_and_sha(client):
    test_client, vault = client
    path = vault / JOURNAL_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: journal\ndate: 2026-10-28\nstatus: sealed\n---\n\n# 手写\n",
                    encoding="utf-8")

    payload = test_client.get(f"/api/journal/review?day={DAY}").json()
    assert payload["note"]["exists"] is True
    assert payload["note"]["sealed"] is True
    assert len(payload["note"]["sha256"]) == 64
    assert payload["digest"]["facts"]["journal"]["sealed"] is True
    assert isinstance(payload["questions"], list)
    assert payload["digest"]["facts"]["quiet_days"] == 30


def test_journal_note_still_reads_the_same_file(client):
    """旧的读取接口保持兼容：写入之后 /api/journal/note 能读到。"""
    test_client, _ = client
    content = test_client.post("/api/journal/compose", json={
        "day": DAY, "free_text": "一句话"}).json()["content"]
    test_client.post("/api/journal/write", json={
        "day": DAY, "content": content, "confirm": True})

    note = test_client.get(f"/api/journal/note?day={DAY}").json()
    assert note["exists"] is True
    assert "一句话" in note["content"]
    assert journal_note.next_action_text(note["content"]) == ""


def test_weekly_reads_last_seven_days_and_prefills_review(client):
    test_client, vault = client
    folder = vault / "00_人生路" / "01_日记"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-10-24.md").write_text(
        "---\ntype: journal\ndate: 2026-10-24\n---\n\n# 10月24日\n\n"
        "## 我想说的\n\n周六去了图书馆，借了两本书。\n\n## 下一步\n- 把书翻完\n",
        encoding="utf-8")

    payload = test_client.get(f"/api/journal/weekly?day={DAY}").json()

    assert payload["from"] == "2026-10-22" and payload["to"] == "2026-10-28"
    assert len(payload["days"]) == 7
    assert payload["written"] == 1
    markdown = payload["markdown"]
    assert markdown.startswith("---\ntype: weekly_review\n")
    assert "周六去了图书馆" in markdown
    assert "## 三个问题" in markdown
    assert "## 下周一件大事" in markdown


def test_distill_turns_a_diary_paragraph_into_a_reflection_note(client):
    test_client, _ = client
    content = test_client.post("/api/journal/compose", json={
        "day": DAY,
        "free_text": "今天发现一个规律：我总是在晚上才想起背单词。下次把它挪到早上试试。",
    }).json()["content"]
    test_client.post("/api/journal/write", json={
        "day": DAY, "content": content, "confirm": True})

    payload = test_client.get(f"/api/journal/distill?day={DAY}").json()
    assert payload["candidates"], payload
    first = payload["candidates"][0]
    assert first["kind"] == "reflection"
    assert "02_反思复盘" in first["path"]
    assert first["path"].endswith(".md") and " " not in first["title"]
    assert "type: reflection" in first["markdown"]
    assert f"来源: 日记 {DAY}" in first["markdown"]
    assert "今天发现一个规律" in first["markdown"]


def test_distill_is_empty_without_a_diary(client):
    test_client, _ = client
    assert test_client.get(f"/api/journal/distill?day={DAY}").json()["candidates"] == []
