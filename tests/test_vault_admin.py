"""设置页「创建新的个人数据库」：按四库模板一次成型，且不碰当前库。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import vault_layout
from backend.app.application import create_app
from backend.app.db import Database
from backend.app.vault_admin import MANIFEST_RELATIVE, PLUGIN_DIR_RELATIVE, PROJECT_ROOT


def build(tmp_path: Path, runtime_label: str = "personal"):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    app_data = vault / ".torch-os"
    app_data.mkdir()
    env = tmp_path / ".env"
    env.write_text("VAULT_DIR=./vault\n", encoding="utf-8")
    registry = tmp_path / "workspaces.json"
    database = Database(app_data / "torch.sqlite3")
    app = create_app(database, vault, security_environ={}, env_file=env,
                     runtime_label=runtime_label, workspace_registry=registry)
    client = TestClient(app)
    client.get("/")
    return client, vault, registry, database


@pytest.fixture()
def desktop(tmp_path: Path) -> Path:
    parent = tmp_path / "desktop"
    parent.mkdir()
    return parent


def test_template_lists_the_canonical_four_pillar_layout(tmp_path):
    client, vault, _registry, _db = build(tmp_path)
    response = client.get("/api/vault/template")
    assert response.status_code == 200
    payload = response.json()
    assert payload["layout_version"] == vault_layout.LAYOUT_VERSION
    assert [pillar["key"] for pillar in payload["pillars"]] == list(vault_layout.PILLAR_ORDER)
    chapters = [chapter for pillar in payload["pillars"] for chapter in pillar["chapters"]]
    assert len(chapters) == len(vault_layout.CHAPTERS)
    assert set(payload["directories"]) == {item[3] for item in vault_layout.CHAPTERS}
    # 人生路 v2：模板直接给出新路径与分组，不复制旧目录。
    assert "00_人生路/02_反思复盘/01_反思" in payload["directories"]
    assert "00_人生路/05_项目/01_项目计划" in payload["directories"]
    life = next(pillar for pillar in payload["pillars"] if pillar["key"] == "life")
    assert [group["key"] for group in life["groups"]] == [
        "insight", "review", "goal", "project"]
    assert payload["obsidian"]["community-plugins.json"] == ["torch"]
    assert payload["current"]["path"] == str(vault.resolve())
    assert payload["manifest_file"] == MANIFEST_RELATIVE


def test_create_builds_the_full_template_and_registers_it(tmp_path, desktop):
    client, vault, registry, database = build(tmp_path)
    response = client.post("/api/vault/create",
                           json={"parent_dir": str(desktop), "name": "第二大脑"})
    assert response.status_code == 201, response.text
    payload = response.json()
    target = desktop / "第二大脑"
    assert payload["created"] is True
    assert payload["registered"] is True
    assert Path(payload["path"]) == target

    # 四库章节一个不少，且与后端布局完全一致。
    for chapter in vault_layout.CHAPTERS:
        assert (target / chapter[3]).is_dir(), chapter[3]
    assert (target / "00_人生路" / "02_反思复盘" / "01_反思").is_dir()
    assert (target / "00_人生路" / "05_项目" / "02_项目复盘").is_dir()
    assert (target / "00_人生路" / "08_任务与待办" / "任务").is_dir()

    # .obsidian 预设与插件。
    obsidian = target / ".obsidian"
    assert json.loads((obsidian / "community-plugins.json").read_text(encoding="utf-8")) == ["torch"]
    assert json.loads((obsidian / "core-plugins.json").read_text(encoding="utf-8"))
    plugin = target / PLUGIN_DIR_RELATIVE
    for name in ("main.js", "manifest.json", "styles.css", "data.json"):
        assert (plugin / name).is_file(), name
    settings = json.loads((plugin / "data.json").read_text(encoding="utf-8"))
    assert settings["repoRoot"] == PROJECT_ROOT.as_posix()
    assert settings["autoStart"] is True

    # 布局清单：章节目录和后端映射一一对应。
    manifest = json.loads((target / MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    assert manifest["schema"] == 1
    assert manifest["layout_version"] == vault_layout.LAYOUT_VERSION
    listed = [chapter for pillar in manifest["pillars"] for chapter in pillar["chapters"]]
    assert [(row["key"], row["path"]) for row in listed] == [
        (item[0], item[3]) for item in vault_layout.CHAPTERS]

    identity = target / vault_layout.identity_paths()[0]
    assert identity.is_file()
    assert "type: identity" in identity.read_text(encoding="utf-8")

    # 注册表 + 审计；当前库不受影响。
    entries = json.loads(registry.read_text(encoding="utf-8"))["workspaces"]
    assert [entry["name"] for entry in entries] == ["第二大脑"]
    assert not (vault / "第二大脑").exists()
    with database.connect() as connection:
        events = [row[0] for row in connection.execute(
            "SELECT event_type FROM audit_events ORDER BY id DESC LIMIT 1")]
    assert events == ["vault_created"]


def test_existing_empty_folder_is_filled_but_a_used_one_is_refused(tmp_path, desktop):
    client, _vault, _registry, _db = build(tmp_path)
    empty = desktop / "空目录"
    empty.mkdir()
    assert client.post("/api/vault/create",
                       json={"parent_dir": str(desktop), "name": "空目录"}).status_code == 201
    assert (empty / "00_人生路").is_dir()

    busy = desktop / "有东西"
    busy.mkdir()
    (busy / "note.md").write_text("x", encoding="utf-8")
    refused = client.post("/api/vault/create",
                          json={"parent_dir": str(desktop), "name": "有东西"})
    assert refused.status_code == 409


@pytest.mark.parametrize("name", ["a:b", "..", "CON", "末尾点.", "  "])
def test_invalid_names_are_rejected(tmp_path, desktop, name):
    client, _vault, _registry, _db = build(tmp_path)
    response = client.post("/api/vault/create", json={"parent_dir": str(desktop), "name": name})
    assert response.status_code == 422


def test_relative_parent_and_nested_targets_are_rejected(tmp_path):
    client, vault, registry, _db = build(tmp_path)
    relative = client.post("/api/vault/create", json={"parent_dir": "desktop", "name": "库"})
    assert relative.status_code == 422
    nested = client.post("/api/vault/create", json={"parent_dir": str(vault), "name": "子库"})
    assert nested.status_code == 409
    assert not (vault / "子库").exists()
    if registry.exists():
        assert json.loads(registry.read_text(encoding="utf-8"))["workspaces"] == []


def test_qa_runtime_cannot_create_databases(tmp_path):
    client, _vault, _registry, _db = build(tmp_path, runtime_label="qa")
    assert client.get("/api/vault/template").status_code == 409
    response = client.post("/api/vault/create",
                           json={"parent_dir": str(tmp_path), "name": "库"})
    assert response.status_code == 409
    assert not (tmp_path / "库").exists()
