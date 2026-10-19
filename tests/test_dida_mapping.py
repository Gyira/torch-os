"""滴答字段映射与只读客户端的单元测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
import pytest

from backend.app.dida_client import (
    DidaCredentials, DidaError, PrivateDidaProvider, RemoteChecklistItem, RemoteTask,
    parse_cookie_input,
)
from backend.app.dida_mapping import (
    build_body, build_remote_task_payload, clean_tags, html_to_markdown,
    legacy_all_day_date, map_task, material_matches, priority_text, remote_hash,
    sanitize_list_name,
)


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


def test_priority_text():
    assert priority_text(5) == "high"
    assert priority_text(3) == "medium"
    assert priority_text(1) == "low"
    assert priority_text(0) == "none"
    assert priority_text("bad") == "none"


def test_time_mapping_range_and_timezone():
    mapped = map_task(make_task(), target_tz="Asia/Shanghai", list_name="生活")
    assert mapped.values["date"] == "2026-10-18"
    assert mapped.values["time"] == "09:00-09:30"

    utc = make_task(due_date="2026-10-18T01:30:00+00:00", start_date="")
    mapped = map_task(utc, target_tz="Asia/Shanghai", list_name="生活")
    assert (mapped.values["date"], mapped.values["time"]) == ("2026-10-18", "09:30")


def test_all_day_keeps_literal_date():
    task = make_task(is_all_day=True, due_date="2026-10-18T00:00:00+0000", start_date="")
    mapped = map_task(task, target_tz="Asia/Shanghai", list_name="生活")
    assert (mapped.values["date"], mapped.values["time"]) == ("2026-10-18", "")


def test_all_day_uses_task_timezone_not_utc_date_prefix():
    # 滴答上海时区的 10-18 全天任务存成 10-17T16:00Z；截取 UTC 字符串会差一天。
    task = make_task(
        is_all_day=True, time_zone="Asia/Shanghai",
        start_date="2026-10-17T16:00:00.000+0000",
        due_date="2026-10-17T16:00:00.000+0000",
    )
    mapped = map_task(task, target_tz="Asia/Shanghai", list_name="收集箱")
    assert mapped.values["date"] == "2026-10-18"
    assert legacy_all_day_date(task) == "2026-10-17"


def test_floating_keeps_wall_clock():
    task = make_task(is_floating=True, due_date="2026-10-18T22:15:00", start_date="")
    mapped = map_task(task, target_tz="Asia/Shanghai", list_name="生活")
    assert (mapped.values["date"], mapped.values["time"]) == ("2026-10-18", "22:15")


def test_html_and_checklist_body():
    assert "Hello" in html_to_markdown("<p>Hello</p>")
    assert "- One" in html_to_markdown("<ul><li>One</li></ul>")
    assert "bad" not in html_to_markdown("<script>bad</script><p>ok</p>")
    body, warnings = build_body("<p>两盒</p>", (RemoteChecklistItem("i1", "低脂", 0),))
    assert "两盒" in body
    assert "- [ ] 低脂" in body
    assert warnings == []


def test_tags_and_list_name_are_sanitized():
    tags = clean_tags(["#生活", " 生活 ", "x" * 40])
    assert tags == ["生活", "x" * 24]
    assert sanitize_list_name("A/B:C") == "A-B-C"
    assert sanitize_list_name("") == "滴答清单"


def test_remote_hash_changes_with_content():
    first = remote_hash(make_task())
    second = remote_hash(make_task(title="买酸奶"))
    assert first != second
    assert first == remote_hash(make_task())


def test_material_matches_ignores_pinned_and_optional_body():
    mapped = map_task(make_task(), target_tz="Asia/Shanghai", list_name="生活")
    note = {
        "title": mapped.title, "list_name": mapped.list_name,
        "date": mapped.values["date"], "time": mapped.values["time"],
        "priority": mapped.values["priority"], "tags": mapped.values["tags"],
        "done": mapped.values["done"], "done_on": mapped.values["done_on"],
        "created": mapped.values["created"], "pinned": True,
    }
    assert material_matches(note, mapped)
    assert not material_matches(note, mapped, compare_body=True, note_body="不同的本地正文")


def test_build_remote_payload_preserves_unmapped_remote_fields():
    note = {
        "title": "本地任务", "date": "2026-10-18", "time": "09:30-10:00",
        "priority": "high", "tags": ["本地"], "done": False, "done_on": "",
        "created": "2026-10-18", "pinned": False, "dida_id": "t1",
    }
    base = {
        "id": "t1", "projectId": "p1", "etag": "e1",
        "content": "<p>远端正文</p>", "items": [{"id": "i1"}],
        "reminders": [{"x": 1}], "repeatFlag": "RRULE", "kind": "TEXT",
        "modifiedTime": "2026-10-17T10:00:00.000+0000",
    }
    payload = build_remote_task_payload(
        note, task_id="t1", project_id="p1",
        target_tz="Asia/Shanghai", base=base,
    )
    assert payload["content"] == "<p>远端正文</p>"
    assert payload["items"] == [{"id": "i1"}]
    assert payload["reminders"] == [{"x": 1}]
    assert payload["repeatFlag"] == "RRULE"
    assert payload["etag"] == "e1"
    assert payload["priority"] == 5
    assert payload["status"] == 0
    assert payload["startDate"] == "2026-10-18T01:30:00.000+0000"
    assert payload["dueDate"] == "2026-10-18T02:00:00.000+0000"


def test_build_remote_payload_create_all_day_uses_shanghai_midnight():
    note = {
        "title": "今天任务", "date": "2026-10-18", "time": "",
        "priority": "none", "tags": [], "done": False, "done_on": "",
        "created": "2026-10-18", "pinned": False, "dida_id": "",
    }
    payload = build_remote_task_payload(
        note, task_id="new-id", project_id="inbox",
        target_tz="Asia/Shanghai", body="第一行\n第二行",
    )
    assert payload["isAllDay"] is True
    assert payload["startDate"] == "2026-10-17T16:00:00.000+0000"
    assert payload["dueDate"] == payload["startDate"]
    assert payload["content"].startswith("<p>第一行<br>第二行</p>")
    assert payload["id"] == "new-id"


def test_parse_cookie_input():
    assert parse_cookie_input("abc") == ("abc", "")
    assert parse_cookie_input("t=abc; _csrf_token=xyz; other=1") == ("abc", "xyz")
    with pytest.raises(DidaError):
        parse_cookie_input("t=\r\nbad")


def _provider_with(handler) -> PrivateDidaProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", device_id="dev-1",
                        device_version=8000, timezone="Asia/Shanghai"),
        client=client,
    )


def test_client_reads_snapshot_and_completed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == "t=abc"
        assert "x-device" in request.headers
        assert "X-Csrftoken" not in request.headers
        if "/batch/check/" in request.url.path:
            return httpx.Response(200, json={
                "checkPoint": 5,
                "inboxId": "inbox-1",
                "projectProfiles": [{"id": "p1", "name": "工作"}],
                "syncTaskBean": {
                    "update": [{"id": "t1", "projectId": "p1", "title": "任务", "status": 0}],
                    "delete": [{"id": "t2"}],
                },
            })
        if "/project/all/closed" in request.url.path:
            return httpx.Response(200, json=[{"id": "t3", "projectId": "p1", "title": "完成"}])
        return httpx.Response(404)

    provider = _provider_with(handler)
    snapshot = asyncio.run(provider.fetch_snapshot())
    completed = asyncio.run(provider.fetch_completed())
    assert snapshot.checkpoint == 5
    assert snapshot.inbox_id == "inbox-1"
    assert snapshot.tasks[0].title == "任务"
    assert snapshot.deleted_ids == ("t2",)
    assert completed[0].id == "t3"
    asyncio.run(provider.aclose())


def test_client_bad_cookie_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"error": "bad"})

    provider = _provider_with(handler)
    with pytest.raises(DidaError) as error:
        asyncio.run(provider.fetch_snapshot())
    assert error.value.code == "bad_cookie"
    assert calls["count"] == 1
    asyncio.run(provider.aclose())


def test_client_delete_tasks_uses_csrf_and_web_envelope():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["csrf"] = request.headers.get("x-csrftoken")
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id2etag": {}, "id2error": {}})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", csrf_token="csrf-1",
                        device_id="dev-1"),
        client=client,
    )
    result = asyncio.run(provider.delete_tasks([{"taskId": "t1", "projectId": "p1"}]))
    assert result["errors"] == {}
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v2/batch/task"
    assert seen["csrf"] == "csrf-1"
    assert "t=abc" in seen["cookie"] and "_csrf_token=csrf-1" in seen["cookie"]
    body = json.loads(seen["body"])
    assert body["delete"] == [{"taskId": "t1", "projectId": "p1"}]
    assert body["add"] == [] and body["update"] == []
    asyncio.run(provider.aclose())


def test_client_restore_tasks_uses_web_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["csrf"] = request.headers.get("x-csrftoken")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id2etag": {"t1": "etag-2"}, "id2error": {}})

    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", csrf_token="csrf-1",
                        device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = asyncio.run(provider.restore_tasks([{"taskId": "t1", "projectId": "p1"}]))
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v2/trash/restore"
    assert seen["csrf"] == "csrf-1"
    assert seen["body"] == [
        {"fromProjectId": "p1", "taskId": "t1", "toProjectId": "p1"}
    ]
    assert result["etags"] == {"t1": "etag-2"}
    asyncio.run(provider.aclose())


def test_client_move_tasks_uses_task_project_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id2etag": {}, "id2error": {}})

    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", csrf_token="csrf-1",
                        device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = asyncio.run(provider.move_tasks([{
        "taskId": "t1", "fromProjectId": "p1", "toProjectId": "p2",
    }]))
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v2/batch/taskProject"
    assert seen["body"] == [{
        "fromProjectId": "p1", "taskId": "t1", "toProjectId": "p2",
    }]
    assert result["errors"] == {}
    asyncio.run(provider.aclose())


def test_client_add_projects_uses_batch_project_envelope():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["csrf"] = request.headers.get("x-csrftoken")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id2etag": {"a" * 24: "e1"}, "id2error": {}})

    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", csrf_token="csrf-1",
                        device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    project = {"id": "a" * 24, "name": "新清单", "color": "#3b82f6",
               "kind": "TASK", "viewMode": "list"}
    result = asyncio.run(provider.add_projects([project]))
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v2/batch/project"
    assert seen["csrf"] == "csrf-1"
    assert seen["body"] == {"add": [project], "update": [], "delete": []}
    assert result["etags"] == {"a" * 24: "e1"}
    asyncio.run(provider.aclose())


def test_client_update_projects_sends_id_name_color():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"id2etag": {"p1": "e2"}, "id2error": {}})

    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", csrf_token="csrf-1",
                        device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = asyncio.run(provider.update_projects([
        {"id": "p1", "name": "改名后", "color": "#ff0000"},
    ]))
    assert seen["path"] == "/api/v2/batch/project"
    assert seen["body"]["update"] == [
        {"id": "p1", "name": "改名后", "color": "#ff0000"}
    ]
    assert result["etags"] == {"p1": "e2"}
    asyncio.run(provider.aclose())


def test_client_delete_projects_sends_bare_ids_and_keeps_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={
            "id2etag": {}, "id2error": {"p2": "NOT_EXISTED"},
        })

    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", csrf_token="csrf-1",
                        device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = asyncio.run(provider.delete_projects(["p1", "p2"]))
    assert seen["path"] == "/api/v2/batch/project"
    assert seen["body"] == {"add": [], "update": [], "delete": ["p1", "p2"]}
    assert result["errors"] == {"p2": "NOT_EXISTED"}
    asyncio.run(provider.aclose())


def test_client_project_write_requires_csrf():
    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(500)
        )),
    )
    with pytest.raises(DidaError) as error:
        asyncio.run(provider.add_projects([{"id": "p1", "name": "清单"}]))
    assert error.value.code == "csrf_required"
    asyncio.run(provider.aclose())


def test_client_delete_requires_csrf():
    provider = PrivateDidaProvider(
        DidaCredentials(region="dida365", cookie="abc", device_id="dev-1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(500)
        )),
    )
    with pytest.raises(DidaError) as error:
        asyncio.run(provider.delete_tasks([{"taskId": "t1", "projectId": "p1"}]))
    assert error.value.code == "csrf_required"
    asyncio.run(provider.aclose())


def test_client_retries_rate_limit(monkeypatch):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"checkPoint": 1, "syncTaskBean": {"update": []}})

    provider = _provider_with(handler)
    monkeypatch.setattr(provider, "_backoff", lambda _attempt: 0)
    snapshot = asyncio.run(provider.fetch_snapshot())
    assert snapshot.checkpoint == 1
    assert calls["count"] == 2
    asyncio.run(provider.aclose())
