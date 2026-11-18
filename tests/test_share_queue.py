import json
from uuid import uuid4
from backend.app.db import Database
from backend.app.storage_layout import new_layout, initialize_empty_workspace
from backend.app.share_queue import ShareQueue, envelope, decode
from backend.app.share_capture import capture


def setup(tmp_path):
    layout = new_layout(tmp_path / "split")
    initialize_empty_workspace(layout)
    db = Database(layout.state / "main.sqlite3"); db.initialize()
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return capture(url, pasted_text="保留原文")
    queue = ShareQueue(db, layout.notes, layout=layout, fetch=fetch)
    return layout, queue, calls


def request(layout, action, **fields):
    value = dict(schema_version=1, request_id=str(uuid4()), device_id=str(uuid4()), workspace_id=layout.workspace_id,
                 created_at="2026-11-15T00:00:00Z", action=action, **fields)
    path = layout.notes / "_Torch/收件" / (value["request_id"] + ".md")
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(envelope(value), encoding="utf-8")
    return value


def receipt(layout, rid):
    return decode((layout.notes / "_Torch/回执" / (rid + ".md")).read_text(encoding="utf-8"))


def test_preview_does_not_archive_and_commit_uses_exact_snapshot_once(tmp_path):
    layout, queue, calls = setup(tmp_path)
    preview = request(layout, "share_preview", source_url="https://chatgpt.com/share/test-share")
    assert queue.run_once()["processed"] == 1
    data = receipt(layout, preview["request_id"])
    assert data["status"] == "preview_ready" and not list(layout.notes.glob("知识库/*/*.md"))
    commit = request(layout, "share_commit", preview_request=preview["request_id"], content_hash=data["content_hash"], chapter="inbox")
    assert queue.run_once()["errors"] == 0
    assert receipt(layout, commit["request_id"])["status"] == "archived"
    queue.run_once()
    assert len(calls) == 1
    assert len(list(layout.notes.glob("知识库/*/*.md"))) == 1


def test_out_of_order_confirmation_waits_and_cancellation_prevents_fetch(tmp_path):
    layout, queue, calls = setup(tmp_path)
    pending = request(layout, "share_commit", preview_request=str(uuid4()), content_hash="a" * 64)
    preview = request(layout, "share_preview", source_url="https://chatgpt.com/share/test-share")
    request(layout, "cancel", target=preview["request_id"])
    queue.run_once()
    assert not calls
    assert receipt(layout, preview["request_id"])["status"] == "cancelled"
    assert not (layout.notes / "_Torch/回执" / (pending["request_id"] + ".md")).exists()


def test_lost_receipt_publish_recovers_and_mutated_request_is_rejected(tmp_path):
    layout, queue, calls = setup(tmp_path)
    preview = request(layout, "share_preview", source_url="https://chatgpt.com/share/test-share")
    original = queue._publish
    queue._publish = lambda *args: (_ for _ in ()).throw(OSError("lost"))
    assert queue.run_once()["errors"] == 1
    queue._publish = original
    queue.run_once()
    assert receipt(layout, preview["request_id"])["status"] == "preview_ready" and len(calls) == 1
    path = layout.notes / "_Torch/收件" / (preview["request_id"] + ".md")
    path.write_text(envelope(dict(preview, source_url="https://chatgpt.com/share/different")), encoding="utf-8")
    assert queue.run_once()["errors"] == 1 and len(calls) == 1


def test_finished_requests_do_not_starve_new_requests(tmp_path):
    layout, queue, calls = setup(tmp_path)
    for _ in range(4):
        request(layout, "share_preview", source_url="https://chatgpt.com/share/test-share")
    for _ in range(4): queue.run_once(limit=1)
    assert len(calls) == 4
