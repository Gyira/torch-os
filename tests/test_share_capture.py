import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import share_capture, share_link, pillar_notes, note_editor, backup
from backend.app.context_pack import PackRequest, build
from backend.app.db import Database
from backend.app.knowledge_share import create_knowledge_share_router
from backend.app.share_store import ShareStore


@pytest.fixture
def workspace(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "state" / "app.sqlite3")
    db.initialize()
    return vault, db


def page():
    mapping = {
        "a": {"parent": None, "message": {"author": {"role": "user"}, "content": {"content_type": "text", "parts": ["问题 中文"]}}},
        "b": {"parent": "a", "message": {"status": "finished_successfully", "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["```py\nprint(1)\n```\n\n|a|b|\n|-|-|\n|1|2|"]}}},
    }
    return ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"serverResponse": {"data": {"mapping": mapping, "current_node": "b", "title": "合成样例"}}}}}) + '</script>').encode()


@pytest.mark.parametrize("url", ["http://chatgpt.com/share/abcd", "https://chatgpt.com.evil/share/abcd",
    "https://user@chatgpt.com/share/abcd", "https://127.0.0.1/share/abcd", "https://chatgpt.com:22/share/abcd",
    "https://chatgpt.com/other/abcd", "https://chatgpt.com/share/abcd/extra"])
def test_reject_url(url):
    with pytest.raises(share_link.ShareLinkError):
        share_capture.identify(url)


def test_chatgpt_and_answers_only():
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=page()))) as client:
        result = share_capture.capture("https://chatgpt.com/share/abcd", answers_only=True, client=client)
    assert "print(1)" in result["markdown"] and "问题 中文" not in result["markdown"]
    assert result["model"] == "unknown" and result["completeness"] == "complete"


def test_unknown_page_and_redirect_fail_closed():
    for response in [httpx.Response(302, headers={"location": "http://127.0.0.1/"}), httpx.Response(200, text="<html>Login</html>")]:
        with httpx.Client(transport=httpx.MockTransport(lambda req: response)) as client:
            with pytest.raises(share_link.ShareLinkError):
                share_capture.capture("https://chatgpt.com/share/abcd", client=client)


def test_deepseek_compatibility():
    payload = {"code": 0, "data": {"biz_data": {"messages": [
        {"role": "USER", "fragments": [{"type": "REQUEST", "content": "问题"}]},
        {"role": "ASSISTANT", "fragments": [{"type": "THINK", "content": "隐藏思考"}, {"type": "RESPONSE", "content": "回答"}]}]}}}
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        result = share_capture.capture("https://chat.deepseek.com/share/abcd", client=client)
    assert "隐藏思考" not in result["markdown"] and result["turns"] == 1


def test_archive_snapshot_no_refetch_and_duplicate(workspace):
    vault, db = workspace
    app = FastAPI()
    app.include_router(create_knowledge_share_router(db, vault))
    with TestClient(app) as client:
        preview = client.post("/api/knowledge/share/preview", json={"pasted_text": "原文 **Markdown**"}).json()
        args = {"preview_id": preview["preview_id"], "content_hash": preview["content_hash"], "confirm": True}
        with patch.object(share_capture, "capture", side_effect=AssertionError("must not fetch")):
            a = client.post("/api/knowledge/share/archive", json=args)
            b = client.post("/api/knowledge/share/archive", json=args)
        assert a.status_code == b.status_code == 200, a.text
        assert a.json()["note"]["path"] == b.json()["note"]["path"]
        assert b.json()["duplicate"]
        assert len(list(vault.rglob("*.md"))) == 1
        assert client.post("/api/knowledge/share/archive", json=dict(args, content_hash="bad")).status_code == 409


def test_archive_recovers_file_written_before_ledger_commit(workspace):
    vault, db = workspace
    store = ShareStore(db, vault)
    result = share_capture.capture(pasted_text="正文")
    real_create = note_editor.create_note
    def interrupted(*a, **kw):
        real_create(*a, **kw)
        raise RuntimeError("simulated crash after file write")
    with patch.object(note_editor, "create_note", side_effect=interrupted), pytest.raises(RuntimeError):
        store.archive(result, chapter="inbox", title="测试")
    recovered = ShareStore(db, vault).archive(result, chapter="inbox", title="测试")
    assert recovered["duplicate"] and len(list(vault.rglob("*.md"))) == 1


def test_preview_persists_and_expires(workspace):
    vault, db = workspace
    saved = ShareStore(db, vault).save_preview(share_capture.capture(pasted_text="原文"))
    assert ShareStore(db, vault).load(saved["preview_id"])["markdown"] == saved["markdown"]
    with ShareStore(db, vault).connect() as con:
        con.execute("UPDATE previews SET created=0")
    with pytest.raises(HTTPException):
        ShareStore(db, vault).load(saved["preview_id"])


