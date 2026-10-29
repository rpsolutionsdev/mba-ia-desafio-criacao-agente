from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import DADOS_DIR, get_condominio_db_path, get_sessions_db_path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sistema_meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS apartamentos (
    numero TEXT PRIMARY KEY,
    morador TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    taxa REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sequencia_reservas (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ultimo_numero INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reservas (
    codigo TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL REFERENCES areas(id),
    data TEXT NOT NULL,
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1))
);

-- Garantia 5: exclusividade imposta pelo próprio banco no instante do INSERT
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservas_area_data_ativa
    ON reservas(area, data)
    WHERE ativa = 1;

CREATE TABLE IF NOT EXISTS visitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmacoes_respondidas (
    session_id TEXT NOT NULL,
    confirmation_id TEXT NOT NULL,
    confirmado INTEGER NOT NULL CHECK (confirmado IN (0, 1)),
    respondido_em TEXT NOT NULL,
    PRIMARY KEY (session_id, confirmation_id)
);
"""

_AREA_ALIASES: dict[str, str] = {
    "salao-de-festas": "salao-de-festas",
    "salao-festas": "salao-de-festas",
    "salao": "salao-de-festas",
    "churrasqueira": "churrasqueira",
    "quadra": "quadra",
    "quadra-poliesportiva": "quadra",
}


@contextmanager
def _connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = db_path or get_condominio_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 15000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        yield conn
    finally:
        conn.close()


def _remover_arquivos_sqlite(db_path: Path) -> bool:
    removido = True
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                removido = False
    return removido


def _carregar_json_inicial(nome_arquivo: str) -> list[dict[str, Any]]:
    caminho = DADOS_DIR / nome_arquivo
    with caminho.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _extrair_maior_sufixo_codigo(reservas: list[dict[str, Any]]) -> int:
    maior = 5000
    for item in reservas:
        match = re.search(r"(\d+)$", str(item.get("codigo", "")))
        if match:
            maior = max(maior, int(match.group(1)))
    return maior


def init_database(force_reset: bool = False) -> None:
    """Inicializa o banco SQLite do condomínio; se force_reset=True, restaura os dados iniciais."""
    condominio_db = get_condominio_db_path()
    sessions_db = get_sessions_db_path()

    if force_reset:
        _remover_arquivos_sqlite(condominio_db)
        sess_limpo = _remover_arquivos_sqlite(sessions_db)
        if not sess_limpo and sessions_db.exists():
            with _connect(sessions_db) as sconn:
                sconn.execute("BEGIN IMMEDIATE;")
                for tbl in ("events", "sessions", "user_states", "app_states"):
                    sconn.execute(f"DROP TABLE IF EXISTS {tbl};")
                sconn.execute("COMMIT;")

    with _connect(condominio_db) as conn:
        if force_reset and condominio_db.exists():
            conn.execute("BEGIN IMMEDIATE;")
            for tbl in (
                "confirmacoes_respondidas",
                "visitantes",
                "reservas",
                "sequencia_reservas",
                "areas",
                "apartamentos",
                "sistema_meta",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {tbl};")
            conn.execute("COMMIT;")

        conn.executescript(SCHEMA_SQL)

        row = conn.execute(
            "SELECT valor FROM sistema_meta WHERE chave = 'inicializado'"
        ).fetchone()
        if row is not None and not force_reset:
            return

        apartamentos = _carregar_json_inicial("apartamentos.json")
        areas = _carregar_json_inicial("areas.json")
        reservas = _carregar_json_inicial("reservas.json")
        visitantes = _carregar_json_inicial("visitantes.json")
        ultimo_num = _extrair_maior_sufixo_codigo(reservas)

        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute("DELETE FROM confirmacoes_respondidas;")
            conn.execute("DELETE FROM visitantes;")
            conn.execute("DELETE FROM reservas;")
            conn.execute("DELETE FROM sequencia_reservas;")
            conn.execute("DELETE FROM areas;")
            conn.execute("DELETE FROM apartamentos;")

            conn.executemany(
                "INSERT INTO apartamentos (numero, morador) VALUES (?, ?)",
                [(str(a["numero"]), str(a["morador"])) for a in apartamentos],
            )
            conn.executemany(
                "INSERT INTO areas (id, nome, taxa) VALUES (?, ?, ?)",
                [(str(ar["id"]), str(ar["nome"]), float(ar["taxa"])) for ar in areas],
            )
            conn.executemany(
                "INSERT INTO reservas (codigo, apartamento, area, data, ativa) VALUES (?, ?, ?, ?, 1)",
                [
                    (
                        str(r["codigo"]),
                        str(r["apartamento"]),
                        str(r["area"]),
                        str(r["data"]),
                    )
                    for r in reservas
                ],
            )
            conn.execute(
                "INSERT INTO sequencia_reservas (id, ultimo_numero) VALUES (1, ?)",
                (ultimo_num,),
            )
            conn.executemany(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                [
                    (str(v["apartamento"]), str(v["nome"]), str(v["data"]))
                    for v in visitantes
                ],
            )
            conn.execute(
                "INSERT OR REPLACE INTO sistema_meta (chave, valor) VALUES ('inicializado', '1')"
            )
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def _slugify(valor: str) -> str:
    sem_acentos = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", valor.strip().lower())
        if not unicodedata.combining(ch)
    )
    return re.sub(r"[^a-z0-9]+", "-", sem_acentos).strip("-")


def normalizar_area(area: str) -> str | None:
    if not area or not isinstance(area, str):
        return None
    slug = _slugify(area)
    candidata = _AREA_ALIASES.get(slug, slug)
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM areas WHERE id = ?", (candidata,)
        ).fetchone()
        return str(row["id"]) if row else None


def validar_data_iso(data: str) -> str | None:
    if not data or not isinstance(data, str):
        return None
    texto = data.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        return None
    try:
        dt.date.fromisoformat(texto)
    except ValueError:
        return None
    return texto


def listar_areas_db() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, nome, taxa FROM areas ORDER BY id"
        ).fetchall()
        return [
            {"id": str(r["id"]), "nome": str(r["nome"]), "taxa": float(r["taxa"])}
            for r in rows
        ]


def obter_area_db(area: str) -> dict[str, Any] | None:
    area_id = normalizar_area(area)
    if not area_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, nome, taxa FROM areas WHERE id = ?", (area_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "nome": str(row["nome"]),
            "taxa": float(row["taxa"]),
        }


def consultar_disponibilidade_db(area: str, data: str) -> dict[str, Any]:
    """Consulta apenas se a área está livre na data, sem jamais expor apartamento ou código."""
    area_info = obter_area_db(area)
    if not area_info:
        return {
            "sucesso": False,
            "erro": "Área comum inválida. Use: salao-de-festas, churrasqueira ou quadra.",
        }
    data_iso = validar_data_iso(data)
    if not data_iso:
        return {
            "sucesso": False,
            "erro": "Data inválida. Use o formato AAAA-MM-DD.",
        }
    with _connect() as conn:
        ocupada = conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1 LIMIT 1",
            (area_info["id"], data_iso),
        ).fetchone()
    return {
        "sucesso": True,
        "area": area_info["id"],
        "nome_area": area_info["nome"],
        "taxa": area_info["taxa"],
        "data": data_iso,
        "disponivel": ocupada is None,
    }


def listar_reservas_apartamento_db(apartamento: str) -> list[dict[str, str]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT codigo, area, data
            FROM reservas
            WHERE apartamento = ? AND ativa = 1
            ORDER BY data ASC, codigo ASC
            """,
            (str(apartamento),),
        ).fetchall()
        return [
            {
                "codigo": str(r["codigo"]),
                "area": str(r["area"]),
                "data": str(r["data"]),
            }
            for r in rows
        ]


