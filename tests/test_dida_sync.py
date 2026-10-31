"""滴答 → torch-os 单向同步的行为测试。"""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import task_notes
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.dida_client import (
    RemoteChecklistItem, RemoteProject, RemoteSnapshot, RemoteTask,
)
from backend.app.dida_sync import DidaSyncService

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
    values["raw"] = {
        "id": values["id"], "projectId": values["project_id"], "title": values["title"],
        "content": values["content"], "dueDate": values["due_date"],
        "startDate": values["start_date"], "isAllDay": values["is_all_day"],
        "isFloating": values["is_floating"], "timeZone": values["time_zone"],
        "priority": values["priority"], "status": values["status"],
        "completedTime": values["completed_time"], "createdTime": values["created_time"],
        "tags": list(values["tags"]), "items": [
            {"id": item.id, "title": item.title, "status": item.status}
            for item in values["items"]
        ],
        "kind": values["kind"], "etag": values["etag"],
        "modifiedTime": values["modified_time"], "repeatFlag": values["repeat_flag"],
        "reminders": list(values["reminders"]), "attachments": list(values["attachments"]),
        "commentCount": values["comment_count"],
    }
    return RemoteTask(**values)


class FakeProvider:
    def __init__(self, snapshot, completed=(), deleted=None, restored=None,
                 added=None, updated=None, moved=None,
                 projects_added=None, projects_updated=None, projects_deleted=None):
        self.snapshot = snapshot
        self.completed = completed
        self.closed = False
        self.deleted = deleted if deleted is not None else []
        self.restored = restored if restored is not None else []
        self.added = added if added is not None else []
        self.updated = updated if updated is not None else []
        self.moved = moved if moved is not None else []
        self.projects_added = projects_added if projects_added is not None else []
        self.projects_updated = projects_updated if projects_updated is not None else []
        self.projects_deleted = projects_deleted if projects_deleted is not None else []
        self.project_errors_by_name: dict[str, str] = {}
        self.project_no_etag: set[str] = set()
        self.project_etags = True

    async def fetch_snapshot(self, checkpoint=0):
        return self.snapshot

    async def fetch_completed(self):
        return tuple(self.completed)

    async def test_connection(self):
        return {"ok": True, "task_count": len(self.snapshot.tasks),
                "project_count": len(self.snapshot.projects), "latency_ms": 1}

    async def add_tasks(self, tasks):
        self.added.append(copy.deepcopy(tasks))
        return {"ok": True, "etags": {t["id"]: "etag-new" for t in tasks}, "errors": {}}

    async def update_tasks(self, tasks):
        self.updated.append(copy.deepcopy(tasks))
        return {"ok": True, "etags": {t["id"]: "etag-upd" for t in tasks}, "errors": {}}

    async def move_tasks(self, entries):
        self.moved.append(copy.deepcopy(entries))
        return {"ok": True, "etags": {}, "errors": {}}

    async def delete_tasks(self, entries):
        self.deleted.append(entries)
        return {"ok": True, "errors": {}}

    async def restore_tasks(self, entries):
        self.restored.append(entries)
        return {"ok": True, "etags": {}, "errors": {}}

    async def add_projects(self, projects):
        self.projects_added.append(copy.deepcopy(projects))
        etags = {}
        errors = {}
        for project in projects:
            code = self.project_errors_by_name.get(project.get("name", ""))
            if code:
                errors[project["id"]] = code
            elif self.project_etags and project["id"] not in self.project_no_etag:
                etags[project["id"]] = "etag-project"
        return {"ok": True, "etags": etags, "errors": errors}

    async def update_projects(self, projects):
        self.projects_updated.append(copy.deepcopy(projects))
        etags = {}
        errors = {}
        for project in projects:
            code = self.project_errors_by_name.get(project.get("name", ""))
            if code:
                errors[project["id"]] = code
            elif project["id"] not in self.project_no_etag:
                etags[project["id"]] = "etag-project-upd"
        return {"ok": True, "etags": etags, "errors": errors}

    async def delete_projects(self, project_ids):
        self.projects_deleted.append(list(project_ids))
        return {"ok": True, "etags": {}, "errors": {}}

    async def aclose(self):
        self.closed = True


