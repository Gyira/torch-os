"""Obsidian 插件（0.99.1）的仓库侧守卫：清单合法、单文件可加载、按库实例、隐私边界。"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "obsidian-plugin"


def test_manifest_is_valid_and_desktop_only():
    manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "torch"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["isDesktopOnly"] is True
    assert manifest["minAppVersion"]
    assert manifest["description"]


def test_main_js_is_a_loadable_commonjs_plugin_without_user_paths():
    source = (PLUGIN / "main.js").read_text(encoding="utf-8")
    assert "require('obsidian')" in source
    assert "module.exports" in source
    assert "registerView" in source and "addRibbonIcon" in source
    # 内嵌入口仍带 embed=1 和 host=obsidian（后者让前端走宿主桥打开任务文档）。
    assert "embed', '1'" in source
    assert "host', 'obsidian'" in source
    # 每个 Obsidian 库一个后端实例：显式传 VAULT_DIR / DATA_DIR / PORT 并标 scoped。
    for token in ("vaultIdentity", "instancePort", "api/health", "vault_id",
                  "TORCH_VAULT_SCOPED", "VAULT_DIR", "DATA_DIR", "PORT", "WORKSPACE_NAME"):
        assert token in source, token
    # 打开任务文档的宿主桥：只接受任务目录或旧总文件，并校验消息来源。
    for token in ("torch:open-note", "TFile", "normalizePath", "getAbstractFileByPath", "openFile",
                  "00_人生路/07_任务与待办/", "00-System/任务（tasks）.md", "00-System/任务/",
                  "00_人生路", "01_自我档案", "02_生活运营", "03_知识库"):
        assert token in source, token
    assert "event.origin" in source
    # 隐私：仓库副本里不许出现任何本机绝对路径与用户名。
    assert "C:/Users" not in source and "C:\\Users" not in source
    assert "AppData" not in source


def test_stylesheet_and_user_data_boundaries():
    styles = (PLUGIN / "styles.css").read_text(encoding="utf-8")
    assert ".torch-frame" in styles and ".torch-offline" in styles
    # 用户本机的 data.json（仓库路径、实例端口）只住在 vault 里，绝不进仓库。
    assert not (PLUGIN / "data.json").exists()
    gitignore = (PLUGIN.parent / ".gitignore").read_text(encoding="utf-8")
    assert "obsidian-plugin/data.json" in gitignore
