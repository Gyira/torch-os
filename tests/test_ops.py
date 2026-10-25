"""生活运营：月度 Markdown 表的只读浏览（2026-10-26 起写入在 Obsidian）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import structured_notes
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


def test_health_month_table_is_readable(tmp_path):
    client, vault = make_client(tmp_path)
    seeded = structured_notes.upsert_row(vault, "health", "2026-10", {
        "date": "2026-10-21", "weight_kg": "68.4", "sleep_minutes": "450",
        "exercise_minutes": "30", "note": "状态不错",
    })
    assert seeded["path"] == "02_生活运营/01_健康与体重/2026-10.md"
    assert (vault / seeded["path"]).is_file()
    assert "| 日期 |" in (vault / seeded["path"]).read_text(encoding="utf-8")

    read = client.get("/api/ops/health/months/2026-10").json()
    assert len(read["rows"]) == 1
    row = read["rows"][0]
    assert row["weight_kg"] == "68.4"
    assert row["sleep_minutes"] == "450"


def test_finance_month_summary(tmp_path):
    client, vault = make_client(tmp_path)
    for kind, amount in (("支出", "12.5"), ("收入", "100")):
        structured_notes.upsert_row(vault, "finance", "2026-10", {
            "date": "2026-10-21", "kind": kind, "amount": amount,
            "category": "餐饮", "account": "微信",
        })
    summary = client.get("/api/ops/finance/summary?months=3").json()
    assert len(summary["months"]) == 1
    assert len(summary["months"][0]["rows"]) == 2
    assert summary["months"][0]["path"].startswith("02_生活运营/02_财富与记账/")
