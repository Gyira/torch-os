"""Obsidian 按库实例（TORCH_VAULT_SCOPED）：库就是工作区，运行中不能再切库。

这些测试钉住三件事：
- health 暴露的 vault_id 是稳定、非敏感的库标识，供插件挑选/复用实例；
- scoped 实例在设置能力里隐藏工作区与旧库连接；
- 写工作区/写 .env 的接口在 scoped 下一律 409，避免界面显示 A 库、进程读 B 库。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app, vault_identity
from backend.app.db import Database



def build(tmp_path: Path, *, vault_scoped: bool = True):
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
                     workspace_registry=registry, vault_scoped=vault_scoped)
    client = TestClient(app)
    client.get("/")
    return client, env, registry, vault, other


def test_vault_identity_is_stable_and_case_insensitive_on_windows(tmp_path):
    vault = tmp_path / "MyVault"
    vault.mkdir()
    first = vault_identity(vault)
    assert len(first) == 16 and first == first.lower()
    assert first == vault_identity(str(vault))
    # 同一路径的 ./ 形态必须指向同一个实例。
    assert first == vault_identity(vault / ".." / "MyVault")
    if os.name == "nt":
        assert first == vault_identity(Path(str(vault).upper()))
        assert vault_identity(tmp_path / "myvault").lower() == first


def test_health_identifies_the_bound_vault_without_exposing_the_path(tmp_path):
    client, _env, _registry, vault, _other = build(tmp_path)
    health = client.get("/api/health").json()
    assert health["vault_id"] == vault_identity(vault)
    assert health["vault_name"] == vault.name
    assert health["vault_scoped"] is True
    assert str(vault.resolve()) not in str(health)

    status = client.get("/api/config/status").json()
    assert status["vault_scoped"] is True
    assert status["vault_name"] == vault.name
    assert status["capabilities"]["workspaces"] is False
    assert status["capabilities"]["vault_connection"] is False


def test_scoped_instance_rejects_every_workspace_or_env_switch(tmp_path):
    client, env, registry, _vault, other = build(tmp_path)
    before = env.read_text(encoding="utf-8")

    created = client.post("/api/workspaces", json={"name": "另一个库", "folder": str(other)})
    assert created.status_code == 409
    assert "Obsidian" in created.json()["detail"]
    assert not registry.exists()

    activated = client.post("/api/workspaces/activate", json={"name": "另一个库"})
    assert activated.status_code == 409
    deleted = client.post("/api/workspaces/delete", json={"name": "另一个库"})
    assert deleted.status_code == 409

    connection = client.post("/api/vault/connection",
                             json={"path": str(other), "read_only": False})
    assert connection.status_code == 409
    assert env.read_text(encoding="utf-8") == before

    # 只读列表仍可打开（前端会隐藏，但接口不应该 500）。
    assert client.get("/api/workspaces").status_code == 200
    assert client.get("/api/vault/connection").status_code == 200


def test_manual_mode_keeps_workspace_management_enabled(tmp_path):
    client, _env, _registry, _vault, _other = build(tmp_path, vault_scoped=False)
    health = client.get("/api/health").json()
    assert health["vault_scoped"] is False
    status = client.get("/api/config/status").json()
    assert status["capabilities"]["workspaces"] is True
    assert status["capabilities"]["vault_connection"] is True
