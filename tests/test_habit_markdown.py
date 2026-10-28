"""Habit Markdown storage is authoritative (2026-10-28)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.habits import HabitCreate, HabitEntryWrite, HabitService

HABIT_DIR = "00_人生路/06_目标与计划/04_习惯追踪"


def make_client(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault


def test_habit_note_monthly_log_and_index_are_written(tmp_path):
    client, vault = make_client(tmp_path)
    habit = client.post("/api/habits", json={
        "title": "每天读一页", "minimum_action": "一页",
        "start_date": "2026-10-01", "weekdays": list(range(7)),
    })
    assert habit.status_code in (200, 201), habit.text
    habit_data = habit.json()
    note = vault / habit_data["path"]
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert "type: habit" in text
    assert f"uid: {habit_data['uid']}" in text
    assert "title: 每天读一页" in text
    assert "frequency:" in text and "kind: daily" in text
    index = vault / HABIT_DIR / "习惯定义.md"
    assert index.is_file()
    assert "本文件由薪火OS自动生成" in index.read_text(encoding="utf-8")

    saved = client.put(f"/api/habits/{habit_data['id']}/entries/2026-10-21", json={
        "revision": 0, "status": "done", "note": "读完一章",
    })
    assert saved.status_code == 200, saved.text
    log = vault / HABIT_DIR / "打卡" / "2026-10.md"
    assert log.is_file()
    log_text = log.read_text(encoding="utf-8")
    assert "| 日期 | 习惯uid | 习惯 | 状态 | 备注 | 记录时间 |" in log_text
    assert "2026-10-21" in log_text and habit_data["uid"] in log_text
    assert "done" in log_text and "读完一章" in log_text


def test_external_note_and_log_edits_are_read_back(tmp_path):
    client, vault = make_client(tmp_path)
    habit = client.post("/api/habits", json={
        "title": "每天读一页", "minimum_action": "一页", "start_date": "2026-10-01",
    }).json()
    note = vault / habit["path"]
    note.write_text(note.read_text(encoding="utf-8").replace(
        "title: 每天读一页", "title: 每天读两页"), encoding="utf-8")
    assert client.get("/api/habits").json()[0]["title"] == "每天读两页"

    client.put(f"/api/habits/{habit['id']}/entries/2026-10-21", json={
        "revision": 0, "status": "done", "note": "",
    })
    log = vault / HABIT_DIR / "打卡" / "2026-10.md"
    log.write_text(log.read_text(encoding="utf-8").replace(
        "| done |", "| skipped |"), encoding="utf-8")
    entry = client.get(f"/api/habits/{habit['id']}/entries", params={
        "from_date": "2026-10-21", "to_date": "2026-10-21",
    }).json()[0]
    assert entry["status"] == "skipped"


def test_cache_migration_writes_all_habits_to_the_same_month(tmp_path):
    database = Database(tmp_path / "torch_os.sqlite3")
    cache = HabitService(database)
    first = cache.create(HabitCreate(title="甲", start_date="2026-09-01"))
    second = cache.create(HabitCreate(title="乙", start_date="2026-09-01"))
    cache.save_entry(first["id"], "2026-09-28",
                     HabitEntryWrite(revision=0, status="done", note="甲的记录"))
    cache.save_entry(second["id"], "2026-09-28",
                     HabitEntryWrite(revision=0, status="done", note="乙的记录"))

    vault = tmp_path / "vault"
    vault.mkdir()
    HabitService(database, vault)
    text = (vault / HABIT_DIR / "打卡" / "2026-09.md").read_text(encoding="utf-8")
    assert "甲的记录" in text and "乙的记录" in text
    assert (vault / HABIT_DIR / "习惯" / "甲.md").is_file()
    assert (vault / HABIT_DIR / "习惯" / "乙.md").is_file()


def test_legacy_mirror_is_read_but_left_in_place(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / HABIT_DIR
    folder.mkdir(parents=True)
    (folder / "习惯定义.md").write_text(
        "---\ntype: habit_definition\n---\n\n"
        "| id | 名称 | 触发提示 | 最低行动 | 开始日期 | 计划日 | 状态 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | 读书 | 早餐后 | 读一页 | 2026-09-28 | 每天 | active |\n",
        encoding="utf-8")
    legacy_log = folder / "2026-09_打卡.md"
    legacy_log.write_text(
        "---\ntype: habit_log\n---\n\n"
        "| 日期 | 习惯id | 状态 | 备注 |\n"
        "| --- | --- | --- | --- |\n"
        "| 2026-09-28 | 1 | done | 旧备注 |\n",
        encoding="utf-8")

    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    habits = client.get("/api/habits").json()
    assert [habit["title"] for habit in habits] == ["读书"]
    assert (folder / "习惯" / "读书.md").is_file()
    new_log = folder / "打卡" / "2026-09.md"
    assert new_log.is_file()
    assert "旧备注" in new_log.read_text(encoding="utf-8")
    # Migration copies/creates, never removes the old files.
    assert (folder / "习惯定义.md").is_file()
    assert legacy_log.is_file()