def make_client(tmp_path: Path, snapshot: RemoteSnapshot, completed=(), *, read_only=False,
                auto_push: bool | None = None):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    seen = {"snapshot": snapshot,
            "deleted": [], "restored": [], "added": [], "updated": [], "moved": [],
            "projects_added": [], "projects_updated": [], "projects_deleted": [],
            "project_errors_by_name": {}, "project_no_etag": set(),
            "project_etags": True}

    def factory(credentials):
        seen["cookie"] = credentials.cookie
        provider = FakeProvider(
            seen["snapshot"], completed, seen["deleted"], seen["restored"],
            seen["added"], seen["updated"], seen["moved"],
            seen["projects_added"], seen["projects_updated"],
            seen["projects_deleted"],
        )
        provider.project_errors_by_name = seen["project_errors_by_name"]
        provider.project_no_etag = seen["project_no_etag"]
        provider.project_etags = seen["project_etags"]
        return provider

    app = create_app(
        Database(tmp_path / "dida.sqlite3"), vault,
        security_environ=environ, env_file=tmp_path / ".env",
        dida_provider_factory=factory,
        enable_dida_auto_push=auto_push,
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
    assert not (vault / "02_生活运营" / "00_待办事项").exists()


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
    folder = vault / "02_生活运营" / "00_待办事项" / "工作"
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
    assert trash[0]["kind"] == "folder"
    assert trash[0]["title"] == "工作"
    assert trash[0]["count"] == 1
    assert trash[0]["dida_ids"] == ["task-1"]
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
    folder = vault / "02_生活运营" / "00_待办事项" / "工作"
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
    assert not (vault / "02_生活运营" / "00_待办事项" / "工作").exists()


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


def test_local_task_is_pushed_to_dida(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    response = client.post("/api/time/tasks", json={
        "title": "本地新建", "list_name": "工作", "date": "2026-10-18",
        "time": "09:30", "priority": "medium", "tags": ["本地"],
        "revision": board["revision"],
    })
    assert response.status_code == 200, response.text

    result = run_sync(client)
    assert result["counts"]["created_pushed"] == 1
    assert len(seen["added"]) == 1
    payload = seen["added"][0][0]
    assert payload["title"] == "本地新建"
    assert payload["projectId"] == "proj-1"
    assert payload["priority"] == 3
    assert payload["tags"] == ["本地"]
    assert payload["dueDate"] == "2026-10-18T01:30:00.000+0000"
    note = next(item for item in scan(vault)[0] if item["title"] == "本地新建")
    assert len(note["dida_id"]) == 24

    again = run_sync(client)
    assert again["counts"]["created_pushed"] == 0
    assert len(seen["added"]) == 1


def test_each_local_operation_triggers_auto_push(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    seen = {"deleted": [], "restored": [], "added": [], "updated": [], "moved": []}

    def factory(_credentials):
        return FakeProvider(snapshot, (), seen["deleted"], seen["restored"],
                            seen["added"], seen["updated"], seen["moved"])

    app = create_app(
        Database(tmp_path / "dida.sqlite3"), vault,
        security_environ={}, env_file=tmp_path / ".env",
        dida_provider_factory=factory, enable_dida_auto_push=True,
    )
    # 用上下文管理器保持同一个事件循环，后台防抖任务才能像真实服务一样运行。
    with TestClient(app) as client:
        client.get("/")
        configure(client, csrf=True)
        run_sync(client)
        board = client.get("/api/time/tasks").json()
        response = client.post("/api/time/tasks", json={
            "title": "自动回写", "list_name": "工作", "date": "2026-10-18",
            "time": "", "priority": "none", "tags": [],
            "revision": board["revision"],
        })
        assert response.status_code == 200, response.text

        deadline = time.time() + 5
        while time.time() < deadline and not seen["added"]:
            time.sleep(0.1)
    assert seen["added"], "auto push did not run after the local create"
    assert seen["added"][0][0]["title"] == "自动回写"


def test_local_task_edit_is_pushed_to_dida(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    task = board["tasks"][0]
    response = client.post("/api/time/tasks/update", json={
        "key": task["key"], "revision": board["revision"],
        "title": "买酸奶", "list_name": task["list_name"],
        "date": "2026-10-19", "time": "", "priority": "high",
        "tags": ["本地修改"], "done": True, "day": "2026-10-18",
    })
    assert response.status_code == 200, response.text

    result = run_sync(client)
    assert result["counts"]["updated_pushed"] == 1
    assert len(seen["updated"]) == 1
    payload = seen["updated"][0][0]
    assert payload["id"] == "task-1"
    assert payload["title"] == "买酸奶"
    assert payload["projectId"] == "proj-1"
    assert payload["priority"] == 5
    assert payload["status"] == 2
    assert payload["tags"] == ["本地修改"]
    assert payload["etag"] == "etag-1"
    # 2026-10-19 全天（上海）按滴答约定存成前一天的 16:00Z。
    assert payload["isAllDay"] is True
    assert payload["dueDate"] == "2026-10-18T16:00:00.000+0000"
    note = scan(vault)[0][0]
    assert note["title"] == "买酸奶"
    assert note["done"] is True

    again = run_sync(client)
    assert again["counts"]["updated_pushed"] == 0


def test_local_task_move_is_pushed_to_dida(tmp_path):
    other_project = make_project(id="proj-2", name="生活")
    other_task = make_task(id="task-2", project_id="proj-2", title="第二任务")
    snapshot = RemoteSnapshot(
        checkpoint=1, inbox_id="inbox-1",
        projects=(make_project(), other_project),
        tasks=(make_task(), other_task),
    )
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    task = next(item for item in board["tasks"] if item["title"] == "买牛奶")
    response = client.post("/api/time/tasks/update", json={
        "key": task["key"], "revision": board["revision"],
        "title": task["title"], "list_name": "生活",
        "date": task["date"], "time": task["time"], "priority": task["priority"],
        "tags": task["tags"], "done": False, "day": "",
    })
    assert response.status_code == 200, response.text

    result = run_sync(client)
    assert result["counts"]["moved_pushed"] == 1
    assert seen["moved"] == [[{
        "taskId": "task-1", "fromProjectId": "proj-1", "toProjectId": "proj-2",
    }]]
    assert seen["updated"][0][0]["projectId"] == "proj-2"
    note = next(item for item in scan(vault)[0] if item["title"] == "买牛奶")
    assert note["list_name"] == "生活"


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


def test_legacy_all_day_date_repair_keeps_local_completion_and_pushes(tmp_path):
    all_day = make_task(
        is_all_day=True, time_zone="Asia/Shanghai",
        start_date="2026-10-17T16:00:00.000+0000",
        due_date="2026-10-17T16:00:00.000+0000",
    )
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(all_day,))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    task = board["tasks"][0]
    # 模拟旧版留下的 10-17，同时在本地勾选完成（还没回写滴答）。
    updated = client.post("/api/time/tasks/update", json={
        "key": task["key"], "revision": board["revision"],
        "title": task["title"], "list_name": task["list_name"],
        "date": "2026-10-17", "time": "", "priority": "none",
        "tags": [], "done": True, "day": "2026-10-18",
    })
    assert updated.status_code == 200, updated.text

    result = run_sync(client)
    assert result["counts"]["local_edits"] == 1
    assert result["counts"]["updated_pushed"] == 1
    assert seen["updated"]
    payload = seen["updated"][0][0]
    assert payload["isAllDay"] is True
    assert payload["dueDate"] == "2026-10-17T16:00:00.000+0000"
    assert payload["status"] == 2
    note = scan(vault)[0][0]
    assert note["date"] == "2026-10-18"
    assert note["done"] is True


def test_sync_repairs_legacy_all_day_off_by_one_date(tmp_path):
    all_day = make_task(
        is_all_day=True, time_zone="Asia/Shanghai",
        start_date="2026-10-17T16:00:00.000+0000",
        due_date="2026-10-17T16:00:00.000+0000",
    )
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(all_day,))
    client, vault, _seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    note = scan(vault)[0][0]
    assert note["date"] == "2026-10-18"

    # 模拟旧版映射留下的 10-17，并让链接基线认为本地也没改过。
    path = note["path"]
    text = path.read_text(encoding="utf-8").replace(
        '日期: "2026-10-18"', '日期: "2026-10-17"'
    )
    path.write_text(text, encoding="utf-8", newline="")
    legacy_note = task_notes.read_task_note(vault, path)
    with sqlite3.connect(tmp_path / "dida.sqlite3") as connection:
        connection.execute(
            "UPDATE dida_task_links SET last_local_revision = ? WHERE external_id = ?",
            (legacy_note["revision"], "task-1"),
        )
        connection.commit()

    result = run_sync(client)
    # 日期修复后本地笔记 revision 变化，会按“本地改动”再回写一次滴答。
    assert result["counts"]["local_edits"] == 1
    assert result["counts"]["updated_pushed"] == 1
    assert scan(vault)[0][0]["date"] == "2026-10-18"


def test_moved_note_is_not_treated_as_local_delete(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    note = scan(vault)[0][0]
    inbox_copy = vault / "02_生活运营" / "00_待办事项" / "收集箱" / note["path"].name
    inbox_copy.parent.mkdir(parents=True, exist_ok=True)
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
    assert not (vault / "02_生活运营" / "00_待办事项").exists()


def _create_list(client, name, color="#22c55e"):
    board = client.get("/api/time/tasks").json()
    response = client.post("/api/time/lists", json={
        "name": name, "color": color, "revision": board["revision"],
    })
    assert response.status_code == 200, response.text
    return response.json()


def _find_list(client, name):
    board = client.get("/api/time/tasks").json()
    return next(item for item in board["lists"] if item["name"] == name)


def test_empty_local_list_is_created_remotely_immediately(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(), tasks=())
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    _create_list(client, "新清单")
    result = run_sync(client)
    assert result["counts"]["projects_created_pushed"] == 1
    assert len(seen["projects_added"]) == 1
    payload = seen["projects_added"][0][0]
    assert payload["name"] == "新清单"
    assert payload["color"] == "#22c55e"
    assert payload["kind"] == "TASK"
    assert payload["viewMode"] == "list"
    assert len(payload["id"]) == 24
    assert _find_list(client, "新清单")["dida_project_id"] == payload["id"]
    again = run_sync(client)
    assert again["counts"].get("projects_created_pushed", 0) == 0
    assert len(seen["projects_added"]) == 1


def test_local_list_and_its_tasks_are_pushed_in_one_cycle(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(), tasks=())
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    _create_list(client, "新清单")
    board = client.get("/api/time/tasks").json()
    created = client.post("/api/time/tasks", json={
        "title": "新任务", "list_name": "新清单", "revision": board["revision"],
    })
    assert created.status_code == 200, created.text
    result = run_sync(client)
    assert result["counts"]["projects_created_pushed"] == 1
    assert result["counts"]["created_pushed"] == 1
    project_id = seen["projects_added"][0][0]["id"]
    assert seen["added"][0][0]["projectId"] == project_id
    assert _find_list(client, "新清单")["dida_project_id"] == project_id


def test_local_list_rename_is_pushed_using_synced_name(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    renamed = client.post("/api/time/lists/update", json={
        "name": "工作", "new_name": "生活", "revision": board["revision"],
    })
    assert renamed.status_code == 200, renamed.text
    result = run_sync(client)
    assert result["counts"]["projects_renamed_pushed"] == 1
    assert seen["projects_updated"] == [[{
        "id": "proj-1", "name": "生活", "color": "#3b82f6",
    }]]
    again = run_sync(client)
    assert again["counts"].get("projects_renamed_pushed", 0) == 0
    assert len(seen["projects_updated"]) == 1


def test_sanitized_remote_name_does_not_trigger_rename(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(name="工作/家"),),
                              tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    assert _find_list(client, "工作-家")["dida_project_id"] == "proj-1"
    result = run_sync(client)
    assert result["counts"].get("projects_renamed_pushed", 0) == 0
    assert seen["projects_updated"] == []


def test_deleting_local_list_deletes_remote_project_without_task_deletes(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    deleted = client.post("/api/time/lists/delete", json={
        "name": "工作", "revision": board["revision"],
    })
    assert deleted.status_code == 200, deleted.text
    result = run_sync(client)
    assert result["counts"]["projects_deleted_pushed"] == 1
    assert result["counts"]["deleted_pushed"] == 0
    assert seen["projects_deleted"] == [["proj-1"]]
    assert seen["deleted"] == []


def test_list_delete_respects_push_deletes_off(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    settings = client.put("/api/sync/dida/settings", json={
        "enabled": False, "push_deletes": False, "csrf_token": "csrf-secret",
    })
    assert settings.status_code == 200, settings.text
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/lists/delete", json={
        "name": "工作", "revision": board["revision"],
    })
    result = run_sync(client)
    assert result["counts"].get("projects_deleted_pushed", 0) == 0
    assert seen["projects_deleted"] == []


def test_list_delete_works_when_push_changes_off(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=(make_task(),))
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    settings = client.put("/api/sync/dida/settings", json={
        "enabled": False, "push_changes": False, "csrf_token": "csrf-secret",
    })
    assert settings.status_code == 200, settings.text
    run_sync(client)
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/lists/delete", json={
        "name": "工作", "revision": board["revision"],
    })
    result = run_sync(client)
    assert result["counts"]["projects_deleted_pushed"] == 1
    assert seen["projects_deleted"] == [["proj-1"]]


def test_stale_snapshot_does_not_delete_locally_created_project(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(make_project(),), tasks=())
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    _create_list(client, "新清单")
    run_sync(client)
    project_id = _find_list(client, "新清单")["dida_project_id"]
    assert project_id
    assert (vault / "02_生活运营" / "00_待办事项" / "新清单").is_dir()

    result = run_sync(client)  # 快照仍不含新项目
    assert result["counts"].get("projects_deleted", 0) == 0
    assert "project_seen_pending" in result["warnings"]
    assert (vault / "02_生活运营" / "00_待办事项" / "新清单").is_dir()

    seen["snapshot"] = RemoteSnapshot(
        checkpoint=2, inbox_id="inbox-1",
        projects=(make_project(), make_project(id=project_id, name="新清单")),
        tasks=(),
    )
    run_sync(client)  # 完整快照确认后解除保护

    seen["snapshot"] = RemoteSnapshot(
        checkpoint=3, inbox_id="inbox-1",
        projects=(make_project(),), tasks=(),
    )
    result = run_sync(client)
    assert result["counts"]["projects_deleted"] == 1
    assert not (vault / "02_生活运营" / "00_待办事项" / "新清单").exists()


def test_one_list_create_failure_does_not_block_others(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(), tasks=())
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    seen["project_errors_by_name"]["失败清单"] = "EXCEED_QUOTA"
    _create_list(client, "失败清单")
    _create_list(client, "正常清单")
    board = client.get("/api/time/tasks").json()
    client.post("/api/time/tasks", json={
        "title": "能推的任务", "list_name": "正常清单", "revision": board["revision"],
    })
    result = run_sync(client)
    assert result["counts"]["projects_created_pushed"] == 1
    assert result["counts"]["created_pushed"] == 1
    assert result["counts"]["errors"] >= 1
    assert "project_push_error_EXCEED_QUOTA" in result["warnings"]
    assert _find_list(client, "正常清单")["dida_project_id"]


def test_initialize_migrates_project_links_and_baselines_synced_name(tmp_path):
    database = Database(tmp_path / "migrate.sqlite3")
    with database.connect() as connection:
        connection.executescript("""
        CREATE TABLE dida_project_links (
            source TEXT NOT NULL DEFAULT 'dida_web',
            project_id TEXT NOT NULL,
            list_name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '',
            closed INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(source, project_id)
        );
        """)
        connection.execute(
            """INSERT INTO dida_project_links
               (source, project_id, list_name, color, closed, last_seen_at, created_at)
               VALUES ('dida_web', 'proj-old', '工作', '#3b82f6', 0, 'now', 'now')"""
        )
        connection.commit()
    DidaSyncService(database, tmp_path / "vault")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT synced_name, remote_seen FROM dida_project_links "
            "WHERE project_id = 'proj-old'"
        ).fetchone()
    assert row["synced_name"] == "工作"
    assert row["remote_seen"] == 1


def test_project_create_without_etag_still_attaches(tmp_path):
    snapshot = RemoteSnapshot(checkpoint=1, inbox_id="inbox-1",
                              projects=(), tasks=())
    client, vault, seen = make_client(tmp_path, snapshot)
    configure(client, csrf=True)
    run_sync(client)
    seen["project_etags"] = False
    _create_list(client, "无确认清单")
    result = run_sync(client)
    assert result["counts"]["projects_created_pushed"] == 1
    assert "project_create_unconfirmed" in result["warnings"]
    assert _find_list(client, "无确认清单")["dida_project_id"]
    again = run_sync(client)
    assert again["counts"].get("projects_created_pushed", 0) == 0
