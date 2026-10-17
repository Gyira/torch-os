"""滴答 → torch-os 单向同步的行为测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import task_notes
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.dida_client import (
    RemoteChecklistItem, RemoteProject, RemoteSnapshot, RemoteTask,
)
from backend.app.model_gateway import ModelGateway

COOKIE = "supersecret-token"


def make_project(**overrides):
    values = dict(id="proj-1", name="工作", color="#3b82f6", sort_order=0,
                  closed=False, view_mode="list", group_id="", etag="p1",
                  modified_time="2026-10-17T10:00:00+0000")
    values.update(overrides)
    return RemoteProject(**values)


def make_task(**overrides):
    values = dict(
        id="task-1", project_id="proj-1", title="买牛奶", content="<p>两盒</p>",
        due_date="2026-10-18T09:30:00+0800", start_date="2026-10-18T09:00:00+0800",
        is_all_day=False, is_floating=False, time_zone="Asia/Shanghai",
        priority=3, status=0, completed_time="", created_time="2026-10-17T10:00:00+0800",
        tags=("生活",), items=(RemoteChecklistItem(id="i1", title="低脂", status=0),),
        kind="TASK", etag="etag-1", modified_time="2026-10-17T10:00:00+0000",
        deleted=0, repeat_flag="", reminders=(), attachments=(), comment_count=0,
    )
    values.update(overrides)
    return RemoteTask(**values)


class FakeProvider:
    def __init__(self, snapshot, completed=(), deleted=None, restored=None):
        self.snapshot = snapshot
        self.completed = completed
        self.closed = False
        self.deleted = deleted if deleted is not None else []
        self.restored = restored if restored is not None else []

    async def fetch_snapshot(self, checkpoint=0):
        return self.snapshot

    async def fetch_completed(self):
        return tuple(self.completed)

    async def test_connection(self):
        return {"ok": True, "task_count": len(self.snapshot.tasks),
                "project_count": len(self.snapshot.projects), "latency_ms": 1}

    async def delete_tasks(self, entries):
        self.deleted.append(entries)
        return {"ok": True, "errors": {}}

    async def restore_tasks(self, entries):
        self.restored.append(entries)
        return {"ok": True, "etags": {}, "errors": {}}

    async def aclose(self):
        self.closed = True


def make_client(tmp_path: Path, snapshot: RemoteSnapshot, completed=(), *, read_only=False):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    seen = {"deleted": [], "restored": []}

    def factory(credentials):
        seen["cookie"] = credentials.cookie
        return FakeProvider(snapshot, completed, seen["deleted"], seen["restored"])

    app = create_app(
        Database(tmp_path / "dida.sqlite3"), vault,
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ=environ, env_file=tmp_path / ".env",
        dida_provider_factory=factory,
    )
    client = TestClient(app)
    client.get("/")
    return client, vault, seen


def configure(client: TestClient, *, enabled=False, csrf=False):
    cookie = f"t={COOKIE}" + ("; _csrf_token=csrf-secret" if csrf else "")
    response = client.put("/api/sync/dida/settings", json={
        "enabled": enabled,
        "region": "dida365",
        "interval_minutes": 15,
        "completed_days": 30,
        "timezone": "Asia/Shanghai",
        "device_version": 8000,
        "cookie": cookie,
    })
    assert response.status_code == 200, response.text
    return response.json()


def run_sync(client: TestClient, *, dry_run=False, force=False):
    response = client.post("/api/sync/dida/run", json={"dry_run": dry_run, "force": force})
    assert response.status_code == 200, response.text
    return response.json()


def scan(vault: Path):
    notes, warnings = task_notes.scan_task_notes(vault)
    return notes, warnings


def test_settings_mask_secrets_and_provider_gets_cookie(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, _vault, seen = make_client(tmp_path, snapshot)
    status = configure(client, csrf=True)
    assert status["cookie_set"] is True
    assert status["csrf_set"] is True
    text = client.get("/api/sync/dida/status").text
    assert COOKIE not in text
    assert "csrf-secret" not in text
    run_sync(client)
    assert seen["cookie"] == COOKIE


def test_dry_run_does_not_write(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    result = run_sync(client, dry_run=True)
    assert result["counts"]["created"] == 1
    assert not (vault / "00-System" / "任务").exists()


def test_sync_creates_note_and_is_idempotent(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    result = run_sync(client)
    assert result["counts"]["created"] == 1
    notes, _warnings = scan(vault)
    assert len(notes) == 1
    note = notes[0]
    assert note["dida_id"] == "task-1"
    assert note["list_name"] == "工作"
    assert note["title"] == "买牛奶"
    assert note["date"] == "2026-10-18"
    assert note["time"] == "09:00-09:30"
    assert note["priority"] == "medium"
    assert note["tags"] == ["生活"]
    body = task_notes.note_body(note)
    assert "两盒" in body
    assert "- [ ] 低脂" in body
    second = run_sync(client)
    assert second["counts"]["created"] == 0
    assert second["counts"]["unchanged"] == 1
    assert len(scan(vault)[0]) == 1


def test_conflict_keeps_local_and_force_overwrites(tmp_path):
    client, vault, _seen = make_client(
        tmp_path,
        RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                       projects=(make_project(),), tasks=(make_task(),)),
    )
    configure(client)
    run_sync(client)
    note = scan(vault)[0][0]
    note["path"].write_text(note["path"].read_text(encoding="utf-8") + "\n本地补充\n",
                            encoding="utf-8")

    updated = RemoteTask(**{
        **make_task().__dict__,
        "title": "买酸奶",
        "etag": "etag-2",
        "modified_time": "2026-10-17T11:00:00+0000",
    })
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        RemoteSnapshot(checkpoint=2, inbox_id="inbox-1",
                       projects=(make_project(),), tasks=(updated,))
    )
    result = run_sync(client)
    assert result["counts"]["conflicts"] == 1
    assert "本地补充" in note["path"].read_text(encoding="utf-8")
    conflicts = client.get("/api/sync/dida/conflicts").json()["conflicts"]
    assert conflicts and conflicts[0]["external_id"] == "task-1"

    forced = run_sync(client, force=True)
    assert forced["counts"]["updated"] == 1
    notes = scan(vault)[0]
    assert len(notes) == 1
    assert notes[0]["title"] == "买酸奶"
    assert "本地补充" in task_notes.note_body(notes[0])
    assert client.get("/api/sync/dida/conflicts").json()["conflicts"] == []


def test_completion_and_remote_delete_move_note_to_trash(tmp_path):
    done = RemoteTask(**{
        **make_task().__dict__,
        "status": 2,
        "completed_time": "2026-10-18T12:00:00+0800",
        "etag": "etag-2",
        "modified_time": "2026-10-18T12:00:00+0000",
    })
    client, vault, _seen = make_client(
        tmp_path,
        RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                       projects=(make_project(),), tasks=(make_task(),)),
        completed=(done,),
    )
    configure(client)
    result = run_sync(client)
    note = scan(vault)[0][0]
    assert note["done"] is True
    assert note["done_on"] == "2026-10-18"
    assert result["counts"]["completed"] == 1

    deleted = RemoteTask(**{
        **done.__dict__,
        "deleted": 1,
        "etag": "etag-3",
        "modified_time": "2026-10-18T13:00:00+0000",
    })
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        RemoteSnapshot(checkpoint=2, inbox_id="inbox-1",
                       projects=(make_project(),), tasks=(deleted,))
    )
    result = run_sync(client)
    assert result["counts"]["remote_deleted"] == 1
    assert not note["path"].exists()
    assert scan(vault)[0] == []
    trash = client.get("/api/time/trash").json()["items"]
    assert len(trash) == 1
    assert trash[0]["title"] == "买牛奶"
    assert trash[0]["dida_id"] == "task-1"
    # 同一条远端删除不会在每次同步时重复计数/移入。
    again = run_sync(client)
    assert again["counts"]["remote_deleted"] == 0
    assert again["counts"]["unchanged"] == 1


def test_remote_project_delete_moves_its_tasks_to_trash(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    run_sync(client)
    folder = vault / "00-System" / "任务" / "工作"
    assert folder.is_dir()

    # 手机上删除了整个清单：项目不再出现在完整的 projectProfiles 里，任务也不再返回。
    # 收集箱仍在，projectProfiles 非空，可以安全地做差集判断。
    inbox = make_project(id="inbox-1", name="收集箱")
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        RemoteSnapshot(checkpoint=2, inbox_id="inbox-1", projects=(inbox,), tasks=())
    )
    result = run_sync(client)
    assert result["counts"]["projects_deleted"] == 1
    assert result["counts"]["remote_deleted"] == 1
    assert not folder.exists()
    board = client.get("/api/time/tasks").json()
    assert [item["name"] for item in board["lists"]] == ["收集箱"]
    trash = client.get("/api/time/trash").json()["items"]
    assert len(trash) == 1
    assert trash[0]["title"] == "买牛奶"
    # 被清单删除带走的任务标记为远端删除，不再回写滴答。
    with sqlite3.connect(tmp_path / "dida.sqlite3") as connection:
        statuses = dict(connection.execute(
            "SELECT external_id, status FROM dida_task_links"
        ).fetchall())
        project_ids = [row[0] for row in connection.execute(
            "SELECT project_id FROM dida_project_links"
        )]
    assert statuses["task-1"] == "remote_deleted"
    assert "proj-1" not in project_ids

    again = run_sync(client)
    assert again["counts"]["projects_deleted"] == 0
    assert again["counts"]["errors"] == 0


def test_missing_project_profiles_never_deletes_local_lists(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    run_sync(client)
    folder = vault / "00-System" / "任务" / "工作"
    assert folder.is_dir()

    # 增量响应可能没有 projectProfiles；此时不能把“没列出来的项目”当成删除。
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        RemoteSnapshot(checkpoint=2, inbox_id="inbox-1", projects=(), tasks=(),
                       projects_complete=False)
    )
    result = run_sync(client)
    assert result["counts"]["projects_deleted"] == 0
    assert folder.is_dir()
    assert scan(vault)[0][0]["title"] == "买牛奶"

    # 即使标记为完整，空列表也视为可疑响应：不做批量删除。
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        RemoteSnapshot(checkpoint=3, inbox_id="inbox-1", projects=(), tasks=())
    )
    result = run_sync(client)
    assert result["counts"]["projects_deleted"] == 0
    assert folder.is_dir()


def test_local_update_preserves_dida_id_and_is_not_overwritten(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    task = next(item for item in board["tasks"] if item["title"] == "买牛奶")
    response = client.post("/api/time/tasks/update", json={
        "key": task["key"], "revision": board["revision"], "title": "本地改名",
        "list_name": "工作",
    })
    assert response.status_code == 200, response.text
    note = scan(vault)[0][0]
    assert note["title"] == "本地改名"
    assert note["dida_id"] == "task-1"
    result = run_sync(client)
    assert result["counts"]["local_edits"] == 1
    assert scan(vault)[0][0]["title"] == "本地改名"


def test_list_rename_keeps_project_mapping(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    entry = next(item for item in board["lists"] if item["name"] == "工作")
    assert entry["dida_project_id"] == "proj-1"
    response = client.post("/api/time/lists/update", json={
        "name": "工作", "new_name": "生活", "revision": board["revision"],
    })
    assert response.status_code == 200, response.text
    result = run_sync(client)
    assert result["counts"]["conflicts"] == 0
    assert result["counts"]["projects_created"] == 0
    notes = scan(vault)[0]
    assert len(notes) == 1
    assert notes[0]["list_name"] == "生活"
    assert not (vault / "00-System" / "任务" / "工作").exists()


def test_local_delete_goes_to_trash_and_only_purge_removes_file(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    task = board["tasks"][0]
    note_path = scan(vault)[0][0]["path"]
    response = client.post("/api/time/tasks/delete", json={
        "key": task["key"], "revision": board["revision"],
    })
    assert response.status_code == 200, response.text
    assert not note_path.exists()
    trash = client.get("/api/time/trash").json()["items"]
    assert len(trash) == 1
    assert trash[0]["title"] == "买牛奶"
    assert trash[0]["original_path"].endswith(".md")
    purged = client.post("/api/time/trash/delete",
                         json={"paths": [trash[0]["trash_path"]]})
    assert purged.status_code == 200, purged.text
    assert purged.json()["deleted"] == 1
    assert client.get("/api/time/trash").json()["items"] == []
    assert not (vault / trash[0]["trash_path"]).exists()


def test_trash_purge_rejects_paths_outside_the_trash(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, _vault, _seen = make_client(tmp_path, snapshot)
    configure(client)
    run_sync(client)
    response = client.post("/api/time/trash/delete", json={"paths": ["../../outside.md"]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted"] == 0
    assert payload["skipped"]


def test_local_delete_is_pushed_to_dida_once(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    task = board["tasks"][0]
    response = client.post("/api/time/tasks/delete", json={
        "key": task["key"], "revision": board["revision"],
    })
    assert response.status_code == 200, response.text
    result = run_sync(client)
    assert result["counts"]["deleted_pushed"] == 1
    assert seen["deleted"] == [[{"taskId": "task-1", "projectId": "proj-1"}]]
    assert client.get("/api/sync/dida/status").json()["last_push_error_code"] == ""
    again = run_sync(client)
    assert again["counts"]["deleted_pushed"] == 0
    assert len(seen["deleted"]) == 1


def test_purged_local_delete_is_still_pushed_to_dida(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    response = client.post("/api/time/tasks/delete", json={
        "key": board["tasks"][0]["key"], "revision": board["revision"],
    })
    assert response.status_code == 200, response.text

    # 用户在回写前清空了回收站：链接和远端任务仍在，补发删除不能丢。
    trash = client.get("/api/time/trash").json()["items"]
    assert len(trash) == 1
    purged = client.post("/api/time/trash/delete",
                         json={"paths": [trash[0]["trash_path"]]})
    assert purged.status_code == 200, purged.text
    assert purged.json()["deleted"] == 1

    result = run_sync(client)
    assert result["counts"]["deleted_pushed"] == 1
    assert seen["deleted"] == [[{"taskId": "task-1", "projectId": "proj-1"}]]
    assert client.get("/api/sync/dida/status").json()["last_push_error_code"] == ""

    again = run_sync(client)
    assert again["counts"]["deleted_pushed"] == 0
    assert len(seen["deleted"]) == 1


def test_local_restore_restores_the_dida_trash_copy(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/tasks/delete", json={
        "key": board["tasks"][0]["key"], "revision": board["revision"],
    })
    run_sync(client)
    assert seen["deleted"] == [[{"taskId": "task-1", "projectId": "proj-1"}]]

    trash = client.get("/api/time/trash").json()["items"]
    restored = client.post("/api/time/trash/restore",
                           json={"paths": [trash[0]["trash_path"]]})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == 1
    assert scan(vault)[0][0]["title"] == "买牛奶"

    remote = client.post("/api/sync/dida/restore", json={"external_ids": ["task-1"]})
    assert remote.status_code == 200, remote.text
    assert remote.json()["restored"] == 1
    assert seen["restored"] == [[{"taskId": "task-1", "projectId": "proj-1"}]]


def test_dida_restore_recreates_note_after_local_trash_purged(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/tasks/delete", json={
        "key": board["tasks"][0]["key"], "revision": board["revision"],
    })
    run_sync(client)  # 回写删除，链接变成 remote_deleted
    trash = client.get("/api/time/trash").json()["items"]
    client.post("/api/time/trash/delete", json={"paths": [trash[0]["trash_path"]]})
    assert scan(vault)[0] == []

    # 用户在滴答回收站里恢复任务：下一次同步应重新建立本地笔记。
    restored_snapshot = RemoteSnapshot(checkpoint=2, inbox_id="inbox-1",
                                       projects=(make_project(),), tasks=(make_task(),))
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        restored_snapshot
    )
    result = run_sync(client)
    assert result["counts"]["restored"] == 1
    notes = scan(vault)[0]
    assert len(notes) == 1
    assert notes[0]["title"] == "买牛奶"
    assert notes[0]["dida_id"] == "task-1"


def test_dida_restore_returns_trashed_note_to_its_folder(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/tasks/delete", json={
        "key": board["tasks"][0]["key"], "revision": board["revision"],
    })
    run_sync(client)  # 回写删除；本地笔记仍在回收站
    assert client.get("/api/time/trash").json()["items"]

    restored_snapshot = RemoteSnapshot(checkpoint=2, inbox_id="inbox-1",
                                       projects=(make_project(),), tasks=(make_task(),))
    client.app.state.dida_sync_service.provider_factory = lambda _creds: FakeProvider(
        restored_snapshot
    )
    result = run_sync(client)
    assert result["counts"]["restored"] == 1
    notes = scan(vault)[0]
    assert len(notes) == 1
    assert notes[0]["title"] == "买牛奶"
    assert notes[0]["list_name"] == "工作"
    assert client.get("/api/time/trash").json()["items"] == []


def test_moved_note_is_not_treated_as_local_delete(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    note = scan(vault)[0][0]
    inbox_copy = vault / "00-System" / "任务" / note["path"].name
    note["path"].rename(inbox_copy)

    result = run_sync(client)
    assert result["counts"]["deleted_pushed"] == 0
    assert seen["deleted"] == []


def test_push_delete_disabled_does_not_call_dida(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    settings = client.put("/api/sync/dida/settings", json={
        "enabled": False, "region": "dida365", "interval_minutes": 15,
        "completed_days": 30, "push_deletes": False, "timezone": "Asia/Shanghai",
        "device_version": 8000,
    })
    assert settings.status_code == 200, settings.text
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/tasks/delete", json={
        "key": board["tasks"][0]["key"], "revision": board["revision"],
    })
    run_sync(client)
    assert seen["deleted"] == []


def test_push_delete_without_csrf_reports_error(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=False)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/tasks/delete", json={
        "key": board["tasks"][0]["key"], "revision": board["revision"],
    })
    result = run_sync(client)
    assert result["counts"]["deleted_pushed"] == 0
    assert "push_delete_error" in result["warnings"]
    assert client.get("/api/sync/dida/status").json()["last_push_error_code"] == "csrf_required"
    assert seen["deleted"] == []


def test_read_only_blocks_run_but_allows_test(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, _seen = make_client(tmp_path, snapshot, read_only=True)
    configure(client)
    assert client.post("/api/sync/dida/test").status_code == 200
    response = client.post("/api/sync/dida/run", json={"dry_run": False})
    assert response.status_code == 409
    assert not (vault / "00-System" / "任务").exists()
