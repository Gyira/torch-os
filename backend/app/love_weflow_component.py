"""WeFlow 微信组件（WCDB）修复（2026-11-01）。

WeFlow 自带的闭源 ``wcdb_api.dll`` 有到期校验；本模块**不修改/破解**它，
只做三件事：

1. 找到 WeFlow 安装目录与 ``wcdb_api.dll``；
2. 用用户选择（或仓库 ``assets/weflow/`` 下）的开源构建产物替换，替换前备份；
3. 失败/需要时回滚。

开源构建来源：https://github.com/olansoft/weflow-wcdb-api-oss
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .love_layout import LovePaths
from .vault_connection import VaultReadOnlyError, ensure_vault_writable

SOURCE_PROJECT = "https://github.com/olansoft/weflow-wcdb-api-oss"
KNOWN_EXPIRED_HASHES = {
    # issue #1227 中提到的 WeFlow 预编译 Windows x64 组件（只用于提示，不用于破解）。
    "6915913a3a9930694e5c821bf58841e17bbad0f56691e307674fb0e31e9d44b8",
}
DLL_NAME = "wcdb_api.dll"
BACKUP_DIR = "weflow-component"
CONFIG_FILE = "config.json"
MAX_DLL_BYTES = 64 * 1024 * 1024
COMMON_RELATIVE = Path("resources") / "resources" / "wcdb" / "win32" / "x64" / DLL_NAME


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bundled_dll() -> Path:
    return _project_root() / "assets" / "weflow" / DLL_NAME


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _registry_installs() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found: list[Path] = []
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    keys = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root in roots:
        for key_path in keys:
            try:
                key = winreg.OpenKey(root, key_path)
            except OSError:
                continue
            try:
                count = winreg.QueryInfoKey(key)[0]
                for index in range(count):
                    try:
                        name = winreg.EnumKey(key, index)
                        sub = winreg.OpenKey(key, name)
                        with sub:
                            display = str(winreg.QueryValueEx(sub, "DisplayName")[0] or "")
                            if "weflow" not in display.casefold():
                                continue
                            for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                try:
                                    value = str(winreg.QueryValueEx(sub, value_name)[0] or "")
                                except OSError:
                                    continue
                                if not value:
                                    continue
                                candidate = Path(value.strip('"').split(",")[0])
                                if candidate.name.lower().endswith(".exe"):
                                    candidate = candidate.parent
                                if candidate.is_dir():
                                    found.append(candidate)
                    except OSError:
                        continue
            finally:
                key.Close()
    return found


def _config_path(paths: LovePaths) -> Path:
    return _backup_root(paths) / CONFIG_FILE


def _read_config(paths: LovePaths) -> dict[str, Any]:
    path = _config_path(paths)
    try:
        if not path.is_file() or path.stat().st_size > 16_384:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _configured_dir(paths: LovePaths) -> Path | None:
    raw = str(_read_config(paths).get("install_dir") or "").strip()
    return Path(raw) if raw else None


def _running_installs() -> list[Path]:
    """Use the running WeFlow.exe path when it was installed outside Program Files."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        output = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WeFlow.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[Path] = []
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    for line in (output.stdout or "").splitlines():
        if "weflow.exe" not in line.casefold():
            continue
        try:
            pid = int(line.split('","')[1])
        except (IndexError, ValueError):
            continue
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(32_768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                found.append(Path(buffer.value).parent)
        finally:
            kernel32.CloseHandle(handle)
    return found


def _candidate_roots(paths: LovePaths | None = None) -> list[Path]:
    candidates: list[Path] = []
    if paths is not None:
        configured = _configured_dir(paths)
        if configured is not None:
            candidates.append(configured)
    env = (os.environ.get("WEFLOW_HOME") or "").strip()
    if env:
        candidates.append(Path(env))
    candidates.extend(_running_installs())
    candidates.extend(_registry_installs())
    if os.name == "nt":
        for base in (
            os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not base:
                continue
            candidates.extend([
                Path(base) / "WeFlow",
                Path(base) / "Programs" / "WeFlow",
                Path(base) / "WeFlow" / "resources" / "resources" / "wcdb" / "win32" / "x64",
            ])
    # 也允许直接指向 dll 的父目录。
    return [path for path in candidates if path]


def _find_dll_in(root: Path) -> tuple[Path | None, Path | None]:
    if not root:
        return None, None
    direct = root / DLL_NAME
    if direct.is_file():
        return root, direct
    nested = root / COMMON_RELATIVE
    if nested.is_file():
        return root / "resources" / "resources" / "wcdb" / "win32" / "x64", nested
    try:
        for path in root.rglob(DLL_NAME):
            if path.is_file():
                return path.parent, path
    except OSError:
        pass
    return None, None


def _find_dll(paths: LovePaths | None = None) -> tuple[Path | None, Path | None]:
    for root in _candidate_roots(paths):
        directory, dll = _find_dll_in(root)
        if dll is not None:
            return directory, dll
    return None, None


def _backup_root(paths: LovePaths) -> Path:
    return paths.archive / BACKUP_DIR


def _latest_backup(paths: LovePaths) -> tuple[Path | None, dict[str, Any]]:
    root = _backup_root(paths)
    if not root.is_dir():
        return None, {}
    batches = sorted((item for item in root.iterdir() if item.is_dir()), reverse=True)
    for batch in batches:
        dll = batch / DLL_NAME
        if dll.is_file():
            meta = {}
            try:
                meta = json.loads((batch / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            return dll, meta
    return None, {}


def component_status(paths: LovePaths) -> dict[str, Any]:
    directory, dll = _find_dll(paths)
    configured = _configured_dir(paths)
    current_hash = _hash(dll) if dll else ""
    backup, meta = _latest_backup(paths)
    return {
        "installed": bool(dll),
        "install_dir": str(directory) if directory else "",
        "dll_path": str(dll) if dll else "",
        "configured_dir": str(configured) if configured else "",
        "configured_dir_exists": bool(configured and configured.is_dir()),
        "dll_hash": current_hash,
        "expired": current_hash in KNOWN_EXPIRED_HASHES,
        "source_project": SOURCE_PROJECT,
        "bundled_dll": str(_bundled_dll()) if _bundled_dll().is_file() else "",
        "backup": str(backup) if backup else "",
        "backup_meta": meta,
        "weflow_running": _weflow_running(),
        "note": "替换前请先完全退出 WeFlow（含托盘）；替换会备份当前 DLL，可回滚。",
    }


def _weflow_running() -> bool:
    if os.name != "nt":
        try:
            output = subprocess.run(
                ["pgrep", "-f", "WeFlow"], capture_output=True, text=True, timeout=3)
            return output.returncode == 0 and bool(output.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        output = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WeFlow.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return "weflow.exe" in (output.stdout or "").casefold()
    except (OSError, subprocess.SubprocessError):
        return False


def _validate_dll_bytes(raw: bytes) -> None:
    if not raw:
        raise HTTPException(400, "DLL 文件为空")
    if len(raw) > MAX_DLL_BYTES:
        raise HTTPException(413, "DLL 超过 64MB 上限")
    if not raw.startswith(b"MZ"):
        raise HTTPException(422, "这不是一个 Windows DLL（缺少 MZ 头）")


def replace_component(paths: LovePaths, raw: bytes, *, filename: str = "",
                      read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "替换 WeFlow 微信组件")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    if _weflow_running():
        raise HTTPException(409, "WeFlow 正在运行，请先完全退出（包括托盘）再替换组件")
    directory, dll = _find_dll(paths)
    if dll is None:
        raise HTTPException(404, "没有找到 WeFlow 的 wcdb_api.dll，请先安装 WeFlow，或在设置里手动指定安装目录")
    _validate_dll_bytes(raw)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch = _backup_root(paths) / stamp
    batch.mkdir(parents=True, exist_ok=True)
    backup = batch / DLL_NAME
    old_hash = _hash(dll)
    shutil.copy2(dll, backup)
    (batch / "meta.json").write_text(
        json.dumps({
            "schema": 1,
            "dll_path": str(dll),
            "backup": str(backup),
            "old_hash": old_hash,
            "new_hash": hashlib.sha256(raw).hexdigest(),
            "filename": filename,
            "replaced_at": datetime.now().isoformat(timespec="seconds"),
            "source_project": SOURCE_PROJECT,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=dll.parent, suffix=".dll") as handle:
            handle.write(raw)
            temporary = Path(handle.name)
        os.replace(temporary, dll)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise HTTPException(409, f"替换失败：{error}") from error
    return {
        "message": "组件已替换；请重新启动 WeFlow 后再测试连接。",
        "dll_path": str(dll),
        "backup": str(backup),
        "old_hash": old_hash,
        "new_hash": hashlib.sha256(raw).hexdigest(),
    }


def rollback_component(paths: LovePaths, *, read_only: bool = False) -> dict[str, Any]:
    try:
        ensure_vault_writable(read_only, "回滚 WeFlow 微信组件")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    if _weflow_running():
        raise HTTPException(409, "WeFlow 正在运行，请先完全退出（包括托盘）再回滚")
    backup, meta = _latest_backup(paths)
    if backup is None:
        raise HTTPException(404, "没有可回滚的组件备份")
    target = Path(str(meta.get("dll_path") or ""))
    if not target.is_file():
        _, target = _find_dll(paths)
    if target is None or not target.is_file():
        raise HTTPException(404, "没有找到当前 WeFlow 组件路径")
    try:
        shutil.copy2(backup, target)
    except OSError as error:
        raise HTTPException(409, f"回滚失败：{error}") from error
    return {
        "message": "已回滚到备份组件；请重新启动 WeFlow。",
        "dll_path": str(target),
        "backup": str(backup),
        "restored_hash": _hash(target),
    }


class InstallDirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=1024)


def _normalise_install_dir(raw: str) -> tuple[Path, Path, Path]:
    text = os.path.expandvars(str(raw or "").strip().strip('"'))
    if not text:
        raise HTTPException(422, "请填写 WeFlow 安装目录")
    candidate = Path(text).expanduser()
    if candidate.name.casefold() == DLL_NAME.casefold():
        candidate = candidate.parent
    if not candidate.is_absolute():
        raise HTTPException(422, "请填写完整的绝对路径，例如 C:\\Program Files\\WeFlow")
    try:
        candidate = candidate.resolve()
    except OSError as error:
        raise HTTPException(422, f"路径无法解析：{error}") from error
    if not candidate.is_dir():
        raise HTTPException(422, f"目录不存在：{candidate}")
    directory, dll = _find_dll_in(candidate)
    if dll is None:
        raise HTTPException(
            422, "这个目录里没有找到 wcdb_api.dll；请选择 WeFlow 安装目录或它下面的组件目录")
    return candidate, directory or candidate, dll


def set_install_dir(paths: LovePaths, raw: str, *, read_only: bool = False) -> dict[str, Any]:
    """保存用户手动指定的 WeFlow 目录；替换时优先从这里找 DLL。"""
    try:
        ensure_vault_writable(read_only, "保存 WeFlow 安装目录")
    except VaultReadOnlyError as error:
        raise HTTPException(409, str(error)) from error
    candidate, _, _ = _normalise_install_dir(raw)
    config = _read_config(paths)
    config.update({
        "schema": 1,
        "install_dir": str(candidate),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    target = _config_path(paths)
    temp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=target.parent, suffix=".json",
                encoding="utf-8", newline="\n") as handle:
            temp = Path(handle.name)
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp, target)
    except OSError as error:
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise HTTPException(409, f"保存目录失败：{error}") from error
    status = component_status(paths)
    status.update({
        "saved": True,
        "message": f"已保存 WeFlow 目录：{candidate}",
    })
    return status


def create_component_router(paths: LovePaths, *, read_only: bool = False,
                            prefix: str = "/api/love/weflow/component") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["love-weflow-component"])

    @router.get("/status")
    def status():
        return component_status(paths)

    @router.put("/install-dir")
    def install_dir(payload: InstallDirRequest):
        return set_install_dir(paths, payload.path, read_only=read_only)

    @router.post("/replace")
    async def replace(file: UploadFile | None = File(default=None)):
        raw = b""
        filename = ""
        if file is not None:
            raw = await file.read(MAX_DLL_BYTES + 1)
            filename = file.filename or ""
        else:
            bundled = _bundled_dll()
            if not bundled.is_file():
                raise HTTPException(
                    422,
                    "还没有选择 DLL，仓库 assets/weflow/ 下也没有预构建文件；"
                    "请按文档构建一次，或手动选择已构建的 wcdb_api.dll",
                )
            raw = bundled.read_bytes()
            filename = bundled.name
        return replace_component(paths, raw, filename=filename, read_only=read_only)

    @router.post("/rollback")
    def rollback():
        return rollback_component(paths, read_only=read_only)

    return router


__all__ = ["SOURCE_PROJECT", "component_status", "set_install_dir",
           "replace_component", "rollback_component", "create_component_router"]
