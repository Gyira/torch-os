import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import notebooks, pillars, vault_layout as layout
from backend.app.db import Database
from backend.app.love_memory import create_memory_router


@pytest.fixture
def setup(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    return vault, db


def test_order_is_saved_to_vault_config_and_read_back(setup):
    vault, _db = setup
    result = layout.set_notebook_order(
        vault, "life", ["essay", "journal", "essay", " "])
    assert result == {"pillar": "life", "order": ["essay", "journal"]}
    path = vault / layout.NOTEBOOK_ORDER_REL
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["order"]["life"] == ["essay", "journal"]
    assert layout.notebook_order(vault) == {"life": ["essay", "journal"]}


def test_orders_are_independent_and_empty_clears_file(setup):
    vault, _db = setup
    layout.set_notebook_order(vault, "life", ["essay"])
    layout.set_notebook_order(vault, "knowledge", ["reading", "inbox"])
    assert layout.notebook_order(vault) == {
        "life": ["essay"], "knowledge": ["reading", "inbox"]}
    path = vault / layout.NOTEBOOK_ORDER_REL
    assert "life" in json.loads(
        path.read_text(encoding="utf-8"))["order"]
    layout.set_notebook_order(vault, "life", [])
    assert layout.notebook_order(vault) == {"knowledge": ["reading", "inbox"]}
    layout.set_notebook_order(vault, "knowledge", [])
    assert not path.exists()


def test_unknown_pillar_and_bad_values_are_rejected(setup):
    vault, _db = setup
    with pytest.raises(ValueError):
        layout.set_notebook_order(vault, "self", ["essay"])
    with pytest.raises(ValueError):
        layout.set_notebook_order(vault, "life", "essay")
    with pytest.raises(ValueError):
        layout.set_notebook_order(
            vault, "life", ["x" * (layout.NOTEBOOK_ORDER_MAX_KEY + 1)])


def test_corrupt_order_file_falls_back_to_default(setup):
    vault, _db = setup
    path = vault / layout.NOTEBOOK_ORDER_REL
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert layout.notebook_order(vault) == {}
    saved = layout.set_notebook_order(vault, "life", ["journal"])
    assert saved["order"] == ["journal"]
    assert layout.notebook_order(vault) == {"life": ["journal"]}


def test_overview_exposes_saved_order(setup):
    vault, db = setup
    layout.set_notebook_order(vault, "life", ["essay", "goal"])
    overview = pillars._overview(vault, db, "life")
    assert overview["notebook_order"] == ["essay", "goal"]
    assert "notebook_order" not in pillars._overview(vault, db, "self")


def test_love_memory_overview_exposes_saved_order(setup):
    vault, db = setup
    keys = ["love_drafts", "love_journal"]
    layout.set_notebook_order(vault, "love", keys)
    app = FastAPI()
    app.include_router(create_memory_router(vault, db))
    with TestClient(app) as client:
        overview = client.get("/api/love/memory/overview").json()
    assert overview["notebook_order"] == keys


def test_order_endpoint_persists_and_validates(setup):
    vault, db = setup
    app = FastAPI()
    app.include_router(notebooks.create_notebook_router(db, vault))
    with TestClient(app) as client:
        response = client.post("/api/notebooks/order", json={
            "pillar": "knowledge", "keys": ["output", "inbox", "output"]})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "pillar": "knowledge", "order": ["output", "inbox"]}
        assert layout.notebook_order(vault) == {
            "knowledge": ["output", "inbox"]}
        assert client.post("/api/notebooks/order", json={
            "pillar": "nope", "keys": []}).status_code == 422
