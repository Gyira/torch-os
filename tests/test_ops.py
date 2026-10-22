"""生活运营：月度 Markdown 表的读写与只读保护。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database
from backend.app.model_gateway import ModelGateway


def make_client(tmp_path, *, read_only=False):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        Database(tmp_path / "torch_os.sqlite3"), vault,
        ModelGateway(provider="local", base_url="", model="", api_key="", timeout=1),
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault


def test_health_month_table_roundtrip(tmp_path):
    client, vault = make_client(tmp_path)
    created = client.post("/api/ops/health/months/2026-10/rows", json={
        "date": "2026-10-21", "weight_kg": "68.4", "sleep_minutes": "450",
        "exercise_minutes": "30", "note": "状态不错",
    })
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["path"] == "02_生活运营/01_健康与体重/2026-10.md"
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["weight_kg"] == "68.4"
    assert (vault / payload["path"]).is_file()
    assert "| 日期 |" in (vault / payload["path"]).read_text(encoding="utf-8")

    updated = client.post(
        f"/api/ops/health/months/2026-10/rows/{row['row_id']}",
        json={"date": "2026-10-21", "weight_kg": "68.1"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["rows"][0]["weight_kg"] == "68.1"

    deleted = client.delete(f"/api/ops/health/months/2026-10/rows/{row['row_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["rows"] == []


def test_deleted_row_restores_in_place_with_the_same_row_id(tmp_path):
    client, vault = make_client(tmp_path)
    created = []
    for weight, note in (("70.2", "周一"), ("69.8", "周二"), ("69.5", "周三")):
        response = client.post("/api/ops/health/months/2026-10/rows", json={
            "date": "2026-10-21", "weight_kg": weight, "note": note,
        })
        assert response.status_code == 200, response.text
        created.append(response.json()["rows"][-1])

    deleted = client.delete(f"/api/ops/health/months/2026-10/rows/{created[1]['row_id']}")
    assert deleted.status_code == 200
    assert [row["weight_kg"] for row in deleted.json()["rows"]] == ["70.2", "69.5"]

    restored = client.post(
        f"/api/ops/health/months/2026-10/rows/{created[1]['row_id']}/restore?position=1",
        json={"date": created[1]["date"], "weight_kg": created[1]["weight_kg"],
              "sleep_minutes": created[1]["sleep_minutes"],
              "exercise_minutes": created[1]["exercise_minutes"], "note": created[1]["note"]})
    assert restored.status_code == 200, restored.text
    rows = restored.json()["rows"]
    assert [row["row_id"] for row in rows] == [row["row_id"] for row in created]
    assert [row["weight_kg"] for row in rows] == ["70.2", "69.8", "69.5"]
    assert created[1]["row_id"] in (vault / restored.json()["path"]).read_text(encoding="utf-8")

    # 重复撤销是冲突，不会写出第二行。
    again = client.post(
        f"/api/ops/health/months/2026-10/rows/{created[1]['row_id']}/restore",
        json={"date": created[1]["date"], "weight_kg": created[1]["weight_kg"]})
    assert again.status_code == 409
    assert len(client.get("/api/ops/health/months/2026-10").json()["rows"]) == 3


def test_restore_rejects_an_invalid_row_id(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post("/api/ops/health/months/2026-10/rows/not-a-row/restore",
                           json={"date": "2026-10-21", "weight_kg": "68"})
    assert response.status_code == 422


def test_finance_month_summary(tmp_path):
    client, vault = make_client(tmp_path)
    for kind, amount in (("支出", "12.5"), ("收入", "100")):
        response = client.post("/api/ops/finance/months/2026-10/rows", json={
            "date": "2026-10-21", "kind": kind, "amount": amount,
            "category": "餐饮", "account": "微信",
        })
        assert response.status_code == 200, response.text
    summary = client.get("/api/ops/finance/summary?months=3").json()
    assert len(summary["months"]) == 1
    assert len(summary["months"][0]["rows"]) == 2
    assert summary["months"][0]["path"].startswith("02_生活运营/02_财富与记账/")


def test_ops_is_read_only_when_vault_is_read_only(tmp_path):
    client, _ = make_client(tmp_path, read_only=True)
    blocked = client.post("/api/ops/health/months/2026-10/rows", json={
        "date": "2026-10-21", "weight_kg": "68",
    })
    assert blocked.status_code == 409
    blocked_restore = client.post(
        "/api/ops/health/months/2026-10/rows/abcdef123456/restore",
        json={"date": "2026-10-21", "weight_kg": "68"})
    assert blocked_restore.status_code == 409
