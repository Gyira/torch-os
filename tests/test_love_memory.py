"""每日聊天记忆：AI 起草、人工确认、写入生活运营章节。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import love as love_module
from backend.app import vault_layout
from backend.app.db import Database
from backend.app.love import LoveStore, create_love_router

SAMPLE = """2026-05-20 10:00:00 我: 你好，我的电话是13800138000
2026-05-20 10:01:00 小林: 嗯嗯，今天做什么
2026-05-20 10:02:00 我: 明天见面吧
2026-05-21 09:00:00 小林: 好呀
2026-05-21 09:01:00 我: 那说定了
"""

MEMORY = {
    "title": "雨天和见面约定",
    "summary": "今天和对象聊了日常，也约好明天见面。",
    "moments": [
        {"time": "10:00", "text": "对象问我今天做什么"},
        {"time": "10:02", "text": "我约对象明天见面"},
    ],
    "plans": ["明天见面"],
    "feelings": ["期待"],
    "keywords": ["日常", "见面", "雨天"],
}


def build_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
                 vault: bool = True, read_only: bool = False) -> tuple[TestClient, LoveStore]:
    store = LoveStore(tmp_path / "love" / "love.db", key_file=tmp_path / "love" / ".key")
    store.ensure_schema()
    app = FastAPI()
    vault_dir = tmp_path / "vault"
    if vault:
        vault_layout.ensure_layout(vault_dir)
    app.include_router(create_love_router(
        store, vault_dir=vault_dir if vault else None, read_only=read_only,
    ))
    return TestClient(app), store


def setup_project(client: TestClient) -> str:
    key = client.post("/api/love/settings/api-key", json={"key": "test-key"})
    assert key.status_code == 200, key.text
    imported = client.post("/api/love/import/text", json={
        "content": SAMPLE, "file_name": "sample.txt", "project_name": "测试项目",
    })
    assert imported.status_code == 200, imported.text
    return imported.json()["project_id"]


def fake_ai(captured: list[list[dict[str, str]]]):
    def call(_settings, _api_key, messages, *, max_tokens=4096, temperature=0.7):
        captured.append(messages)
        return json.dumps(MEMORY, ensure_ascii=False), {
            "prompt_tokens": 12, "completion_tokens": 34,
        }
    return call


def test_memory_days_lists_dates_and_drafts(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    project_id = setup_project(client)
    monkeypatch.setattr(love_module, "_call_deepseek", fake_ai([]))
    response = client.get(f"/api/love/memory/days?project_id={project_id}")
    assert response.status_code == 200, response.text
    days = response.json()["days"]
    assert [item["date"] for item in days] == ["2026-05-21", "2026-05-20"]
    assert days[0]["message_count"] == 2
    assert days[1]["message_count"] == 3
    assert all(item["status"] == "" for item in days)

    generated = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    })
    assert generated.status_code == 200, generated.text
    days = client.get(f"/api/love/memory/days?project_id={project_id}").json()["days"]
    assert days[1]["status"] == "draft"
    assert days[1]["draft_id"] == generated.json()["id"]


def test_generate_draft_edits_and_writes_note(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    project_id = setup_project(client)
    captured: list[list[dict[str, str]]] = []
    monkeypatch.setattr(love_module, "_call_deepseek", fake_ai(captured))

    response = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    })
    assert response.status_code == 200, response.text
    draft = response.json()
    draft_id = draft["id"]
    assert draft["status"] == "draft"
    assert draft["date"] == "2026-05-20"
    assert draft["content"]["title"] == MEMORY["title"]
    assert draft["target_exists"] is False
    assert "source: torch-love" in draft["markdown"]
    # 送给模型的记录已脱敏，并使用「我 / 对象」。
    prompt = "\n".join(item["content"] for item in captured[0])
    assert "13800138000" not in prompt
    assert "小林" not in prompt
    assert "对象" in prompt
    # 写入四库的摘要使用对象的真实称呼，而不是「对象」。
    assert "小林" in draft["markdown"]
    assert "对象" not in draft["markdown"]

    edited = draft["markdown"].replace("期待", "很期待明天")
    updated = client.put(f"/api/love/memory/drafts/{draft_id}", json={"markdown": edited})
    assert updated.status_code == 200, updated.text
    assert "很期待明天" in updated.json()["markdown"]

    written = client.post(f"/api/love/memory/drafts/{draft_id}/write", json={})
    assert written.status_code == 200, written.text
    result = written.json()
    assert result["status"] == "written"
    assert result["note_path"] == "02_生活运营/05_情感与记忆/2026-05-20 恋爱记忆.md"
    path = tmp_path / "vault" / result["note_path"]
    text = path.read_text(encoding="utf-8")
    assert "很期待明天" in text
    assert text.startswith("---\n")
    assert "source: torch-love" in text
    assert "type: memory" in text
    assert "13800138000" not in text
    # 草稿的 markdown 也同步为最终落盘版本。
    assert "source: torch-love" in result["markdown"]

    again = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    })
    assert again.status_code == 409
    rewrite = client.post(f"/api/love/memory/drafts/{draft_id}/write", json={})
    assert rewrite.status_code == 409
    overwritten = client.post(
        f"/api/love/memory/drafts/{draft_id}/write", json={"overwrite": True}
    )
    assert overwritten.status_code == 200, overwritten.text


def test_write_refuses_to_overwrite_user_note(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    project_id = setup_project(client)
    monkeypatch.setattr(love_module, "_call_deepseek", fake_ai([]))
    draft = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    }).json()
    target = tmp_path / "vault" / draft["target_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# 我自己写的笔记\n\n不要覆盖。\n", encoding="utf-8")

    blocked = client.post(f"/api/love/memory/drafts/{draft['id']}/write", json={
        "overwrite": True,
    })
    assert blocked.status_code == 409
    assert "torch" in blocked.json()["detail"]
    assert "不要覆盖" in target.read_text(encoding="utf-8")


def test_write_requires_vault_and_obeys_read_only(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch, vault=False)
    project_id = setup_project(client)
    monkeypatch.setattr(love_module, "_call_deepseek", fake_ai([]))
    draft = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    }).json()
    assert client.post(
        f"/api/love/memory/drafts/{draft['id']}/write", json={}
    ).status_code == 503

    read_only_client, _store = build_client(tmp_path / "ro", monkeypatch, read_only=True)
    project_id = setup_project(read_only_client)
    draft = read_only_client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    }).json()
    blocked = read_only_client.post(
        f"/api/love/memory/drafts/{draft['id']}/write", json={}
    )
    assert blocked.status_code == 403


def test_draft_delete_and_invalid_ai_output(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    client, _store = build_client(tmp_path, monkeypatch)
    project_id = setup_project(client)
    monkeypatch.setattr(
        love_module, "_call_deepseek",
        lambda *_args, **_kwargs: ("不是 JSON", {"prompt_tokens": 1, "completion_tokens": 1}),
    )
    bad = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    })
    assert bad.status_code == 502

    monkeypatch.setattr(love_module, "_call_deepseek", fake_ai([]))
    draft = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    }).json()
    deleted = client.delete(f"/api/love/memory/drafts/{draft['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/love/memory/drafts/{draft['id']}").status_code == 404


def test_written_memory_is_indexed_without_auto_candidates(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LoveStore(tmp_path / "love" / "love.db", key_file=tmp_path / "love" / ".key")
    store.ensure_schema()
    vault = tmp_path / "vault"
    vault_layout.ensure_layout(vault)
    database = Database(tmp_path / "torch_os.sqlite3")
    database.initialize()
    app = FastAPI()
    app.include_router(create_love_router(store, vault_dir=vault, database=database))
    client = TestClient(app)
    project_id = setup_project(client)
    monkeypatch.setattr(love_module, "_call_deepseek", fake_ai([]))
    draft = client.post("/api/love/memory/generate", json={
        "project_id": project_id, "date": "2026-05-20",
    }).json()
    written = client.post(f"/api/love/memory/drafts/{draft['id']}/write", json={})
    assert written.status_code == 200, written.text
    documents = database.list_documents()
    document = next(item for item in documents if item["path"] == written.json()["note_path"])
    assert document["title"].startswith("2026-05-20")
    assert database.list_memories("candidate") == []


def test_love_memory_chapter_is_additive() -> None:
    manifest = vault_layout.chapter_manifest()
    ops = next(item for item in manifest["pillars"] if item["key"] == "ops")
    chapter = next(item for item in ops["chapters"] if item["key"] == "love_memory")
    assert chapter["path"] == "02_生活运营/05_情感与记忆"
    assert chapter["default_type"] == "memory"
    assert len(vault_layout.CHAPTERS) == 47
    assert manifest["layout_version"] == "pillars-v2"
