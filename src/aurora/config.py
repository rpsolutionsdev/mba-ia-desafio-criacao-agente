from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DADOS_DIR = PROJECT_ROOT / "dados"
DEFAULT_STORAGE_DIR = PROJECT_ROOT / "storage"

APP_NAME = "residencial_aurora"
SESSION_USER_ID = "morador"
DEFAULT_MODEL = "gemini-2.5-flash"


def get_storage_dir() -> Path:
    raw = (os.getenv("AURORA_STORAGE_DIR") or "").strip()
    storage_dir = Path(raw).resolve() if raw else DEFAULT_STORAGE_DIR
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def get_condominio_db_path() -> Path:
    return get_storage_dir() / "condominio.db"


def get_sessions_db_path() -> Path:
    return get_storage_dir() / "sessions.db"


def get_model_name() -> str:
    raw = (os.getenv("AURORA_MODEL") or "").strip()
    return raw or DEFAULT_MODEL