def test_pack_readonly_exclusion_budget_and_secret(workspace):
    vault, db = workspace
    note = pillar_notes.create_note(vault, chapter="inbox", title="资料", body="原始内容")
    source = vault / note["path"]
    before = source.read_bytes()
    result = build(vault, PackRequest(paths=[note["path"]]))
    assert source.read_bytes() == before and not result["uploaded"]
    assert "原始内容" in result["files"][0]["markdown"]
    for path in ["../x.md", ".torch-os/key.md", "03_知识库/备份/a.md", "02_生活运营/02_微信聊天/a.md"]:
        with pytest.raises(HTTPException):
            build(vault, PackRequest(paths=[path]))
    source.write_text("api_key: sk-" + "x" * 40, encoding="utf-8")
    with pytest.raises(HTTPException):
        build(vault, PackRequest(paths=[note["path"]]))


def test_pack_changed_after_preview_rejected(workspace):
    vault, db = workspace
    note = pillar_notes.create_note(vault, chapter="inbox", title="背景", body="旧内容")
    app = FastAPI()
    app.include_router(create_knowledge_share_router(db, vault))
    with TestClient(app) as client:
        payload = {"paths": [note["path"]]}
        preview = client.post("/api/knowledge/context-pack/preview", json=payload).json()
        (vault / note["path"]).write_text("新内容", encoding="utf-8")
        payload["expected_hash"] = preview["source_hash"]
        assert client.post("/api/knowledge/context-pack/export", json=payload).status_code == 409


def test_existing_backup_restores_to_separate_directory(workspace, tmp_path):
    vault, db = workspace
    note = pillar_notes.create_note(vault, chapter="inbox", title="恢复", body="合成测试正文")
    result = backup.create_backup(db, vault, tmp_path / "backups")
    assert result
    package = next((tmp_path / "backups").glob("*.zip"))
    assert backup.verify_backup(package)
    target = tmp_path / "restored" / "copy"
    target.parent.mkdir()
    restored = backup.restore_backup(package, target, allowed_parent=tmp_path / "restored")
    assert restored and (vault / note["path"]).exists()


def test_concurrent_archive_retries_produce_one_note(workspace):
    from concurrent.futures import ThreadPoolExecutor
    vault, db = workspace
    result = share_capture.capture(pasted_text="同时归档")
    def archive(_):
        return ShareStore(db, vault).archive(result, chapter="inbox", title="合成")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(archive, range(2)))
    assert outputs[0]["note"]["path"] == outputs[1]["note"]["path"]
    assert len(list(vault.rglob("*.md"))) == 1


def test_full_pack_export_download_and_no_upload(workspace):
    vault, db = workspace
    note = pillar_notes.create_note(vault, chapter="inbox", title="资料", body="内容")
    app = FastAPI()
    app.include_router(create_knowledge_share_router(db, vault))
    with TestClient(app) as client:
        payload = {"paths": [note["path"]]}
        result = client.post("/api/knowledge/context-pack/preview", json=payload).json()
        payload["expected_hash"] = result["source_hash"]
        exported = client.post("/api/knowledge/context-pack/export", json=payload)
        assert exported.status_code == 200, exported.text
        data = exported.json()
        assert data["uploaded"] is False
        assert "内容" in client.get(data["files"][0]["url"]).text
        assert client.get(f"/api/knowledge/context-pack/{data['pack_id']}/manifest.json").status_code == 404
        assert client.get("/api/knowledge/context-pack/status").json()["stale"] is False
        (vault / note["path"]).write_text("修改后的背景", encoding="utf-8")
        assert client.get("/api/knowledge/context-pack/status").json()["stale"] is True


def test_body_change_same_source_creates_new_version(workspace):
    vault, db = workspace
    store = ShareStore(db, vault)
    first = store.archive(share_capture.capture(pasted_text="第一版"), chapter="inbox", title="第一版")
    second = store.archive(share_capture.capture(pasted_text="第二版"), chapter="inbox", title="第二版")
    assert first["note"]["path"] != second["note"]["path"]
    assert "第一版" in pillar_notes.read_note(vault, first["note"]["path"])["body"]


def test_atomic_note_creation_failure_publishes_no_partial_note(workspace):
    vault, _ = workspace
    with patch("backend.app.pillar_notes.os.link", side_effect=OSError("disk failure")), pytest.raises(OSError):
        pillar_notes.create_note(vault, chapter="inbox", title="未完成", body="完整正文")
    assert not list(vault.rglob("*.md"))
    assert not list(vault.rglob("*.tmp"))


def test_large_response_is_bounded():
    with patch.object(share_capture, "MAX_RESPONSE", 10):
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 20))) as client:
            with pytest.raises(share_link.ShareLinkError):
                share_capture.capture("https://chatgpt.com/share/abcd", client=client)


def test_readonly_archive_and_export_are_rejected(workspace):
    vault, db = workspace
    app = FastAPI()
    app.include_router(create_knowledge_share_router(db, vault, read_only=True))
    with TestClient(app) as client:
        preview = client.post("/api/knowledge/share/preview", json={"pasted_text": "正文"}).json()
        assert client.post("/api/knowledge/share/archive", json={"preview_id": preview["preview_id"],
            "content_hash": preview["content_hash"], "confirm": True}).status_code == 409
        assert client.post("/api/knowledge/context-pack/export", json={"paths": ["03_知识库/a.md"]}).status_code == 409
