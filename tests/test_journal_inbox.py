"""一个输入口：判类、拆卡、确认落盘。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import journal_inbox
from backend.app.application import create_app
from backend.app.db import Database

TODAY = date(2026, 10, 28)
DAY = TODAY.isoformat()
DIARY = Path("00_人生路", "01_日记", f"{DAY}.md")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_inbox, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    test_client = TestClient(app)
    test_client.get("/")
    return test_client, vault


MIXED = (
    "今天下午改了一版日记组件，跑了三遍测试。"
    "其实我不该在晚上改代码，脑子不清楚。"
    "原来把分类交给规则比交给模型稳。"
)


def test_classify_splits_one_paragraph_into_three_cards():
    cards = journal_inbox.classify(MIXED, DAY)
    assert [card["kind"] for card in cards] == ["diary", "reflection", "insight"]
    assert cards[0]["target"] == f"00_人生路/01_日记/{DAY}.md"
    assert "反思" in cards[1]["target"] or "02_" in cards[1]["target"]
    assert cards[1]["title"] and "不该" in cards[1]["body"]
    assert cards[1]["reason"] and cards[0]["confidence"] >= 0.5


def test_classify_falls_back_to_diary():
    cards = journal_inbox.classify("去了一趟超市，买了牛奶。", DAY)
    assert len(cards) == 1 and cards[0]["kind"] == "diary"


def test_classify_caps_at_three_cards():
    text = "今天做了三件事。其实我不该这样。原来如此。意识到一个问题。学到了两点。"
    assert len(journal_inbox.classify(text, DAY)) == 3


def test_save_diary_and_reflection(client):
    test_client, vault = client
    cards = test_client.post("/api/journal/inbox/classify",
                             json={"text": MIXED, "day": DAY}).json()["cards"]

    saved = test_client.post("/api/journal/inbox/save", json={
        "day": DAY, "cards": cards, "confirm": True})
    assert saved.status_code == 200, saved.text
    written = saved.json()["written"]
    assert [item["kind"] for item in written] == ["diary", "reflection", "insight"]

    diary = (vault / DIARY).read_text(encoding="utf-8")
    assert "今天下午改了一版日记组件" in diary
    assert "## 我想说的" in diary

    reflection = next(item for item in written if item["kind"] == "reflection")
    note = (vault / reflection["path"]).read_text(encoding="utf-8")
    assert note.startswith("---\ntype: reflection\n")
    assert "来源: torch 一个输入口" in note
    assert "不该在晚上改代码" in note


def test_save_requires_confirm_and_today(client):
    test_client, vault = client
    cards = [{"kind": "diary", "body": "今天写了一行。"}]
    assert test_client.post("/api/journal/inbox/save", json={
        "day": DAY, "cards": cards}).status_code == 400
    assert test_client.post("/api/journal/inbox/save", json={
        "day": "2026-10-27", "cards": cards, "confirm": True}).status_code == 409
    assert not (vault / DIARY).exists()


def test_save_is_read_only_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_inbox, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={"VAULT_READ_ONLY": "1"}, env_file=tmp_path / ".env")
    test_client = TestClient(app)
    test_client.get("/")

    response = test_client.post("/api/journal/inbox/save", json={
        "day": DAY, "cards": [{"kind": "diary", "body": "x"}], "confirm": True})
    assert response.status_code == 409
    assert not (vault / DIARY).exists()


def test_second_same_title_gets_a_suffix(client):
    test_client, vault = client
    card = {"kind": "reflection", "title": "值班不该硬扛", "body": "下次先叫备班。"}
    first = test_client.post("/api/journal/inbox/save", json={
        "day": DAY, "cards": [card], "confirm": True}).json()["written"][0]["path"]
    second = test_client.post("/api/journal/inbox/save", json={
        "day": DAY, "cards": [card], "confirm": True}).json()["written"][0]["path"]

    assert first != second and second.endswith("-2.md")
    assert (vault / first).is_file() and (vault / second).is_file()


def test_appending_to_existing_diary_keeps_earlier_text(client):
    test_client, vault = client
    path = vault / DIARY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntype: journal\ndate: 2026-10-28\n---\n\n# 10月28日 · 周三\n\n"
                    "## 我想说的\n\n先写的一句。\n\n## 下一步\n\n- 早睡\n", encoding="utf-8")

    test_client.post("/api/journal/inbox/save", json={
        "day": DAY, "cards": [{"kind": "diary", "body": "后面补的一句。"}], "confirm": True})

    content = path.read_text(encoding="utf-8")
    assert "先写的一句。" in content and "后面补的一句。" in content
    assert content.index("先写的一句") < content.index("后面补的一句")
    assert "## 下一步" in content and "早睡" in content
    assert list((vault / ".torch-os" / "backups" / "inbox").glob("*.md"))
