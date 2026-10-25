"""The dsh sidebar reads /api/nav; it must match the real frontend modules.

0.95.0 把侧边栏收敛成五大模块（对话在 dsh 里、帮助并进设置），所以这里既比对
侧边栏按钮，也比对每个模块下面的分区（`data-module`）。
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database

ROOT = Path(__file__).resolve().parents[1]
MODULES = ["workbench", "life", "self", "ops", "knowledge", "settings"]


def make_app(tmp_path):
    return create_app(
        Database(tmp_path / "nav.sqlite3"), tmp_path / "vault",
        security_environ={}, env_file=tmp_path / ".env")


def test_nav_manifest_matches_the_frontend_modules_and_labels(tmp_path):
    client = TestClient(make_app(tmp_path))
    client.get("/")
    views = client.get("/api/nav").json()["views"]
    ids = [view["id"] for view in views]
    assert len(ids) == len(set(ids)) == 6
    assert ids == MODULES, "顺序由 NAV_VIEWS 决定"
    assert "chat" not in ids, "对话页已移除：清单里不应再有 chat"
    assert "today" not in ids and "profile" not in ids, "旧页面名不应再出现在清单里"
    for view in views:
        assert view["hash"] == f"#{view['id']}"
        assert view["label"]

    markup = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    buttons = re.findall(r'class="nav-item[^"]*" data-view="([a-z]+)"', markup)
    assert set(ids) == set(buttons), "侧边栏按钮与 /api/nav 必须一致"
    # 每个模块至少有一个分区（工作台与设置是实体页面，其余模块由 pane 组成）
    for module in ids:
        assert (f'data-module="{module}"' in markup) or (f'id="{module}-view"' in markup), module

    order = (ROOT / "frontend/nav-order.js").read_text(encoding="utf-8")
    block = re.search(r"NAV_LABELS\s*=\s*\{(.*?)\}", order, re.S).group(1)
    labels = {key: value for key, value in re.findall(r'(\w+):\s*"([^"]+)"', block)}
    assert {view["id"]: view["label"] for view in views} == labels
    defaults = re.search(r"NAV_ORDER_DEFAULT\s*=\s*\[(.*?)\]", order, re.S).group(1)
    default_ids = re.findall(r'"([a-z]+)"', defaults)
    assert [view["id"] for view in views] == default_ids


def test_learning_and_daily_pages_are_gone_from_the_shell(tmp_path):
    markup = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert 'id="learning-view"' not in markup, "学习页已移除：学了什么写进日记"
    assert 'data-settings-section="help"' in markup, "帮助并进了设置"
    assert 'id="agent-view"' in markup and 'data-module="workbench"' in markup, "与神对话在工作台里"
    assert 'data-pane-label="与神对话"' in markup, "工作台锚点叫与神对话"
    assert 'id="journal-view"' not in markup, "旧日记面板已移除"
    assert 'id="me-view"' not in markup and 'id="notes-view"' not in markup, "旧模块入口已移除"
    for view in ("life", "self", "ops", "knowledge"):
        assert f'id="{view}-view"' in markup, f"{view} 四库页面存在"
