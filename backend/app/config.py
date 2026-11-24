from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


DATA_DIR = _path_from_env("DATA_DIR", ROOT_DIR / "data")
VAULT_DIR = _path_from_env("VAULT_DIR", DATA_DIR / "vault")
DB_PATH = DATA_DIR / "torch_os.sqlite3"
STORAGE_LAYOUT = None
if os.getenv("TORCH_STORAGE_CONFIG"):
    from .storage_layout import StorageLayout
    STORAGE_LAYOUT = StorageLayout.load(os.environ["TORCH_STORAGE_CONFIG"])
    VAULT_DIR = STORAGE_LAYOUT.notes
    DATA_DIR = STORAGE_LAYOUT.state / "vault-state"
    DB_PATH = DATA_DIR / "torch_os.sqlite3"
FRONTEND_DIR = ROOT_DIR / "frontend"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
# 插件按库启动的后端实例：库就是工作区，禁止在界面里再切库。
VAULT_SCOPED = os.getenv("TORCH_VAULT_SCOPED", "").strip().lower() in {"1", "true", "yes", "on"}
# B4b: mentor's validated archive updates are committed directly. Set
# TORCH_MENTOR_AUTONOMOUS_ARCHIVE=0 to fall back to old pending cards.
MENTOR_AUTONOMOUS_ARCHIVE = os.getenv(
    "TORCH_MENTOR_AUTONOMOUS_ARCHIVE", "1").strip().lower() in {"1", "true", "yes", "on"}

# ── 恋爱系统（v2）──────────────────────────────────────────────────
# 旧版位置：只在首次启动迁移时读取，迁移完成后原文件保留、不再作为运行时路径。
_LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
LOVE_LEGACY_DB_PATH = _path_from_env(
    "LOVE_DB_PATH", _LOCALAPPDATA / "love-analysis-assistant" / "love_analysis.db"
)
LOVE_APP_DIR = _path_from_env("LOVE_APP_DIR", ROOT_DIR)
LOVE_LEGACY_TIMETABLE_PATH = _path_from_env(
    "LOVE_TIMETABLE_PATH", LOVE_APP_DIR / "backend" / "data" / "train_timetable.json"
)
# 排班对象姓名只从本机 .env 读取（示例见 .env.example），仓库里不写真实姓名；
# 留空时只在这一工作表恰好只有一个姓名时自动识别。
LOVE_TARGET_NAME = (os.getenv("LOVE_TARGET_NAME") or "").strip()


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
