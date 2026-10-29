"""生活运营 AI：健康/财务上下文、报告落盘、生活助理建议提案。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import life_reports, structured_notes, task_reports
from backend.app.ai_gateway import HeadlessResult
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.proposals import ProposalService

HEALTH_REPLY = {
    "title": "最近 6 个月健康报告",
    "summary": "睡眠偏少",
    "sections": [{"heading": "趋势", "body": "体重稳定，睡眠偏少。"}],
    "recommendations": ["先固定入睡时间"],
    "note": {"title": "最近 6 个月健康报告", "chapter": "health_report",
             "markdown": "> 摘要\n\n体重稳定，睡眠偏少。\n"},
}

LIFE_REPLY = {
    "reply": "这周先减少两件事，把睡眠补回来。",
    "proposals": [{
        "kind": "new_task", "title": "23:30 前上床",
        "reason": "最近睡眠偏少",
        "payload": {"title": "23:30 前上床", "date": "2026-10-30", "priority": "high"},
    }],
}


class FakeRole:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def run(self, role_id, mode, **kwargs):
        self.calls.append((role_id, mode, kwargs))
        return HeadlessResult(text=self.text, session_id="session-life",
                              seconds=0.2, returncode=0, stderr_tail="")


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    structured_notes.upsert_row(vault, "health", "2026-10", {
        "date": "2026-10-29", "weight_kg": "68.2", "sleep_minutes": "360",
        "exercise_minutes": "30", "note": "睡得有点少",
    })
    structured_notes.upsert_row(vault, "finance", "2026-10", {
        "date": "2026-10-29", "kind": "支出", "amount": "32.5",
        "category": "午餐", "account": "微信", "note": "午饭",
    })
    database = Database(tmp_path / "life.sqlite3")
    database.initialize()
    return vault, database


def test_health_and_finance_context_are_read_from_monthly_tables(tmp_path):
    vault, _ = make_vault(tmp_path)
    health, health_stats = life_reports.health_context(vault)
    assert "68.2" in health and "360" in health
    assert health_stats["rows"] == 1
    finance, finance_stats = life_reports.finance_context(vault)
    assert "支出 32.50" in finance
    assert finance_stats["expense"] == 32.5


def test_health_report_lands_in_the_health_report_chapter(tmp_path):
    vault, database = make_vault(tmp_path)
    proposals = ProposalService(database)
    result = life_reports.generate_report(
        database, vault, FakeRole(json.dumps(HEALTH_REPLY, ensure_ascii=False)),
        proposals, "health")
    assert result["domain"] == "health"
    note = result["note"]
    assert note["chapter"] == "health_report"
    assert "体重稳定" in note["body"]
    assert (vault / note["path"]).is_file()
    assert result["proposals"] == []


def test_life_report_creates_action_proposals(tmp_path):
    vault, database = make_vault(tmp_path)
    proposals = ProposalService(database)
    result = life_reports.generate_report(
        database, vault, FakeRole(json.dumps(LIFE_REPLY, ensure_ascii=False)),
        proposals, "life")
    assert result["note"]["chapter"] == "life_advice"
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["kind"] == "new_task"
    assert result["proposals"][0]["status"] == "pending"


def test_life_ai_api_report_and_overview(tmp_path):
    vault, database = make_vault(tmp_path)
    app = create_app(database, vault, security_environ={}, env_file=tmp_path / ".env",
                     enable_dsh=False)
    app.state.ai_service.run = lambda *args, **kwargs: HeadlessResult(
        text=json.dumps(HEALTH_REPLY, ensure_ascii=False), session_id="session-life",
        seconds=0.1, returncode=0, stderr_tail="")
    client = TestClient(app)
    client.get("/")

    overview = client.get("/api/ops/ai/overview")
    assert overview.status_code == 200, overview.text
    assert overview.json()["health"]["stats"]["rows"] == 1

    blocked = client.post("/api/ops/ai/report", json={"domain": "health", "confirm": False})
    assert blocked.status_code == 422
    generated = client.post("/api/ops/ai/report",
                            json={"domain": "health", "confirm": True})
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["note"]["chapter"] == "health_report"
    assert (vault / body["note"]["path"]).is_file()


def test_life_ai_api_accepts_a_task_proposal(tmp_path):
    vault, database = make_vault(tmp_path)
    app = create_app(database, vault, security_environ={}, env_file=tmp_path / ".env",
                     enable_dsh=False)
    app.state.ai_service.run = lambda *args, **kwargs: HeadlessResult(
        text=json.dumps(LIFE_REPLY, ensure_ascii=False), session_id="session-life",
        seconds=0.1, returncode=0, stderr_tail="")
    client = TestClient(app)
    client.get("/")
    body = client.post("/api/ops/ai/report", json={"domain": "life", "confirm": True}).json()
    proposal = body["proposals"][0]
    applied = client.post(f"/api/proposals/{proposal['id']}/apply", json={"confirm": True})
    assert applied.status_code == 200, applied.text
    assert "23:30 前上床" in [task["title"] for task in task_reports.open_tasks(vault)]
