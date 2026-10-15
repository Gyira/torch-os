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
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "local")
MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "").rstrip("/")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "45"))


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
