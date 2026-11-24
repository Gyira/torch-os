import time

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import self_archive
from backend.app.self_api import create_self_router


def client_for(vault, service=None, *, read_only=False):
    app = FastAPI()
    app.include_router(create_self_router(vault, role_service=service, read_only=read_only))
    return TestClient(app)


def test_windows_extended_names_compare_as_the_same_vault_folder(tmp_path, monkeypatch):
    import os
    import pytest
    from pathlib import Path
    from backend.app.vault import safe_note_path
    if os.name != "nt":
        pytest.skip("Windows path spelling")
    candidate = tmp_path / "基础事实"
    resolve = Path.resolve
    def mixed_names(path, *args, **kwargs):
        result = resolve(path, *args, **kwargs)
        return Path("//?/" + result.as_posix()) if path == candidate else result
    monkeypatch.setattr(Path, "resolve", mixed_names)
    assert safe_note_path(tmp_path, "基础事实") == candidate


def test_manual_revision_keeps_history_and_rejects_stale_edit(tmp_path):
    client = client_for(tmp_path)
    payload = dict(book_key="self_behavior", entry_type="behavior", title="初稿", text="我的观察",
                   operation_id="manual-test-create")
    entry = client.post("/api/self/entries", json=payload).raise_for_status().json()
    again = client.post("/api/self/entries", json=payload).raise_for_status().json()
    assert again["entry_id"] == entry["entry_id"] and entry["origin"] == "user"
    route = "/api/self/entries/" + entry["entry_id"]
    correction = dict(payload, operation_id="manual-test-edit", title="修正", base_revision="stale")
    assert client.put(route, json=correction).status_code == 409
    correction["base_revision"] = entry["revision"]
    new = client.put(route, json=correction).raise_for_status().json()
    assert new["supersedes"] == entry["entry_id"]
    assert client.get(route).json()["status"] == "superseded"
    assert client.get("/api/self/overview").json()["revised"][0]["title"] == "修正"
    assert client.post("/api/self/entries/" + new["entry_id"] + "/retire",
                       json={"revision": new["revision"]}).raise_for_status().json()["status"] == "archived"
    assert client.get("/api/self/books/self_behavior").json()["entries"] == []
    assert client_for(tmp_path, read_only=True).post("/api/self/entries", json=payload).is_error


def test_full_organization_uses_early_and_late_sources_and_writes_once(tmp_path):
    from threading import Event
    started, release = Event(), Event()
    prompts = []

    class Model:
        def archive_completion(self, prompt, *, final=False):
            prompts.append(prompt)
            started.set()
            assert release.wait(5)
            if not final:
                return "整理的个人经历 [S1]"
            return '{"portrait":"曾经搬家。","portrait_confidence":0.8,"memory_updates":[{"kind":"timeline","text":"搬家","sources":["S1"]}]}'

    for root in ("人生路", "自我档案", "生活运营", "知识库"):
        folder = tmp_path / root
        folder.mkdir()
        (folder / "2020.md").write_text("旧笔记-" + root, encoding="utf-8")
    (tmp_path / "人生路/长文.md").write_text("甲" * 55000 + "长文最后一句", encoding="utf-8")
    folder = tmp_path / "知识库/_附件"
    folder.mkdir()
    doc = Document()
    doc.add_paragraph("我的毕业论文答辩")
    doc.save(folder / "答辩.docx")
    (folder / "不可读.pdf").write_bytes(b"invalid")
    client = client_for(tmp_path, Model())
    job = client.post("/api/self/organize", json={"confirm": True}).raise_for_status().json()
    assert started.wait(5)
    duplicate = client.post("/api/self/organize", json={"confirm": True}).json()
    assert duplicate["id"] == job["id"]
    release.set()
    for _ in range(200):
        job = client.get("/api/self/organize").json()
        if job["state"] != "running":
            break
        time.sleep(0.03)
    assert job["state"] == "done", job
    assert "不可读.pdf" in job["skipped"][0]
    corpus = "\n".join(prompts)
    for text in ("旧笔记-人生路", "旧笔记-自我档案", "旧笔记-生活运营", "旧笔记-知识库", "长文最后一句", "我的毕业论文答辩"):
        assert text in corpus
    assert self_archive.read_portrait(tmp_path)["text"] == "曾经搬家。"

