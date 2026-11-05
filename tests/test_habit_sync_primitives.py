"""HabitService 同步原语测试：missing、set_dida_links、apply_remote_entries。"""

from __future__ import annotations

from datetime import date

import pytest

from backend.app import habits
from backend.app.db import Database
from backend.app.habits import HabitCreate, HabitDelete, HabitUpdate, HabitService

TODAY = date(2026, 11, 5)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(habits, "shanghai_today", lambda: TODAY)
    vault = tmp_path / "vault"
    vault.mkdir()
    database = Database(tmp_path / "habits.sqlite3")
    return HabitService(database, vault), vault, database


def create(service, **overrides):
    payload = {"title": "读书", "start_date": "2026-11-01", **overrides}
    return service.create(HabitCreate(**payload))


def test_export_payload_includes_missing_flag(service, tmp_path):
    svc, vault, _database = service
    habit = create(svc)
    note = vault / habit["path"]
    note.unlink()
    svc._invalidate_snapshot()
    svc.sync(force=True)
    exported = svc.export_payload()["habits"]
    assert exported[0]["missing"] is True


def test_apply_remote_entries_bypasses_schedule_and_archive(service):
    svc, _vault, _database = service
    habit = create(svc, frequency={"kind": "weekly", "weekdays": [0]})
    # 2026-11-03 是周二，不在每周一的计划内；先归档也不能阻止导入。
    svc.update(habit["id"], HabitUpdate(revision=habit["revision"], status="archived"))
    result = svc.apply_remote_entries([{
        "habit_id": habit["id"], "date": "2026-11-03",
        "status": "done", "note": "远端补记",
    }])
    assert result["changed"] == 1
    entry = svc.entries(habit["id"], "2026-11-03", "2026-11-03")[0]
    assert entry["status"] == "done"
    assert entry["note"] == "远端补记"


def test_apply_remote_entries_rejects_future_and_expected_mismatch(service):
    svc, _vault, _database = service
    habit = create(svc)
    future = svc.apply_remote_entries([{
        "habit_id": habit["id"], "date": "2026-12-01", "status": "done",
    }])
    assert future["changed"] == 0
    assert future["skipped"][0]["reason"] == "invalid_date"
    first = svc.apply_remote_entries([{
        "habit_id": habit["id"], "date": "2026-11-03", "status": "done",
        "expected_status": "done",
    }])
    assert first["changed"] == 0
    assert first["skipped"][0]["reason"] == "local_changed"


def test_set_dida_links_preserves_body_unknown_keys_and_revision(service):
    svc, vault, _database = service
    habit = create(svc)
    note = vault / habit["path"]
    text = note.read_text(encoding="utf-8").replace(
        "status: active", "status: active\n自定义键: 保留")
    note.write_text(text + "\n正文内容\n", encoding="utf-8")
    svc._invalidate_snapshot()
    svc.sync(force=True)
    before = svc.get(habit["id"])
    result = svc.set_dida_links({habit["uid"]: "h" * 24})
    assert result["written"] == {habit["uid"]: "h" * 24}
    after = svc.get(habit["id"])
    assert after["revision"] == before["revision"]
    refreshed = note.read_text(encoding="utf-8")
    assert "dida_id: " + "h" * 24 in refreshed
    assert "自定义键: 保留" in refreshed
    assert "正文内容" in refreshed


def test_apply_remote_entries_supports_failed_status(service):
    svc, _vault, _database = service
    habit = create(svc)
    result = svc.apply_remote_entries([{
        "habit_id": habit["id"], "date": "2026-11-03", "status": "failed",
    }])
    assert result["changed"] == 1
    entry = svc.entries(habit["id"], "2026-11-03", "2026-11-03")[0]
    assert entry["status"] == "failed"


def test_apply_remote_entries_preserves_existing_note_when_note_is_none(service):
    svc, _vault, _database = service
    habit = create(svc)
    svc.save_entry(habit["id"], "2026-11-03",
                   habits.HabitEntryWrite(revision=0, status="done", note="本地备注"))
    result = svc.apply_remote_entries([{
        "habit_id": habit["id"], "date": "2026-11-03",
        "status": "skipped", "note": None, "expected_status": "done",
    }])
    assert result["changed"] == 1
    entry = svc.entries(habit["id"], "2026-11-03", "2026-11-03")[0]
    assert entry["status"] == "skipped" and entry["note"] == "本地备注"
