from __future__ import annotations

import os

import uvicorn

from .config import get_condominio_db_path, get_sessions_db_path
from .database import init_database


def reset_data() -> None:
    """Restaura o banco de reservas, visitantes e sessões para o estado inicial em dados/."""
    init_database(force_reset=True)
    print(
        "Dados iniciais restaurados com sucesso!\n"
        f"  Condomínio DB : {get_condominio_db_path()}\n"
        f"  Sessões ADK DB: {get_sessions_db_path()}"
    )


def run_api() -> None:
    """Inicia o servidor HTTP da API do Residencial Aurora em http://localhost:8000."""
    init_database(force_reset=False)
    host = (os.getenv("AURORA_HOST") or "").strip() or "0.0.0.0"
    port_raw = (os.getenv("AURORA_PORT") or "").strip()
    port = int(port_raw) if port_raw.isdigit() else 8000
    uvicorn.run("aurora.api:app", host=host, port=port, reload=False)

