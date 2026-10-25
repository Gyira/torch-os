"""习惯写入后镜像到人生路的 Markdown（工作台接口保持不变）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def make_client(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault


def test_habit_definition_and_entry_are_mirrored_to_markdown(tmp_path):
    client, vault = make_client(tmp_path)
    habit = client.post("/api/habits", json={
        "title": "每天读一页", "minimum_action": "一页",
        "start_date": "2026-10-01", "weekdays": [0, 1, 2, 3, 4, 5, 6],
    })
    assert habit.status_code in (200, 201), habit.text
    habit_id = habit.json()["id"]
    directory = vault / "00_人生路" / "06_目标与计划" / "04_习惯追踪"
    definition = directory / "习惯定义.md"
    assert definition.is_file()
    assert "每天读一页" in definition.read_text(encoding="utf-8")

    saved = client.put(f"/api/habits/{habit_id}/entries/2026-10-21", json={
        "revision": 0, "status": "done", "note": "读完一章",
    })
    assert saved.status_code == 200, saved.text
    log = directory / "2026-10_打卡.md"
    assert log.is_file()
    text = log.read_text(encoding="utf-8")
    assert "2026-10-21" in text and "done" in text and "读完一章" in text
