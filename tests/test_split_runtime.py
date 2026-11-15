from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app.db import Database
from backend.app.storage_layout import new_layout, initialize_empty_workspace, register_layout, workspace_relative, workspace_path
from backend.app import pillar_notes, recycle, memory_hub, personal_data, train_tracker, identity_inbox


def prepare(tmp_path):
    layout = new_layout(tmp_path / "split")
    initialize_empty_workspace(layout)
    register_layout(layout)
    db = Database(layout.state / "vault-state/torch_os.sqlite3"); db.initialize()
    return layout, db


def test_split_state_and_recycle_roundtrip_leave_notes_free_of_hidden_databases(tmp_path):
    layout, db = prepare(tmp_path)
    note = pillar_notes.create_note(layout.notes, pillar="knowledge", chapter="inbox", title="可恢复", body="原文")
    deleted = pillar_notes.trash_note(layout.notes, note["path"])
    assert not (layout.notes / note["path"]).exists()
    listing = recycle.list_recycle(layout.notes)
    items = [item for group in listing["groups"] for item in group["items"]]
    assert len(items) == 1
    assert items[0]["trash_path"].startswith(".torch-os/")
    result = recycle.restore_recycle(db, layout.notes, paths=[items[0]["trash_path"]])
    assert result["restored"] == 1
    assert "原文" in (layout.notes / note["path"]).read_text(encoding="utf-8")
    memory_hub.ensure_hub(layout.notes)
    assert personal_data._data_path(layout.notes).is_relative_to(layout.state)
    assert train_tracker._timetable_path(layout.notes).is_relative_to(layout.state)
    assert not (layout.notes / ".torch-os").exists()
    assert workspace_relative(layout.notes, workspace_path(layout.notes, ".torch-os/state.json")).as_posix() == ".torch-os/state.json"
    with pytest.raises(ValueError):
        workspace_path(layout.notes, "../escape")


def test_split_application_uses_injected_roots_and_preserves_legacy_health_id(tmp_path):
    from backend.app.application import create_app
    layout, db = prepare(tmp_path)
    app = create_app(database=db, vault_dir=layout.notes, storage_layout=layout, runtime_label="qa",
                     enable_dsh=False, love_legacy_migration=False, enable_dida_auto_push=False,
                     env_file=tmp_path / "empty.env", workspace_registry=tmp_path / "registry.json")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        health = client.get("/api/health").json()
    assert health["storage_mode"] == "split" and health["workspace_id"] == layout.workspace_id
    assert health["vault_id"] != layout.workspace_id
    assert not list(layout.notes.rglob("*.sqlite3")) and not list(layout.notes.rglob("*.db"))
    assert not (layout.notes / ".torch-os").exists()
