"""WeFlow 本地 API 客户端与「微信 → 现有聊天项目」增量合并。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import love as love_module
from backend.app.love import (
    LoveStore,
    WeFlowMessage,
    _WeFlowMergeState,
    _weflow_token,
    create_love_router,
)
from backend.app.weflow import (
    WeFlowClient, WeFlowError, load_chatlab_export, parse_chatlab,
)

TZ = ZoneInfo("Asia/Shanghai")
TOKEN = "test-weflow-token"
SAMPLE = """2026-05-20 10:00:00 我: 你好，我的电话是13800138000
2026-05-20 10:01:00 小林: 嗯嗯，今天做什么
2026-05-20 10:02:00 我: 明天见面吧
2026-05-21 09:00:00 小林: 好呀
2026-05-21 09:01:00 我: 那说定了
"""


def epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp())


def chatlab_payload() -> dict:
    return {
        "meta": {"ownerId": "wxid_me", "name": "小林"},
        "members": [
            {"platformId": "wxid_me", "accountName": "我"},
            {"platformId": "wxid_partner", "accountName": "小林"},
        ],
        "messages": [
            {
                "platformMessageId": "", "sender": "wxid_me", "accountName": "我",
                "timestamp": epoch("2026-05-20T10:02:00"), "type": 0, "content": "明天见面吧",
            },
            {
                "platformMessageId": "m-1", "sender": "wxid_partner", "accountName": "小林",
                "timestamp": epoch("2026-05-20T10:03:00"), "type": 0, "content": "今天下雨",
            },
            {
                "platformMessageId": "m-2", "sender": "wxid_me", "accountName": "我",
                "timestamp": epoch("2026-05-20T10:04:00"), "type": 1, "content": "",
                "mediaPath": "media/abc.jpg",
            },
            {
                "platformMessageId": "m-3", "sender": "wxid_partner", "accountName": "小林",
                "timestamp": epoch("2026-05-20T10:05:00"), "type": 80, "content": "你已添加了对方",
            },
            {
                "platformMessageId": "m-1", "sender": "wxid_partner", "accountName": "小林",
                "timestamp": epoch("2026-05-20T10:03:00"), "type": 0, "content": "今天下雨",
            },
        ],
        "sync": {"hasMore": False, "nextSince": epoch("2026-05-20T10:05:00"),
                 "nextOffset": 5, "watermark": epoch("2026-05-20T10:05:00")},
    }


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("authorization") == f"Bearer {TOKEN}"
    if request.url.path == "/api/v1/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/api/v1/sessions":
        return httpx.Response(200, json={"sessions": [
            {"id": "wxid_partner", "name": "小林", "type": "private",
             "platform": "wechat", "messageCount": 5,
             "lastMessageAt": epoch("2026-05-20T10:05:00")},
            {"id": "group-1", "name": "工作群", "type": "group",
             "platform": "wechat", "messageCount": 20},
            {"id": "channel-1", "name": "公众号", "type": "channel",
             "platform": "wechat", "messageCount": 3},
        ]})
    if request.url.path.endswith("/messages"):
        return httpx.Response(200, json=chatlab_payload())
    return httpx.Response(404, json={"error": "not found"})


def build_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, LoveStore]:
    store = LoveStore(tmp_path / "love" / "love.db", key_file=tmp_path / "love" / ".key")
    store.ensure_schema()
    app = FastAPI()
    app.include_router(create_love_router(store))
    real_client = WeFlowClient

    def factory(token: str, port: int = 5031, **kwargs):
        return real_client(token, port, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(love_module, "WeFlowClient", factory)
    return TestClient(app), store


def import_sample(client: TestClient) -> str:
    response = client.post("/api/love/import/text", json={
        "content": SAMPLE, "file_name": "sample.txt", "project_name": "测试项目",
    })
    assert response.status_code == 200, response.text
    return response.json()["project_id"]


def configure(client: TestClient) -> None:
    response = client.put("/api/love/weflow/settings", json={"token": TOKEN, "port": 5031})
    assert response.status_code == 200, response.text


def connect(client: TestClient, project_id: str, me_sender: str = "我") -> dict:
    response = client.post("/api/love/weflow/connect", json={
        "project_id": project_id, "session_id": "wxid_partner", "me_sender": me_sender,
    })
    assert response.status_code == 200, response.text
    return response.json()


def sync(client: TestClient, project_id: str, mode: str, since: int | None = None) -> dict:
    payload = {"project_id": project_id, "mode": mode}
    if since is not None:
        payload["since"] = since
    response = client.post("/api/love/weflow/sync", json=payload)
    assert response.status_code == 200, response.text
    log = response.json()
    status = client.get(f"/api/love/weflow/sync/{log['id']}")
    assert status.status_code == 200, status.text
    return status.json()


def test_parse_chatlab_normalizes_messages() -> None:
    pull = parse_chatlab(chatlab_payload())
    assert pull.owner_id == "wxid_me"
    assert pull.members["wxid_partner"] == "小林"
    assert len(pull.messages) == 5
    first = pull.messages[0]
    assert first.is_send is True
    assert first.sender == "我"
    assert first.timestamp == datetime(2026, 5, 20, 10, 2)
    assert pull.messages[1].is_send is False
    image = pull.messages[2]
    assert image.content == "[图片]"
    assert image.media_path == "media/abc.jpg"
    assert pull.messages[3].is_system is True
    assert pull.has_more is False


def test_client_uses_loopback_token_and_filters_private() -> None:
    seen: list[httpx.Request] = []

    def mock(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/sessions":
            return httpx.Response(200, json={"sessions": [
                {"id": "p1", "name": "小林", "type": "private"},
                {"id": "g1", "name": "群", "type": "group"},
            ]})
        return httpx.Response(200, json={"ok": True})

    client = WeFlowClient(TOKEN, 5031, transport=httpx.MockTransport(mock))
    try:
        sessions = client.sessions()
        assert [item.id for item in sessions] == ["p1"]
        assert seen[0].url.host == "127.0.0.1"
        assert seen[0].url.port == 5031
        assert seen[0].url.params["format"] == "chatlab"
        assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    finally:
        client.close()


def test_client_rejects_bad_token_and_connection() -> None:
    unauthorized = WeFlowClient(
        TOKEN, 5031,
        transport=httpx.MockTransport(lambda _request: httpx.Response(401)),
    )
    try:
        with pytest.raises(WeFlowError, match="Token"):
            unauthorized.health()
    finally:
        unauthorized.close()

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    offline = WeFlowClient(TOKEN, 5031, transport=httpx.MockTransport(boom))
    try:
        with pytest.raises(WeFlowError, match="连不上 WeFlow"):
            offline.health()
    finally:
        offline.close()
    with pytest.raises(WeFlowError, match="Token"):
        WeFlowClient("", 5031)


def test_settings_encrypts_token_and_never_returns_it(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    configure(client)
    status = client.get("/api/love/weflow/status")
    assert status.status_code == 200
    assert status.json()["token_configured"] is True
    assert TOKEN not in status.text
    assert _weflow_token(store) == TOKEN
    with store.connect() as con:
        stored = con.execute("SELECT value FROM settings WHERE key='weflow_token'").fetchone()[0]
    assert stored
    assert TOKEN not in stored

    # 空 Token 只改端口，不清除已经加密保存的 Token。
    kept = client.put("/api/love/weflow/settings", json={"token": "", "port": 5032})
    assert kept.status_code == 200, kept.text
    assert kept.json()["token_configured"] is True
    assert kept.json()["port"] == 5032
    assert _weflow_token(store) == TOKEN

    deleted = client.delete("/api/love/weflow/token")
    assert deleted.json()["token_configured"] is False
    assert TOKEN not in deleted.text


def test_sessions_and_connect_map_me_and_reject_groups(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    configure(client)
    project_id = import_sample(client)
    sessions = client.get("/api/love/weflow/sessions")
    assert sessions.status_code == 200, sessions.text
    assert [item["id"] for item in sessions.json()["sessions"]] == ["wxid_partner"]
    assert TOKEN not in sessions.text

    group = client.post("/api/love/weflow/connect", json={
        "project_id": project_id, "session_id": "group-1", "me_sender": "我",
    })
    assert group.status_code == 422

    result = connect(client, project_id)
    assert result["source"]["session_id"] == "wxid_partner"
    assert result["source"]["contact_name"] == "小林"
    assert result["source"]["me_sender"] == "我"
    assert result["source"]["partner_sender"] == "小林"
    assert result["project"] == {"partner_a": "我", "partner_b": "小林"}

    source = client.get(f"/api/love/weflow/project?project_id={project_id}").json()
    assert source["source"]["watermark"] == 0


def test_connect_swaps_project_roles_when_needed(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    configure(client)
    project_id = import_sample(client)
    with store.connect() as con:
        con.execute("UPDATE projects SET partner_a='小林', partner_b='我' WHERE id=?",
                    (project_id,))
    result = connect(client, project_id, me_sender="我")
    assert result["swapped"] is True
    assert result["project"] == {"partner_a": "我", "partner_b": "小林"}
    detail = client.get(f"/api/love/projects/{project_id}").json()
    assert detail["partner_a"] == "我"
    assert detail["partner_b"] == "小林"


def test_preview_does_not_write_and_apply_merges_without_duplicates(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    configure(client)
    project_id = import_sample(client)
    connect(client, project_id)

    preview = sync(client, project_id, "preview", since=0)
    assert preview["status"] == "completed", preview
    assert preview["mode"] == "preview"
    assert preview["fetched"] == 5
    assert preview["matched"] == 1
    assert preview["inserted"] == 3
    assert preview["duplicates"] == 1
    assert preview["ambiguous"] == 0
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 5
        assert con.execute("SELECT watermark FROM project_sources").fetchone()[0] == 0
        assert not (store.db_path.parent / "backups").exists()

    applied = sync(client, project_id, "apply", since=0)
    assert applied["status"] == "completed", applied
    assert applied["inserted"] == 3
    assert applied["matched"] == 1
    assert applied["duplicates"] == 1
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 8
        project = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        assert project["message_count"] == 8
        matched = con.execute(
            "SELECT * FROM messages WHERE timestamp='2026-05-20 10:02:00.000000'"
        ).fetchone()
        assert matched["platform_message_id"] is None or matched["platform_message_id"] == ""
        inserted = con.execute(
            "SELECT * FROM messages WHERE platform_message_id='m-1'"
        ).fetchone()
        assert inserted["sender"] == "小林"
        assert inserted["is_send"] == 0
        assert inserted["source"] == "weflow"
        image = con.execute(
            "SELECT * FROM messages WHERE platform_message_id='m-2'"
        ).fetchone()
        assert image["content"] == "[图片]"
        assert image["media_path"] == "media/abc.jpg"
        system = con.execute(
            "SELECT * FROM messages WHERE platform_message_id='m-3'"
        ).fetchone()
        assert system["is_system"] == 1
        watermark = con.execute("SELECT watermark FROM project_sources").fetchone()[0]
        assert watermark == epoch("2026-05-20T10:05:00")
    backups = list((store.db_path.parent / "backups").glob("love-weflow-before-merge-*.db"))
    assert backups

    rebound = connect(client, project_id)
    assert rebound["source"]["watermark"] == epoch("2026-05-20T10:05:00")

    again = sync(client, project_id, "apply", since=0)
    assert again["status"] == "completed", again
    assert again["inserted"] == 0
    # 没有平台消息 ID 的旧消息只能每次按内容匹配；其余四条平台消息按 ID 去重。
    assert again["matched"] == 1
    assert again["duplicates"] == 4
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 8


def test_create_project_from_weflow_session(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    configure(client)
    created = client.post("/api/love/weflow/project", json={
        "session_id": "wxid_partner", "me_sender": "我",
    })
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["project"]["partner_a"] == "我"
    assert data["project"]["partner_b"] == "小林"
    assert data["project"]["message_count"] == 0
    assert data["source"]["session_id"] == "wxid_partner"
    assert data["source"]["watermark"] == 0
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1

    duplicate = client.post("/api/love/weflow/project", json={
        "session_id": "wxid_partner", "me_sender": "我",
    })
    assert duplicate.status_code == 409
    group = client.post("/api/love/weflow/project", json={
        "session_id": "group-1", "me_sender": "我",
    })
    assert group.status_code == 422


def test_sync_requires_a_bound_session(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    project_id = import_sample(client)
    response = client.post("/api/love/weflow/sync", json={
        "project_id": project_id, "mode": "preview",
    })
    assert response.status_code == 400
    assert "绑定" in response.json()["detail"]


def test_sync_without_token_does_not_leave_a_queued_log(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    configure(client)
    project_id = import_sample(client)
    connect(client, project_id)
    assert client.delete("/api/love/weflow/token").status_code == 200
    response = client.post("/api/love/weflow/sync", json={
        "project_id": project_id, "mode": "preview",
    })
    assert response.status_code == 400
    with store.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM weflow_sync_logs WHERE status IN ('queued','running')"
        ).fetchone()[0]
    assert count == 0


def test_sync_rejects_concurrent_and_recovers_stale_log(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    configure(client)
    project_id = import_sample(client)
    connect(client, project_id)
    now = datetime.now()
    with store.connect() as con:
        con.execute(
            "INSERT INTO weflow_sync_logs (id, project_id, mode, status, started_at) "
            "VALUES ('old', ?, 'preview', 'running', ?)",
            (project_id, now.strftime("%Y-%m-%d %H:%M:%S.%f")),
        )
    concurrent = client.post("/api/love/weflow/sync", json={
        "project_id": project_id, "mode": "preview",
    })
    assert concurrent.status_code == 409

    old = (now - timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S.%f")
    with store.connect() as con:
        con.execute("UPDATE weflow_sync_logs SET started_at=? WHERE id='old'", (old,))
    recovered = client.post("/api/love/weflow/sync", json={
        "project_id": project_id, "mode": "preview",
    })
    assert recovered.status_code == 200, recovered.text
    status = client.get(f"/api/love/weflow/sync/{recovered.json()['id']}").json()
    assert status["status"] == "completed"
    with store.connect() as con:
        stale = con.execute(
            "SELECT status, error_message FROM weflow_sync_logs WHERE id='old'"
        ).fetchone()
    assert stale["status"] == "failed"
    assert "中断" in stale["error_message"]


def _message(text: str, stamp: str, *, sender: str = "我") -> WeFlowMessage:
    return WeFlowMessage(
        platform_message_id="", sender_id="wxid_me", sender=sender,
        timestamp=datetime.fromisoformat(stamp), message_type=0, content=text,
        is_send=True, is_system=False,
    )


def test_merge_fuzzy_matches_within_two_minutes(tmp_path: Path) -> None:
    store = LoveStore(tmp_path / "love.db", key_file=tmp_path / ".key")
    store.ensure_schema()
    with store.connect() as con:
        con.execute(
            "INSERT INTO projects (id, name, partner_a, partner_b, created_at, updated_at) "
            "VALUES ('p1','项目','我','小林',?,?)", ("2026-05-20 00:00:00",) * 2,
        )
        con.execute(
            "INSERT INTO messages (id, project_id, timestamp, sender, content, is_system, "
            "content_hash, created_at) VALUES ('m1','p1','2026-05-20 10:02:30.000000',"
            "'我','明天见面吧',0,?,?)", ("h1", "2026-05-20 10:02:30.000000"),
        )
        state = _WeFlowMergeState(con, "p1", "我", "小林")
        state.ingest(con, _message("明天见面吧", "2026-05-20T10:02:00"), apply=True)
    assert state.stats["matched"] == 1
    assert state.stats["inserted"] == 0
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_merge_marks_ambiguous_when_multiple_old_rows_match(tmp_path: Path) -> None:
    store = LoveStore(tmp_path / "love.db", key_file=tmp_path / ".key")
    store.ensure_schema()
    with store.connect() as con:
        con.execute(
            "INSERT INTO projects (id, name, partner_a, partner_b, created_at, updated_at) "
            "VALUES ('p1','项目','我','小林',?,?)", ("2026-05-20 00:00:00",) * 2,
        )
        for index in (1, 2):
            con.execute(
                "INSERT INTO messages (id, project_id, timestamp, sender, content, is_system, "
                "content_hash, created_at) VALUES (?, 'p1', '2026-05-20 10:02:30.000000',"
                "'我','明天见面吧',0,?,?)",
                (f"m{index}", f"h{index}", "2026-05-20 10:02:30.000000"),
            )
        state = _WeFlowMergeState(con, "p1", "我", "小林")
        state.ingest(con, _message("明天见面吧", "2026-05-20T10:02:00"), apply=True)
        assert state.stats["ambiguous"] == 1
        assert state.stats["inserted"] == 0
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_load_chatlab_export_json_and_jsonl() -> None:
    payload = chatlab_payload()
    from_json = load_chatlab_export(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert len(from_json["messages"]) == 5
    parsed = parse_chatlab(from_json, me_sender="我")
    assert parsed.messages[0].is_send is True
    assert parsed.messages[1].is_send is False

    header = {
        "_type": "header",
        "meta": {"name": "小林", "type": "private"},
        "chatlab": {"version": "0.0.2", "generator": "WeFlow"},
    }
    members = [
        {"_type": "member", "platformId": "wxid_me", "accountName": "我"},
        {"_type": "member", "platformId": "wxid_partner", "accountName": "小林"},
    ]
    messages = [{"_type": "message", **item} for item in payload["messages"]]
    raw = "\n".join(
        json.dumps(item, ensure_ascii=False) for item in [header, *members, *messages]
    ).encode("utf-8")
    from_jsonl = load_chatlab_export(raw)
    assert from_jsonl["meta"]["name"] == "小林"
    assert len(from_jsonl["messages"]) == 5
    parsed_jsonl = parse_chatlab(from_jsonl, me_sender="我")
    assert parsed_jsonl.messages[0].sender == "我"
    assert parsed_jsonl.messages[0].is_send is True


def test_chatlab_file_preview_and_import(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    project_id = import_sample(client)
    raw = json.dumps(chatlab_payload(), ensure_ascii=False).encode("utf-8")
    preview = client.post(
        "/api/love/weflow/import-preview",
        files={"file": ("chatlab.json", raw, "application/json")},
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["message_count"] == 5
    assert set(data["senders"]) == {"我", "小林"}
    assert data["sender_counts"] == {"我": 2, "小林": 2}

    missing = client.post(
        "/api/love/weflow/import-file",
        files={"file": ("chatlab.json", raw, "application/json")},
        data={"project_id": project_id, "me_sender": "不存在的人"},
    )
    assert missing.status_code == 422

    imported = client.post(
        "/api/love/weflow/import-file",
        files={"file": ("chatlab.json", raw, "application/json")},
        data={"project_id": project_id, "me_sender": "我"},
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert result["status"] == "completed"
    assert result["inserted"] == 3
    assert result["matched"] == 1
    assert result["duplicates"] == 1
    assert result["swapped"] is False
    with store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 8
        assert con.execute(
            "SELECT message_count FROM projects WHERE id=?", (project_id,)
        ).fetchone()[0] == 8
    backups = list((store.db_path.parent / "backups").glob("love-weflow-file-before-merge-*.db"))
    assert backups


def test_chatlab_file_rejects_group_and_swaps_project_roles(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = build_client(tmp_path, monkeypatch)
    project_id = import_sample(client)
    with store.connect() as con:
        con.execute("UPDATE projects SET partner_a='小林', partner_b='我' WHERE id=?",
                    (project_id,))
    payload = chatlab_payload()
    payload["messages"].append({
        "platformMessageId": "m-9", "sender": "wxid_other", "accountName": "第三人",
        "timestamp": epoch("2026-05-20T10:06:00"), "type": 0, "content": "我也在",
    })
    group_raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    group = client.post(
        "/api/love/weflow/import-preview",
        files={"file": ("group.json", group_raw, "application/json")},
    )
    assert group.status_code == 422

    single_payload = chatlab_payload()
    single_raw = json.dumps(single_payload, ensure_ascii=False).encode("utf-8")
    imported = client.post(
        "/api/love/weflow/import-file",
        files={"file": ("chatlab.json", single_raw, "application/json")},
        data={"project_id": project_id, "me_sender": "我"},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["swapped"] is True
    detail = client.get(f"/api/love/projects/{project_id}").json()
    assert detail["partner_a"] == "我"
    assert detail["partner_b"] == "小林"


def test_sync_log_is_json_serializable(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    configure(client)
    project_id = import_sample(client)
    connect(client, project_id)
    log = sync(client, project_id, "preview", since=0)
    json.dumps(log, ensure_ascii=False)
    assert "token" not in json.dumps(log).lower()
