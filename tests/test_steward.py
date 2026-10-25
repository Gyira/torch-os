"""薪火管家：健康检查、确定性提案与确认执行。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def make_client(tmp_path, *, read_only=False):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    database = Database(tmp_path / "torch_os.sqlite3")
    environ = {"VAULT_READ_ONLY": "1"} if read_only else {}
    app = create_app(
        database, vault,
        security_environ=environ, env_file=tmp_path / ".env")
    client = TestClient(app)
    client.get("/")
    return client, vault, database


def capture(client, **payload):
    response = client.post("/api/capture", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_check_flags_structure_issues(tmp_path):
    client, vault, _ = make_client(tmp_path)
    old = capture(client, title="旧收件", body="见 [[不存在的笔记]]", date="2020-01-01")
    empty = capture(client, title="空笔记", body="")
    first = capture(client, title="同名笔记", body="一")
    second = capture(client, title="同名笔记", body="二")
    legacy = vault / "00_人生路" / "01_日记" / "2026-10-20.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("没有 frontmatter 的日记。\n", encoding="utf-8")
    mismatch = vault / "00_人生路" / "02_反思" / "错位的反思.md"
    mismatch.parent.mkdir(parents=True, exist_ok=True)
    mismatch.write_text(
        "---\ntype: reflection\ntitle: 错位的反思\npillar: self\nchapter: idea\n---\n\n正文。\n",
        encoding="utf-8")
    outside = vault / "03_知识库" / "根级笔记.md"
    outside.write_text(
        "---\ntype: note\ntitle: 根级笔记\n---\n\n正文。\n", encoding="utf-8")

    payload = client.get("/api/steward/health").json()
    kinds = {item["kind"] for item in payload["items"]}
    assert payload["checked"] >= 7
    assert {"frontmatter", "inbox_stale", "broken_link", "empty",
            "duplicate_title", "pillar_mismatch", "chapter_mismatch",
            "outside_chapter"} <= kinds
    assert payload["counts"]["warning"] >= 1
    paths = {item["path"] for item in payload["items"]}
    assert old["path"] in paths and empty["path"] in paths
    assert first["path"] in paths and second["path"] in paths
    assert "00_人生路/01_日记/2026-10-20.md" in paths
    assert payload["note"].startswith("只检查四库")


def test_proposals_apply_requires_confirmation_and_moves(tmp_path):
    client, vault, database = make_client(tmp_path)
    note = capture(client, title="整理收件箱五步法", body="第一步：列清单；第二步：分类。")
    proposals = client.get("/api/steward/proposals").json()["items"]
    proposal = next(item for item in proposals if item["path"] == note["path"])
    assert proposal["action"] == "move"
    assert proposal["target_chapter"] == "method_general"
    assert proposal["requires_confirmation"] is True

    blocked = client.post("/api/steward/proposals/apply", json={
        "proposal_id": proposal["id"], "confirm": False,
    })
    assert blocked.status_code == 422
    assert (vault / note["path"]).is_file()

    applied = client.post("/api/steward/proposals/apply", json={
        "proposal_id": proposal["id"], "confirm": True,
    })
    assert applied.status_code == 200, applied.text
    moved = applied.json()["note"]
    assert moved["chapter"] == "method_general"
    assert not (vault / note["path"]).exists()
    assert (vault / moved["path"]).is_file()

    with database.connect() as connection:
        row = connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type = ?",
            ("steward_proposal_applied",)).fetchone()
    assert row is not None
    detail = json.loads(row[0])
    assert detail["proposal_id"] == proposal["id"]
    assert detail["result_path"] == moved["path"]
    assert "body" not in detail


def test_batch_apply_moves_selected_and_reports_stale(tmp_path):
    client, vault, database = make_client(tmp_path)
    method = capture(client, title="整理收件箱五步法", body="第一步：列清单；第二步：分类。")
    reading = capture(client, title="读书笔记：原则", body="作者说要做减法。")
    suggestions = client.get("/api/steward/inbox-suggestions").json()["items"]
    ids = {item["path"]: item["proposal_id"] for item in suggestions}
    assert set(ids) == {method["path"], reading["path"]}
    assert all(value.startswith("st-") for value in ids.values())

    result = client.post("/api/steward/proposals/apply-batch", json={
        "proposal_ids": [ids[method["path"]], "st-missing", ids[reading["path"]]],
        "confirm": True,
    })
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["applied_count"] == 2
    assert payload["failed_count"] == 1
    assert payload["failed"][0]["proposal_id"] == "st-missing"
    assert not (vault / method["path"]).exists()
    assert not (vault / reading["path"]).exists()
    remaining = client.get("/api/steward/inbox-suggestions").json()["items"]
    assert remaining == []

    with database.connect() as connection:
        per_item = connection.execute(
            "SELECT count(*) FROM audit_events WHERE event_type = ?",
            ("steward_proposal_applied",)).fetchone()[0]
        batches = connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type = ?",
            ("steward_batch_applied",)).fetchall()
    assert per_item == 2
    assert len(batches) == 1
    assert json.loads(batches[0][0]) == {"requested": 3, "applied": 2, "failed": 1}


def test_batch_apply_requires_confirmation_unique_ids_and_write_access(tmp_path):
    client, _, _ = make_client(tmp_path)
    capture(client, title="整理收件箱五步法", body="第一步：列清单。")
    proposal_id = client.get("/api/steward/inbox-suggestions").json()["items"][0]["proposal_id"]

    blocked = client.post("/api/steward/proposals/apply-batch", json={
        "proposal_ids": [proposal_id], "confirm": False,
    })
    assert blocked.status_code == 422
    duplicate = client.post("/api/steward/proposals/apply-batch", json={
        "proposal_ids": [proposal_id, proposal_id], "confirm": True,
    })
    assert duplicate.status_code == 422

    read_only_client, _, _ = make_client(tmp_path / "readonly", read_only=True)
    read_only = read_only_client.post("/api/steward/proposals/apply-batch", json={
        "proposal_ids": [proposal_id], "confirm": True,
    })
    assert read_only.status_code == 409


def test_distill_proposal_creates_principle_with_source_link(tmp_path):
    client, vault, _ = make_client(tmp_path)
    source = capture(client, title="今天的教训", body="我学到：先完成再完美。下次先列清单。",
                     pillar="life", chapter="reflection", note_type="reflection")
    proposals = client.get("/api/steward/proposals").json()["items"]
    proposal = next(item for item in proposals
                    if item["kind"] == "distill" and item["path"] == source["path"])
    assert proposal["target_chapter"] == "distill_work"

    applied = client.post("/api/steward/proposals/apply", json={
        "proposal_id": proposal["id"], "confirm": True,
    })
    assert applied.status_code == 200, applied.text
    principle = applied.json()["note"]
    assert principle["pillar"] == "self"
    assert principle["chapter"] == "distill_work"
    assert principle["source"] == source["path"]
    assert f"[[{source['path']}|" in principle["body"]
    assert (vault / source["path"]).is_file()  # 原文保留，不移动

    again = client.post("/api/steward/proposals/apply", json={
        "proposal_id": proposal["id"], "confirm": True,
    })
    assert again.status_code == 409
    remaining = client.get("/api/steward/proposals").json()["items"]
    assert not any(item["path"] == source["path"] and item["kind"] == "distill"
                   for item in remaining)


def test_apply_unknown_proposal_and_read_only(tmp_path):
    client, _, _ = make_client(tmp_path)
    missing = client.post("/api/steward/proposals/apply", json={
        "proposal_id": "st-not-found", "confirm": True,
    })
    assert missing.status_code == 409

    read_only_client, _, _ = make_client(tmp_path / "readonly", read_only=True)
    blocked = read_only_client.post("/api/steward/proposals/apply", json={
        "proposal_id": "st-not-found", "confirm": True,
    })
    assert blocked.status_code == 409
