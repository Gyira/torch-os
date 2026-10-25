"""工作区（用户）的自测：一个工作区 = 一个文件夹，应用数据放在 `<文件夹>/.torch-os`。

2026-10-17：换用户就是换文件夹。切换必须同时切 DATA_DIR 与 VAULT_DIR，否则两个人的
SQLite 索引、记忆与审计会混在一起。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database



def build(tmp_path: Path, *, runtime_label: str = "personal"):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    app_data = vault / ".torch-os"
    app_data.mkdir()
    other = tmp_path / "other-vault"
    (other / ".obsidian").mkdir(parents=True)
    env = tmp_path / ".env"
    env.write_text("VAULT_DIR=./vault\n", encoding="utf-8")
    registry = tmp_path / "workspaces.json"
    database = Database(app_data / "torch.sqlite3")
    app = create_app(database, vault, security_environ={}, env_file=env,
                     runtime_label=runtime_label, workspace_registry=registry)
    client = TestClient(app)
    client.get("/")
    return client, database, env, registry, vault, other, app_data


def test_list_includes_the_running_workspace_even_without_registry(tmp_path):
    client, _database, _env, _registry, vault, _other, app_data = build(tmp_path)
    payload = client.get("/api/workspaces").json()
    assert payload["active"] == "个人"
    active = payload["workspaces"][0]
    assert active["active"] is True and active["registered"] is False
    assert Path(active["folder"]) == vault.resolve()
    assert Path(active["data"]) == app_data.resolve()
    assert payload["restart_required_for_changes"] is True


def test_create_validates_the_folder_and_derives_the_data_directory(tmp_path):
    client, _database, _env, registry, _vault, other, _app_data = build(tmp_path)

    relative = client.post("/api/workspaces", json={"name": "家人A", "folder": "./vault"})
    assert relative.status_code == 422

    missing = client.post("/api/workspaces", json={
        "name": "家人A", "folder": str(tmp_path / "missing")})
    assert missing.status_code == 422

    created = client.post("/api/workspaces", json={
        "name": "家人A", "folder": str(other), "read_only": True})
    assert created.status_code == 200
    entry = created.json()["workspace"]
    assert entry["name"] == "家人A" and entry["read_only"] is True
    assert Path(entry["folder"]) == other.resolve()
    assert Path(entry["data"]) == other.resolve() / ".torch-os"  # 数据目录由文件夹推导
    stored = json.loads(registry.read_text(encoding="utf-8"))["workspaces"]
    assert stored[0]["name"] == "家人A"
    assert "data" not in stored[0]  # 注册表只记文件夹

    # 同名 = 编辑已有工作区（列表按名字做键），不是重复创建
    updated = client.post("/api/workspaces", json={
        "name": "家人A", "folder": str(other), "read_only": False})
    assert updated.status_code == 200
    stored = json.loads(registry.read_text(encoding="utf-8"))["workspaces"]
    assert len(stored) == 1 and stored[0]["read_only"] is False


def test_rename_moves_the_registry_entry(tmp_path):
    client, _database, _env, registry, _vault, other, _app_data = build(tmp_path)
    client.post("/api/workspaces", json={"name": "旧名字", "folder": str(other)})
    renamed = client.post("/api/workspaces", json={
        "name": "新名字", "folder": str(other), "original_name": "旧名字"})
    assert renamed.status_code == 200
    stored = json.loads(registry.read_text(encoding="utf-8"))["workspaces"]
    assert [item["name"] for item in stored] == ["新名字"]


def test_activate_writes_env_without_storing_paths_in_audit(tmp_path):
    client, database, env, _registry, _vault, other, _app_data = build(tmp_path)
    client.post("/api/workspaces", json={"name": "家人A", "folder": str(other)})

    response = client.post("/api/workspaces/activate", json={"name": "家人A"})
    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    env_text = env.read_text(encoding="utf-8")
    assert f"DATA_DIR={other.resolve() / '.torch-os'}" in env_text
    assert f"VAULT_DIR={other.resolve()}" in env_text
    assert "VAULT_READ_ONLY=0" in env_text
    assert "WORKSPACE_NAME=家人A" in env_text
    assert (other / ".torch-os").is_dir()

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT detail_json FROM audit_events WHERE event_type='workspace_activated'").fetchall()
    assert len(rows) == 1
    detail = rows[0]["detail_json"]
    assert str(other) not in detail


def test_activation_does_not_change_the_running_configuration(tmp_path):
    """写 .env 只是“下次启动用”；当前进程仍应报告原来的工作区。"""
    client, _database, _env, _registry, _vault, other, _app_data = build(tmp_path)
    client.post("/api/workspaces", json={"name": "家人A", "folder": str(other)})
    client.post("/api/workspaces/activate", json={"name": "家人A"})
    payload = client.get("/api/workspaces").json()
    assert payload["active"] == "个人"


def test_active_workspace_cannot_be_deleted(tmp_path):
    client, _database, _env, _registry, vault, _other, _app_data = build(tmp_path)
    client.post("/api/workspaces", json={"name": "当前", "folder": str(vault)})
    refused = client.post("/api/workspaces/delete", json={"name": "当前"})
    assert refused.status_code == 409 and "不能删除" in refused.json()["detail"]


def test_v1_registry_entries_fold_into_folders(tmp_path):
    client, _database, _env, registry, _vault, other, _app_data = build(tmp_path)
    registry.write_text(json.dumps({
        "version": 1,
        "workspaces": [{"name": "旧条目", "vault": str(other), "data": "D:\\old", "read_only": False}],
    }), encoding="utf-8")
    payload = client.get("/api/workspaces").json()
    entry = next(item for item in payload["workspaces"] if item["name"] == "旧条目")
    assert Path(entry["folder"]) == other
    assert Path(entry["data"]) == other / ".torch-os"


def test_qa_rejects_workspace_writes_and_hides_profiles(tmp_path):
    client, _database, _env, _registry, _vault, other, _app_data = build(
        tmp_path, runtime_label="qa")
    payload = client.get("/api/workspaces").json()
    assert payload["active"] == "当前工作区"
    assert len(payload["workspaces"]) == 1
    refused = client.post("/api/workspaces", json={"name": "家人A", "folder": str(other)})
    assert refused.status_code == 409


def test_corrupt_registry_does_not_break_the_list(tmp_path):
    client, _database, _env, registry, *_ = build(tmp_path)
    registry.write_text("{ not json", encoding="utf-8")
    payload = client.get("/api/workspaces")
    assert payload.status_code == 200
    assert payload.json()["active"] == "个人"
