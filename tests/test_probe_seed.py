"""The QA browser probe may seed disposable data, but never a real vault."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import browser_pillars_probe as probe  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, health: dict, note: dict, search: dict,
                 suggestions: dict | None = None):
        self.health = health
        self.note = note
        self.search = search
        self.suggestions = suggestions or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url: str, params: dict | None = None):
        self.calls.append(("GET", url, params))
        if url == "/api/health":
            return FakeResponse(200, self.health)
        if url == "/api/journal/note":
            return FakeResponse(200, self.note)
        if url == "/api/search":
            return FakeResponse(200, self.search)
        if url == "/api/steward/inbox-suggestions":
            return FakeResponse(200, self.suggestions)
        return FakeResponse(200, {})

    def post(self, url: str, json: dict | None = None):
        self.calls.append(("POST", url, json))
        if url == "/api/journal/note/generate":
            return FakeResponse(200, {"exists": True})
        if url == "/api/notes":
            return FakeResponse(201, {"document": {"id": 1}})
        return FakeResponse(200, {})


def install(monkeypatch, client: FakeClient):
    monkeypatch.setattr(probe.httpx, "Client", lambda **_kwargs: client)
    return client


def test_real_vault_is_never_written(monkeypatch):
    client = install(monkeypatch, FakeClient(
        health={"runtime_label": "personal"},
        note={"exists": False},
        search={"items": []},
    ))
    result = probe.seed_qa_workspace("http://127.0.0.1:8765")
    assert result["skipped"] == "not-qa"
    assert [call[0] for call in client.calls] == ["GET", "GET"]
    assert not any(call[0] == "POST" for call in client.calls)


def test_existing_qa_data_is_not_duplicated(monkeypatch):
    client = install(monkeypatch, FakeClient(
        health={"runtime_label": "qa"},
        note={"exists": True},
        search={"items": [{"path": "03_知识库/00_收件箱/考研.md"}]},
        suggestions={"items": [{"proposal_id": "st-existing"}]},
    ))
    result = probe.seed_qa_workspace("http://127.0.0.1:8766")
    assert result == {"seeded": True, "diary": None, "search_note": None}
    assert not any(call[0] == "POST" for call in client.calls)


def test_fresh_qa_workspace_gets_a_diary_and_search_note(monkeypatch):
    client = install(monkeypatch, FakeClient(
        health={"runtime_label": "qa"},
        note={"exists": False},
        search={"items": []},
    ))
    result = probe.seed_qa_workspace("http://127.0.0.1:8766")
    assert result["seeded"] is True
    assert result["diary"] == 200
    assert result["search_note"] == 201
    assert result["inbox_notes"] == ["QA 整理收件箱五步法", "QA 读书笔记：原则", "QA 触动"]
    posted = [call[1] for call in client.calls if call[0] == "POST"]
    assert posted == ["/api/journal/note/generate", "/api/notes",
                     "/api/capture", "/api/capture", "/api/capture"]