def listar_visitantes_apartamento_db(apartamento: str) -> list[dict[str, str]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT nome, data
            FROM visitantes
            WHERE apartamento = ?
            ORDER BY id ASC
            """,
            (str(apartamento),),
        ).fetchall()
        return [{"nome": str(r["nome"]), "data": str(r["data"])} for r in rows]


def criar_reserva_atomica_db(
    apartamento: str, area: str, data: str
) -> dict[str, Any]:
    """Grava uma reserva com exclusividade atômica no instante do INSERT."""
    area_info = obter_area_db(area)
    if not area_info:
        return {
            "sucesso": False,
            "erro": "Área comum inválida. Use: salao-de-festas, churrasqueira ou quadra.",
        }
    data_iso = validar_data_iso(data)
    if not data_iso:
        return {
            "sucesso": False,
            "erro": "Data inválida. Use o formato AAAA-MM-DD.",
        }

    area_id = area_info["id"]
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            seq_row = conn.execute(
                "SELECT ultimo_numero FROM sequencia_reservas WHERE id = 1"
            ).fetchone()
            proximo = int(seq_row["ultimo_numero"]) if seq_row else 5000

            while True:
                proximo += 1
                codigo = f"RSV-{proximo}"
                existente = conn.execute(
                    "SELECT 1 FROM reservas WHERE codigo = ? LIMIT 1",
                    (codigo,),
                ).fetchone()
                if existente is None:
                    break

            conn.execute(
                "UPDATE sequencia_reservas SET ultimo_numero = ? WHERE id = 1",
                (proximo,),
            )
            conn.execute(
                """
                INSERT INTO reservas (codigo, apartamento, area, data, ativa)
                VALUES (?, ?, ?, ?, 1)
                """,
                (codigo, str(apartamento), area_id, data_iso),
            )
            conn.execute("COMMIT;")
            return {
                "sucesso": True,
                "codigo": codigo,
                "area": area_id,
                "nome_area": area_info["nome"],
                "data": data_iso,
                "taxa": area_info["taxa"],
            }
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK;")
            return {
                "sucesso": False,
                "motivo": "indisponivel",
                "area": area_id,
                "data": data_iso,
                "mensagem": (
                    f"Não foi possível concluir a reserva: a área '{area_info['nome']}' "
                    f"já está ocupada na data {data_iso}."
                ),
            }
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def cancelar_reserva_apartamento_db(
    apartamento: str,
    area: str | None = None,
    data: str | None = None,
    codigo: str | None = None,
) -> dict[str, Any]:
    """Cancela exclusivamente reserva ativa pertencente ao apartamento da sessão."""
    codigo_limpo = (codigo or "").strip() or None
    area_id = normalizar_area(area) if area else None
    if area and not area_id:
        return {
            "sucesso": False,
            "erro": "Área comum inválida. Use: salao-de-festas, churrasqueira ou quadra.",
        }
    data_iso = validar_data_iso(data) if data else None
    if data and not data_iso:
        return {
            "sucesso": False,
            "erro": "Data inválida. Use o formato AAAA-MM-DD.",
        }

    if not codigo_limpo and not (area_id and data_iso):
        return {
            "sucesso": False,
            "erro": "Informe o código da reserva ou a área e a data (AAAA-MM-DD) para cancelar.",
        }

    filtros = ["apartamento = ?", "ativa = 1"]
    params: list[Any] = [str(apartamento)]
    if codigo_limpo:
        filtros.append("UPPER(codigo) = UPPER(?)")
        params.append(codigo_limpo)
    if area_id:
        filtros.append("area = ?")
        params.append(area_id)
    if data_iso:
        filtros.append("data = ?")
        params.append(data_iso)

    where_sql = " AND ".join(filtros)

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            row = conn.execute(
                f"SELECT codigo, area, data FROM reservas WHERE {where_sql} LIMIT 1",
                tuple(params),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK;")
                return {
                    "sucesso": False,
                    "mensagem": (
                        "Nenhuma reserva ativa encontrada para o seu apartamento "
                        "com os dados informados."
                    ),
                }

            conn.execute(
                "UPDATE reservas SET ativa = 0 WHERE codigo = ? AND apartamento = ? AND ativa = 1",
                (str(row["codigo"]), str(apartamento)),
            )
            conn.execute("COMMIT;")
            return {
                "sucesso": True,
                "codigo": str(row["codigo"]),
                "area": str(row["area"]),
                "data": str(row["data"]),
            }
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def autorizar_visitante_db(
    apartamento: str, nome: str, data: str
) -> dict[str, Any]:
    nome_limpo = (nome or "").strip()
    if not nome_limpo:
        return {"sucesso": False, "erro": "Informe o nome do visitante."}
    data_iso = validar_data_iso(data)
    if not data_iso:
        return {
            "sucesso": False,
            "erro": "Data inválida. Use o formato AAAA-MM-DD.",
        }

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (str(apartamento), nome_limpo, data_iso),
            )
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    return {
        "sucesso": True,
        "nome": nome_limpo,
        "data": data_iso,
    }


def registrar_resposta_confirmacao_atomica(
    session_id: str, confirmation_id: str, confirmado: bool
) -> bool:
    """Registra atomicamente que uma confirmação foi respondida; devolve False se já respondida."""
    agora = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                """
                INSERT INTO confirmacoes_respondidas
                    (session_id, confirmation_id, confirmado, respondido_em)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, confirmation_id, 1 if confirmado else 0, agora),
            )
            conn.execute("COMMIT;")
            return True
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK;")
            return False
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def listar_confirmacoes_respondidas_db(session_id: str) -> set[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT confirmation_id FROM confirmacoes_respondidas WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {str(r["confirmation_id"]) for r in rows}

