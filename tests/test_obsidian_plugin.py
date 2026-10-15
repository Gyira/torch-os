"""Obsidian 插件（0.99.0）的仓库侧守卫：清单合法、单文件可加载、隐私边界。"""

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
    # 内嵌入口固定带 embed=1（隐藏产品自己的外壳）。
    assert "?embed=1" in source
    # 隐私：仓库副本里不许出现任何本机绝对路径与用户名。
    assert "C:/Users" not in source and "C:\\Users" not in source
    assert "AppData" not in source


def test_stylesheet_and_user_data_boundaries():
    assert ".torch-frame" in (PLUGIN / "styles.css").read_text(encoding="utf-8")
    # 用户本机的 data.json（仓库路径）只住在 vault 里，绝不进仓库。
    assert not (PLUGIN / "data.json").exists()
    gitignore = (PLUGIN.parent / ".gitignore").read_text(encoding="utf-8")
    assert "obsidian-plugin/data.json" in gitignore
