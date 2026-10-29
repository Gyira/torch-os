"""主 dsh/脚本给忆灵投递事件卡：只写队列，消费后移入已读。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backend.app import memory_hub
from backend.app.db import Database

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "memory_event.py"


def test_memory_event_cli_drops_a_card_and_hub_consumes_it(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault),
         "--kind", "decision", "--summary", "确认项目范围先做 MVP",
         "--source", "日记 2026-10-29"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    card = Path(result.stdout.strip())
    assert card.is_file()
    assert card.parent.name == "事件"

    database = Database(tmp_path / "hub.sqlite3")
    database.initialize()
    assert memory_hub.ingest_event_files(database, vault) == 1
    events = memory_hub.list_events(database)
    assert len(events) == 1
    assert "MVP" in events[0]["summary"]
    assert not card.exists()
    assert (vault / ".torch-os/忆灵/事件/已读" / card.name).is_file()


def test_memory_event_cli_rejects_bad_kind(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault),
         "--kind", "drop_table", "--summary", "x"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 2
