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
# 默认直接沿用原「恋爱分析助手」的本地数据库，因此已有项目和聊天分析不需要迁移。
# 这是一份本机数据，不进仓库、不进备份；路径可用 .env 覆盖。
_LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
LOVE_DB_PATH = _path_from_env(
    "LOVE_DB_PATH", _LOCALAPPDATA / "love-analysis-assistant" / "love_analysis.db"
)
# 原项目目录：交路表 Excel、时刻表和停用词都在这里。未配置时恋爱日志/统计仍可用，
# 只有「丽洁追踪」会提示找不到交路表。
LOVE_APP_DIR = _path_from_env("LOVE_APP_DIR", ROOT_DIR)
LOVE_EXCEL_DIR = _path_from_env("LOVE_EXCEL_DIR", LOVE_APP_DIR / "胡丽洁" / "file" / "xlsx")
LOVE_TIMETABLE_PATH = _path_from_env(
    "LOVE_TIMETABLE_PATH", LOVE_APP_DIR / "backend" / "data" / "train_timetable.json"
)
LOVE_STOPWORDS_PATH = _path_from_env(
    "LOVE_STOPWORDS_PATH", LOVE_APP_DIR / "resources" / "stopwords" / "cn_stopwords.txt"
)
LOVE_API_BASE_URL = os.getenv("LOVE_API_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LOVE_API_MODEL = os.getenv("LOVE_API_MODEL", "deepseek-chat")



def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
