r"""Restore a torch-os full-vault cloud backup into a new directory.

Usage (Windows, from the repo root):

    .\.venv\Scripts\python.exe scripts\restore_full_backup.py ^
        "D:\BaiduSyncdisk\薪火OS备份\torch-os-full-....zip.enc" ^
        "D:\恢复的薪火OS"

The script never overwrites an existing vault: the target must be empty or new.
For encrypted backups it asks for the password with getpass (it is not echoed).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.cloud_backup import (  # noqa: E402
    FORMAT,
    FORMAT_VERSION,
    CloudBackupError,
    decrypt_file,
)


def _safe_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or ":" in name or any(ord(char) < 32 for char in name):
        raise CloudBackupError(f"压缩包里有不安全的路径：{name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(
            part in {"", ".", ".."} for part in path.parts):
        raise CloudBackupError(f"压缩包里有不安全的路径：{name!r}")
    if name != "manifest.json":
        if len(path.parts) < 2 or path.parts[0] != "vault":
            raise CloudBackupError(f"未知的压缩包条目：{name}")
    return path


def _manifest(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read("manifest.json")
        manifest = json.loads(raw.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, ValueError) as error:
        raise CloudBackupError("压缩包里缺少可读的 manifest.json") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise CloudBackupError("这不是薪火OS完整备份")
    if manifest.get("version") != FORMAT_VERSION:
        raise CloudBackupError("完整备份版本不受支持")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise CloudBackupError("完整备份清单为空")
    return manifest


def _verify(archive: zipfile.ZipFile, manifest: dict) -> None:
    names = set(archive.namelist())
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise CloudBackupError("完整备份清单格式不正确")
        name = str(item.get("path") or "")
        _safe_member(name)
        if name not in names:
            raise CloudBackupError(f"压缩包缺少文件：{name}")
        if int(item.get("size") or 0) != archive.getinfo(name).file_size:
            raise CloudBackupError(f"文件大小不一致：{name}")


def _check_target(target: Path) -> None:
    if target.exists():
        if not target.is_dir():
            raise CloudBackupError("恢复目标是已存在的文件，请选择一个新目录")
        if any(target.iterdir()):
            raise CloudBackupError("恢复目标不是空目录；为避免覆盖，请换一个空目录")
    else:
        target.mkdir(parents=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复薪火OS完整备份到新目录")
    parser.add_argument("archive", help="torch-os-full-*.zip 或 .zip.enc")
    parser.add_argument("target", help="新的空目录（不能是当前正在使用的库）")
    parser.add_argument("--list", action="store_true", help="只列出清单，不解压")
    args = parser.parse_args()
    archive_path = Path(args.archive).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    if not archive_path.is_file():
        print(f"找不到备份文件：{archive_path}", file=sys.stderr)
        return 2

    temporary: Path | None = None
    try:
        if archive_path.name.endswith(".enc"):
            password = getpass.getpass("备份密码：")
            handle = tempfile.NamedTemporaryFile(
                prefix="torch-restore-", suffix=".zip", delete=False)
            temporary = Path(handle.name)
            handle.close()
            decrypt_file(archive_path, temporary, password)
            zip_path = temporary
        else:
            zip_path = archive_path

        with zipfile.ZipFile(zip_path) as archive:
            manifest = _manifest(archive)
            _verify(archive, manifest)
            if args.list:
                print(json.dumps(manifest, ensure_ascii=False, indent=2))
                return 0
            _check_target(target)
            for item in manifest["files"]:
                name = str(item["path"])
                _safe_member(name)
                destination = target / PurePosixPath(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as incoming, destination.open("xb") as outgoing:
                    digest = hashlib.sha256()
                    while chunk := incoming.read(1024 * 1024):
                        outgoing.write(chunk)
                        digest.update(chunk)
                expected = str(item.get("sha256") or "")
                if expected and digest.hexdigest() != expected:
                    raise CloudBackupError(f"文件校验失败：{name}")
            (target / "manifest.json").write_bytes(archive.read("manifest.json"))

        print(f"恢复完成：{target}")
        print("下一步：")
        print("1. 完全退出 Obsidian / 薪火OS 后端。")
        print("2. 备份或改名当前个人数据库，避免覆盖。")
        print("3. 把上面目标目录里的 vault 文件夹内容放回个人数据库路径。")
        print("4. 重新打开 Obsidian；如果换了目录，记得同步修改 .env 的 VAULT_DIR。")
        return 0
    except (CloudBackupError, zipfile.BadZipFile, OSError) as error:
        print(f"恢复失败：{error}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
