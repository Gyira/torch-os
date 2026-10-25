"""薪火管家：健康检查与确定性提案（2026-10-26 起只读，执行在 Obsidian）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import pillar_notes
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


def add_note(vault, **payload):
    return pillar_notes.create_note(vault, **payload)


def test_health_check_flags_structure_issues(tmp_path):
    client, vault, _ = make_client(tmp_path)
    old = add_note(vault, title="旧收件", body="见 [[不存在的笔记]]", date="2020-01-01")
    empty = add_note(vault, title="空笔记", body="")
    first = add_note(vault, title="同名笔记", body="一")
    second = add_note(vault, title="同名笔记", body="二")
    legacy = vault / "00_人生路" / "01_日记" / "2026-10-20.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("没有 frontmatter 的日记。\n", encoding="utf-8")
    mismatch = vault / "00_人生路" / "02_反思复盘" / "01_反思" / "错位的反思.md"
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


def test_proposals_triage_inbox_notes_without_executing(tmp_path):
    client, vault, _ = make_client(tmp_path)
    method = add_note(vault, title="整理收件箱五步法", body="第一步：列清单；第二步：分类。")
    reading = add_note(vault, title="读书笔记：原则", body="作者说要做减法。")

    proposals = client.get("/api/steward/proposals").json()["items"]
    by_path = {item["path"]: item for item in proposals}
    assert by_path[method["path"]]["action"] == "move"
    assert by_path[method["path"]]["target_chapter"] == "method_general"
    assert by_path[method["path"]]["requires_confirmation"] is True
    assert by_path[reading["path"]]["target_chapter"] == "reading"

    suggestions = client.get("/api/steward/inbox-suggestions").json()["items"]
    assert {item["path"] for item in suggestions} == {method["path"], reading["path"]}
    assert all(item["proposal_id"].startswith("st-") for item in suggestions)
    # 只生成提案，源笔记仍在收件箱。
    assert (vault / method["path"]).is_file() and (vault / reading["path"]).is_file()


def test_distill_proposal_is_generated_for_a_reflection_with_a_cue(tmp_path):
    client, vault, _ = make_client(tmp_path)
    source = add_note(vault, title="今天的教训", body="我学到：先完成再完美。下次先列清单。",
                      pillar="life", chapter="reflection", note_type="reflection")
    proposals = client.get("/api/steward/proposals").json()["items"]
    proposal = next(item for item in proposals
                    if item["kind"] == "distill" and item["path"] == source["path"])
    assert proposal["target_chapter"] == "distill_work"
    assert proposal["target_pillar"] == "self"
    assert (vault / source["path"]).is_file()  # 只给建议，不改原文
