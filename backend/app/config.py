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
FRONTEND_DIR = ROOT_DIR / "frontend"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8765"))
# 插件按库启动的后端实例：库就是工作区，禁止在界面里再切库。
VAULT_SCOPED = os.getenv("TORCH_VAULT_SCOPED", "").strip().lower() in {"1", "true", "yes", "on"}

# ── 恋爱系统（原生模块）─────────────────────────────────────────────
# 运行时数据统一放在当前个人数据库的 .torch-os/love/：
#   love_analysis.db / .api_key.key / xlsx/ / train_timetable.json / cn_stopwords.txt
# LOVE_DATA_DIR 可显式覆盖；默认跟着 DATA_DIR（也就是所选个人数据库的 .torch-os）走。
LOVE_DATA_DIR = _path_from_env("LOVE_DATA_DIR", DATA_DIR / "love")
# 旧版位置：只在首次启动迁移时读取，迁移完成后原文件保留、不再作为运行时路径。
_LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
LOVE_LEGACY_DB_PATH = _path_from_env(
    "LOVE_DB_PATH", _LOCALAPPDATA / "love-analysis-assistant" / "love_analysis.db"
)
LOVE_LEGACY_EXCEL_DIR = _path_from_env(
    "LOVE_EXCEL_DIR", ROOT_DIR / "data" / "love" / "xlsx"
)
LOVE_APP_DIR = _path_from_env("LOVE_APP_DIR", ROOT_DIR)
LOVE_LEGACY_TIMETABLE_PATH = _path_from_env(
    "LOVE_TIMETABLE_PATH", LOVE_APP_DIR / "backend" / "data" / "train_timetable.json"
)
LOVE_STOPWORDS_PATH = _path_from_env(
    "LOVE_STOPWORDS_PATH", LOVE_APP_DIR / "resources" / "stopwords" / "cn_stopwords.txt"
)
# 排班对象姓名只从本机 .env 读取（示例见 .env.example），仓库里不写真实姓名；
# 留空时只在这一工作表恰好只有一个姓名时自动识别。
LOVE_TARGET_NAME = (os.getenv("LOVE_TARGET_NAME") or "").strip()
LOVE_API_BASE_URL = os.getenv("LOVE_API_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LOVE_API_MODEL = os.getenv("LOVE_API_MODEL", "deepseek-chat")



def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
