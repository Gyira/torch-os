"""v1.0 audit coverage (P5): every new write path leaves a count-only audit row.

The test drives writing, role chat/run, life reports, memory-hub synthesis,
identity-patch apply and proposal apply through the HTTP surface with a fake
model runner, then asserts the audit table names the events without copying any
body text, reply, portrait, identity value or API key.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.ai_gateway import HeadlessResult
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.identity import IdentityWrite, write_identity
from backend.app.journal_review import shanghai_today

WRITE_BODY = "正文-MARKER-WRITE 今天在会议上太急了。"
WRITE_BODY_2 = "正文-MARKER-WRITE-2 下次先停三秒。"
CHAT_REPLY = "回复-MARKER-CHAT 先做最小的一步。"
CHAT_INPUT = "用户-MARKER-INPUT 我是不是在讨好别人？"
RUN_REPLY = "润色-MARKER-RUN 更简洁的版本。"
REPORT_BODY = "报告-MARKER-BODY 体重稳定，睡眠偏少。"
PORTRAIT = "画像-MARKER-PORTRAIT 他最近在收尾一个长期项目。"
IDENTITY_VALUE = "诚实-MARKER-VALUE"

SYNTH = {
    "portrait": PORTRAIT,
    "observations": [{"title": "收尾模式", "text": "最近记录集中在项目收尾。"}],
    "review_cards": [{
        "kind": "observation", "title": "先做最小的一步",
        "text": "他在多篇日记里用同一句话推动自己。",
        "confidence": "high", "sources": ["S1"],
    }],
    "identity_patches": [{
        "field": "values", "from": "", "to": IDENTITY_VALUE,
        "reason": "多次提到宁可吃亏也不骗人", "sources": ["S1"],
    }],
    "sources": ["S1"],
    "confidence": 0.8,
}

LIFE_REPLY = {
    "reply": "这周先减少两件事，把睡眠补回来。",
    "note": {"title": "最近生活建议", "chapter": "life_advice", "markdown": REPORT_BODY},
    "proposals": [{
        "kind": "new_task", "title": "审计任务-MARKER-PROPOSAL", "reason": "睡眠偏少",
        "payload": {"title": "审计任务-MARKER-PROPOSAL", "date": "2026-10-30", "priority": "high"},
    }],
}


def _headless(text: str) -> HeadlessResult:
    return HeadlessResult(text=text, session_id="session-audit", seconds=0.1,
                          returncode=0, stderr_tail="")


def make_client(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    write_identity(vault, IdentityWrite(name="审计用户", revision=""))
    journal = vault / "00_人生路/01_日记/2026-10-29.md"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("---\ntype: journal\n---\n\n今天把项目收尾了，先做最小的一步。\n",
                       encoding="utf-8")
    database = Database(tmp_path / "audit.sqlite3")
    app = create_app(database, vault, security_environ={}, env_file=tmp_path / ".env",
                     enable_dsh=False)

    def fake_execute(prompt, role, **kwargs):
        return _headless(json.dumps({"reply": CHAT_REPLY}, ensure_ascii=False))

    def fake_role_run(role_id, mode, **kwargs):
        if role_id == "yiling":
            return _headless(json.dumps(SYNTH, ensure_ascii=False))
        return _headless(json.dumps(LIFE_REPLY, ensure_ascii=False))

    app.state.ai_service._execute = fake_execute
    app.state.ai_service.run = fake_role_run
    client = TestClient(app)
    client.get("/")
    return client, database, vault


def test_v1_writes_are_audited_without_bodies_or_secrets(tmp_path):
    client, database, _ = make_client(tmp_path)

    created = client.post("/api/writing/notes", json={
        "chapter": "reflection", "title": "审计反思", "body": WRITE_BODY,
    })
    assert created.status_code == 200, created.text
    note = created.json()
    updated = client.put("/api/writing/notes", json={
        "path": note["path"], "expected_revision": note["revision"], "body": WRITE_BODY_2,
    })
    assert updated.status_code == 200, updated.text
    moved = client.post("/api/writing/notes/move", json={
        "path": note["path"], "target_chapter": "essay",
        "expected_revision": updated.json()["revision"],
    })
    assert moved.status_code == 200, moved.text

    journal_day = shanghai_today().isoformat()
    journal = client.post("/api/journal/write", json={
        "day": journal_day,
        "content": f"---\ntype: journal\ndate: {journal_day}\n---\n\n# 今天\n日记-MARKER-BODY 今天写下的一句。\n",
        "confirm": True,
    })
    assert journal.status_code == 200, journal.text

    chat = client.post("/api/ai/chat", json={
        "role_id": "journal_companion", "mode": "discuss", "message": CHAT_INPUT,
    })
    assert chat.status_code == 200, chat.text
    assert CHAT_REPLY in chat.json()["reply"]

    run = client.post("/api/ai/run", json={
        "role_id": "journal_companion", "mode": "polish", "input": "把这段改简洁",
    })
    assert run.status_code == 200, run.text

    report = client.post("/api/ops/ai/report", json={"domain": "life", "confirm": True})
    assert report.status_code == 200, report.text
    proposal = report.json()["proposals"][0]
    applied = client.post(f"/api/proposals/{proposal['id']}/apply", json={"confirm": True})
    assert applied.status_code == 200, applied.text

    synthesized = client.post("/api/memory-hub/synthesize",
                              json={"confirm": True, "range_days": 7})
    assert synthesized.status_code == 200, synthesized.text
    patches = client.get("/api/memory-hub/overview").json()["identity_patches"]
    identity_applied = client.post(f"/api/proposals/{patches[0]['id']}/apply",
                                   json={"confirm": True})
    assert identity_applied.status_code == 200, identity_applied.text

    with database.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT event_type, detail_json FROM audit_events ORDER BY id")]
    details = {row["event_type"]: json.loads(row["detail_json"]) for row in rows}
    assert set(details) >= {
        "note_created", "note_updated", "note_moved", "journal_saved",
        "ai_turn_completed", "ai_run_completed",
        "ops_ai_report", "proposal_created", "proposal_applied",
        "memory_hub_synthesized", "identity_patch_applied",
    }

    # 审计只留元数据：正文、回复、画像、身份值、提案标题与 Key 都不能出现。
    dumped = "\n".join(row["detail_json"] for row in rows)
    for marker in (WRITE_BODY, WRITE_BODY_2, CHAT_REPLY, CHAT_INPUT, RUN_REPLY,
                   REPORT_BODY, PORTRAIT, IDENTITY_VALUE, "审计任务-MARKER-PROPOSAL",
                   "日记-MARKER-BODY"):
        assert marker not in dumped, marker
    assert "sk-" not in dumped

    assert set(details["note_created"]) == {"path", "chapter"}
    assert set(details["note_updated"]) == {"path", "chapter"}
    assert set(details["note_moved"]) == {"path", "from", "to", "index_updated"}
    assert set(details["journal_saved"]) == {"day", "bytes", "created"}
    assert set(details["ai_turn_completed"]) == {
        "role", "mode", "thread_id", "chars", "seconds", "proposals", "events", "structured",
    }
    assert set(details["ai_run_completed"]) == {
        "role", "mode", "chars", "seconds", "structured",
    }
    assert set(details["ops_ai_report"]) == {"domain", "role", "path", "proposals"}
    assert set(details["proposal_created"]) == {
        "proposal_id", "kind", "role_id", "sources",
    }
    assert set(details["proposal_applied"]) == {"proposal_id", "kind", "role_id"}
    assert set(details["memory_hub_synthesized"]) == {
        "run_id", "range_days", "events", "cards", "patches",
    }
    assert set(details["identity_patch_applied"]) == {
        "fields", "revision", "proposal_id",
    }
