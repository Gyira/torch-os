"""恋爱系统 v2：私人数据在 02_生活运营/01_恋爱系统，按页面分目录。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.application import create_app
from backend.app.db import Database


def test_love_data_is_stored_inside_the_selected_personal_database(tmp_path: Path) -> None:
    vault = tmp_path / "个人数据库"
    data_dir = vault / ".torch-os"
    database = Database(data_dir / "torch_os.sqlite3")
    app = create_app(
        database, vault,
        session_token="test-token", security_environ={}, env_file=tmp_path / ".env",
    )
    client = TestClient(app)

    response = client.get("/api/love/status", headers={"x-api-token": "test-token"})
    assert response.status_code == 200, response.text
    payload = response.json()
    paths = payload["paths"]

    expected = vault / "02_生活运营" / "01_恋爱系统"
    assert Path(paths["root"]) == expected
    assert Path(paths["memory"]) == expected / "01_恋爱记忆"
    assert Path(paths["chat"]) == expected / "02_微信聊天"
    assert Path(paths["schedule"]) == expected / "03_排班日历"
    assert Path(paths["cycle"]) == expected / "04_经期关怀"
    assert Path(paths["gallery"]) == expected / "05_恋爱相册"
    assert Path(paths["bazi"]) == expected / "06_八字合婚"
    assert Path(paths["master"]) == expected / "07_恋爱大师"
    assert expected.is_relative_to(vault), "恋爱数据必须位于所选的个人数据库内"
    assert (expected / "01_恋爱记忆" / "恋爱日志").is_dir()
    assert (expected / "02_微信聊天" / "chat.db").is_file()
    assert (expected / "04_经期关怀" / "cycle.db").exists() is False

    overview = client.get(
        "/api/love/cycle/overview", headers={"x-api-token": "test-token"},
    )
    assert overview.status_code == 200, overview.text
    assert (expected / "04_经期关怀" / "cycle.db").exists() is False
    saved = client.put(
        "/api/love/cycle/profile",
        json={"partner_name": "她"},
        headers={"x-api-token": "test-token"},
    )
    assert saved.status_code == 200, saved.text
    assert (expected / "04_经期关怀" / "cycle.db").is_file()


def test_injected_temp_database_does_not_touch_the_real_love_database(tmp_path: Path) -> None:
    """测试/QA 的临时库不能因为默认路径去读写用户的真实恋爱数据。"""
    database = Database(tmp_path / "torch_os.sqlite3")
    app = create_app(
        database, tmp_path / "vault",
        session_token="test-token", security_environ={}, env_file=tmp_path / ".env",
    )
    payload = TestClient(app).get(
        "/api/love/status", headers={"x-api-token": "test-token"},
    ).json()
    assert Path(payload["paths"]["root"]) == (
        tmp_path / "vault" / "02_生活运营" / "01_恋爱系统")
    assert Path(payload["paths"]["root"]).is_relative_to(tmp_path)
