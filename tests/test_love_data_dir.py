"""恋爱系统数据目录必须跟着当前选中的个人数据库，而不是仓库 data/love 或 %LOCALAPPDATA%。"""

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

    expected = data_dir / "love"
    assert Path(payload["data_dir"]) == expected
    assert Path(payload["db_path"]) == expected / "love_analysis.db"
    assert Path(payload["schedule_excel_dir"]) == expected / "xlsx"
    assert Path(payload["schedule_timetable"]) == expected / "train_timetable.json"
    assert expected.is_relative_to(vault), "恋爱数据必须位于所选的个人数据库内"
    assert (expected / "xlsx").is_dir()
    assert (expected / "love_analysis.db").is_file()  # /status 会确保表结构
    assert payload["migration"] == {}


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
    assert Path(payload["data_dir"]) == tmp_path / "love"
    assert Path(payload["db_path"]).is_relative_to(tmp_path)
