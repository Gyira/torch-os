"""WeFlow 微信组件替换/回滚：只替换用户提供的开源构建产物，不修改闭源 DLL。"""

from __future__ import annotations

from pathlib import Path

from backend.app.love_layout import ensure_love_layout, love_paths
from backend.app.love_weflow_component import (
    component_status, replace_component, rollback_component,
)


def test_replace_and_rollback_component(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "WeFlow"
    dll_dir = install / "resources" / "resources" / "wcdb" / "win32" / "x64"
    dll_dir.mkdir(parents=True)
    original = b"MZ" + b"old-component" * 20
    dll = dll_dir / "wcdb_api.dll"
    dll.write_bytes(original)
    monkeypatch.setenv("WEFLOW_HOME", str(install))
    monkeypatch.setattr(
        "backend.app.love_weflow_component._weflow_running", lambda: False)
    paths = love_paths(tmp_path)
    ensure_love_layout(tmp_path)

    status = component_status(paths)
    assert status["installed"] is True
    assert Path(status["dll_path"]) == dll

    replacement = b"MZ" + b"open-source-build" * 20
    result = replace_component(paths, replacement, filename="wcdb_api.dll")
    assert dll.read_bytes() == replacement
    backup = Path(result["backup"])
    assert backup.is_file()
    assert backup.parent.is_relative_to(paths.archive)

    rolled = rollback_component(paths)
    assert dll.read_bytes() == original
    assert Path(rolled["backup"]) == backup
