"""Audit summary counts operations without leaking any content."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def build_client(tmp_path):
    database = Database(tmp_path / "torch_os.sqlite3")
    gateway = ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1)
    app = create_app(database, tmp_path / "vault", gateway, security_environ={})
    client = TestClient(app)
    client.get("/")
    return client, database


def test_audit_summary_counts_chat_modes_exports_backups_and_errors(tmp_path):
    client, database = build_client(tmp_path)
    client.post("/api/notes", json={"title": "隐私标题", "content": "隐私正文内容", "type": "note"})
    client.post("/api/chat", json={"message": "隐私问题内容？"})
    assert client.get("/api/export/conversations.json").status_code == 200
    assert client.post("/api/backups").status_code == 201

    # A simulated cloud answer and two errors, inserted directly (no cloud call).
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO audit_events(event_type,detail_json,created_at) VALUES (?,?,?)",
            ("chat_completed", json.dumps({"mode": "model"}), now),
        )
        connection.execute(
            "INSERT INTO audit_events(event_type,detail_json,created_at) VALUES (?,?,?)",
            ("model_error", json.dumps({"code": "model_unauthorized"}), now),
        )
        connection.execute(
            "INSERT INTO audit_events(event_type,detail_json,created_at) VALUES (?,?,?)",
            ("scheduler_loop_error", "{}", now),
        )
        connection.execute(
            "INSERT INTO audit_events(event_type,detail_json,created_at) VALUES (?,?,?)",
            ("ancient_event", "{}", old),
        )

    payload = client.get("/api/audit/summary").json()
    assert payload["window_days"] == 30
    assert payload["chat"]["total"] == 2
    assert payload["chat"]["local"] == 1 and payload["chat"]["cloud"] == 1
    assert payload["chat"]["other"] == 0
    assert payload["exports"] >= 1
    assert payload["backups"] == 1
    assert payload["errors"] == 2
    assert payload["latest_error"]["event_type"] in {"model_error", "scheduler_loop_error"}
    types = {item["event_type"]: item["count"] for item in payload["by_type"]}
    assert types["note_created"] == 1 and types["chat_completed"] == 2
    assert "ancient_event" not in types

    text = json.dumps(payload, ensure_ascii=False)
    for secret in ("隐私标题", "隐私正文内容", "隐私问题内容"):
        assert secret not in text
    assert "路径" in payload["note"]

    wider = client.get("/api/audit/summary?days=365").json()
    wider_types = {item["event_type"]: item["count"] for item in wider["by_type"]}
    assert wider_types["ancient_event"] == 1
    assert client.get("/api/audit/summary?days=0").status_code == 422
    assert client.get("/api/audit/summary?days=400").status_code == 422


def test_audit_summary_handles_empty_database_and_requires_the_local_session(tmp_path):
    client, _ = build_client(tmp_path)
    payload = client.get("/api/audit/summary").json()
    assert payload["total_events"] == 0 and payload["chat"]["total"] == 0 and payload["errors"] == 0

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/audit/summary").status_code == 401
