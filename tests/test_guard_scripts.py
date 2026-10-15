"""守卫脚本的自测：把 2026-10-05 真踩过的每个坑注入临时树，要求检查器报错。

这是“下次不再踩同一个坑”的机器保证：规则会不会失效，由这些用例说了算。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRITY = ROOT / "scripts" / "check_integrity.py"
SURFACE = ROOT / "scripts" / "check_module_surface.mjs"
APP_JS = """const MODULES = {
  workbench: {
    panes: [["workbench-view", "今日流程"]],
  },
  settings: { panes: [], ownNav: true },
};
"""
INDEX = "<!doctype html>\n<html><body>\n<section id=\"workbench-view\" class=\"view\">\n</section>\n</body></html>\n"


def make_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "frontend").mkdir(parents=True)
    (tree / "frontend" / "app.js").write_text(APP_JS, encoding="utf-8", newline="")
    (tree / "frontend" / "index.html").write_text(INDEX, encoding="utf-8", newline="")
    (tree / "frontend" / "helper.js").write_text("export function used() {}\nexport const other = 1;\n",
                                                 encoding="utf-8", newline="")
    (tree / "frontend" / "consumer.js").write_text('import { used } from "./helper.js";\nused();\n',
                                                   encoding="utf-8", newline="")
    (tree / "README.md").write_text("# 薪火OS · 0.96\n", encoding="utf-8", newline="")
    return tree


def run_integrity(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INTEGRITY), str(tree)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def run_surface(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(SURFACE), str(tree)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_clean_tree_passes(tmp_path):
    tree = make_tree(tmp_path)
    assert run_integrity(tree).returncode == 0
    assert run_surface(tree).returncode == 0


def test_bom_is_caught_but_powershell_keeps_it(tmp_path):
    tree = make_tree(tmp_path)
    (tree / "README.md").write_bytes(b"\xef\xbb\xbf" + (tree / "README.md").read_bytes())
    # 带中文的 .ps1 **必须**留 BOM：去掉它，Windows PowerShell 5.1 会把中文读成乱码、脚本语法错。
    (tree / "scripts").mkdir(exist_ok=True)
    (tree / "scripts" / "check.ps1").write_bytes(b"\xef\xbb\xbfWrite-Host '\u4e2d\u6587'\n")
    result = run_integrity(tree)
    assert result.returncode == 1
    assert "BOM" in result.stdout and "README.md" in result.stdout

    tree2 = make_tree(tmp_path / "ps")
    (tree2 / "scripts").mkdir()
    (tree2 / "scripts" / "check.ps1").write_bytes("Write-Host '中文'\n".encode("utf-8"))
    result2 = run_integrity(tree2)
    assert result2.returncode == 1
    assert "没有 UTF-8 BOM" in result2.stdout


def test_replacement_character_and_broken_first_char_are_caught(tmp_path):
    tree = make_tree(tmp_path)
    (tree / "frontend" / "app.js").write_bytes("\ufffdconst MODULES = {}".encode("utf-8"))
    result = run_integrity(tree)
    assert result.returncode == 1
    assert "U+FFFD" in result.stdout

    tree2 = make_tree(tmp_path / "second")
    (tree2 / "frontend" / "index.html").write_bytes(b"!doctype html>\n")
    result2 = run_integrity(tree2)
    assert result2.returncode == 1
    assert "首行应以" in result2.stdout


def test_unclosed_section_is_caught(tmp_path):
    """帮助段漏了 </section>，后面的分区被塞进隐藏父节点，页面一片空白。"""
    tree = make_tree(tmp_path)
    (tree / "frontend" / "index.html").write_text(
        INDEX.replace("</section>\n", "") + '<section id="notes-view" class="pane" hidden>\n</section>\n',
        encoding="utf-8", newline="")
    result = run_integrity(tree)
    assert result.returncode == 1
    assert "没有闭合" in result.stdout or "多出" in result.stdout


def test_stale_view_name_is_caught(tmp_path):
    """三个模块还在判断旧页面名 today，工作台上的建议永不刷新。"""
    tree = make_tree(tmp_path)
    (tree / "frontend" / "suggestions.js").write_text(
        'if (state.view === "today") loadSuggestions();\n', encoding="utf-8", newline="")
    result = run_integrity(tree)
    assert result.returncode == 1
    assert 'state.view === "today"' in result.stdout


def test_broken_python_docstring_is_caught(tmp_path):
    """去 BOM 的脚本把首字符删掉，`\"\"\"` 只剩两个引号，整个后端 import 失败。"""
    tree = make_tree(tmp_path)
    (tree / "backend").mkdir()
    (tree / "backend" / "application.py").write_text('""App factory."""\n', encoding="utf-8", newline="")
    result = run_integrity(tree)
    assert result.returncode == 1
    assert "三引号被破坏" in result.stdout


def test_missing_export_is_caught(tmp_path):
    """整体覆盖 nav-order.js 时丢了 applyNavOrder，静态 import 立刻炸。"""
    tree = make_tree(tmp_path)
    (tree / "frontend" / "consumer.js").write_text(
        'import { used, applyNavOrder } from "./helper.js";\n', encoding="utf-8", newline="")
    result = run_surface(tree)
    assert result.returncode == 1
    assert "applyNavOrder" in result.stdout

    tree2 = make_tree(tmp_path / "third")
    (tree2 / "frontend" / "consumer.js").write_text('import missing from "./nope.js";\n',
                                                    encoding="utf-8", newline="")
    assert run_surface(tree2).returncode == 1
