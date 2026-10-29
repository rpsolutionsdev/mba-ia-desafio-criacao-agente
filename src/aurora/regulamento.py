from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .config import DADOS_DIR

REGULAMENTO_PATH = DADOS_DIR / "regulamento.md"

_ROMAN_TO_INT: dict[str, int] = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
}
_INT_TO_ROMAN: dict[int, str] = {v: k for k, v in _ROMAN_TO_INT.items()}


@dataclass(frozen=True)
class CapituloRegulamento:
    numero: str
    titulo: str
    conteudo: str
    artigos: tuple[tuple[str, str], ...]


def _normalizar_texto(texto: str) -> str:
    sem_acentos = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", texto.lower())
        if not unicodedata.combining(ch)
    )
    return sem_acentos


@lru_cache(maxsize=1)
def carregar_indice_regulamento() -> tuple[CapituloRegulamento, ...]:
    """Lê dados/regulamento.md em memória estruturada por capítulo sem injetar texto no prompt."""
    texto = REGULAMENTO_PATH.read_text(encoding="utf-8")
    padrao_cap = re.compile(r"^##\s+Capítulo\s+([IVXLCDM]+):\s*(.+?)\s*$", re.MULTILINE)
    matches = list(padrao_cap.finditer(texto))

    capitulos: list[CapituloRegulamento] = []
    padrao_art = re.compile(r"^\*\*(Art\.\s*\d+[º.]?)\*\*", re.MULTILINE)

    for idx, match in enumerate(matches):
        numero = match.group(1).strip().upper()
        titulo = match.group(2).strip()
        inicio = match.start()
        fim = matches[idx + 1].start() if idx + 1 < len(matches) else len(texto)
        bloco = texto[inicio:fim].strip()

        art_matches = list(padrao_art.finditer(bloco))
        artigos: list[tuple[str, str]] = []
        for a_idx, a_match in enumerate(art_matches):
            rotulo = a_match.group(1).rstrip(".")
            a_ini = a_match.start()
            a_fim = (
                art_matches[a_idx + 1].start()
                if a_idx + 1 < len(art_matches)
                else len(bloco)
            )
            artigos.append((rotulo, bloco[a_ini:a_fim].strip()))

        capitulos.append(
            CapituloRegulamento(
                numero=numero,
                titulo=titulo,
                conteudo=bloco,
                artigos=tuple(artigos),
            )
        )

    return tuple(capitulos)


def _resolver_numero_capitulo(identificador: str) -> CapituloRegulamento | None:
    if not identificador or not isinstance(identificador, str):
        return None
    limpo = identificador.strip()
    limpo_sem_prefixo = re.sub(
        r"^(cap[ií]tulo|cap\.?)\s*", "", limpo, flags=re.IGNORECASE
    ).strip()

    candidato_romano = limpo_sem_prefixo.upper()
    if candidato_romano.isdigit():
        candidato_romano = _INT_TO_ROMAN.get(int(candidato_romano), "")

    capitulos = carregar_indice_regulamento()
    for cap in capitulos:
        if cap.numero == candidato_romano:
            return cap

    alvo_norm = _normalizar_texto(limpo_sem_prefixo)
    if alvo_norm:
        for cap in capitulos:
            if alvo_norm == _normalizar_texto(cap.titulo) or alvo_norm in _normalizar_texto(cap.titulo):
                return cap

    return None


def buscar_capitulos_por_termo(termo: str) -> dict[str, Any]:
    """Localiza capítulos pertinentes retornando apenas metadados (sem trechos de capítulos)."""
    consulta = _normalizar_texto((termo or "").strip())
    palavras = [p for p in re.split(r"\W+", consulta) if len(p) >= 3]
    if not palavras and consulta:
        palavras = [consulta]
    if not palavras:
        return {"sucesso": False, "erro": "Informe ao menos uma palavra-chave para busca."}

    resultados: list[dict[str, Any]] = []
    for cap in carregar_indice_regulamento():
        titulo_norm = _normalizar_texto(cap.titulo)
        artigos_relacionados: list[str] = []
        relevancia = 0

        for p in palavras:
            if p in titulo_norm:
                relevancia += 5

        for rotulo, texto_artigo in cap.artigos:
            art_norm = _normalizar_texto(texto_artigo)
            acertos = sum(1 for p in palavras if p in art_norm)
            if acertos > 0:
                relevancia += acertos
                artigos_relacionados.append(rotulo)

        if relevancia > 0:
            resultados.append(
                {
                    "relevancia": relevancia,
                    "capitulo": cap.numero,
                    "titulo": cap.titulo,
                    "artigos_relacionados": artigos_relacionados[:5],
                }
            )

    resultados.sort(key=lambda item: int(item["relevancia"]), reverse=True)
    # Remove campo interno de ordenação e limita aos capítulos mais pertinentes
    limpos = [
        {
            "capitulo": r["capitulo"],
            "titulo": r["titulo"],
            "artigos_relacionados": r["artigos_relacionados"],
        }
        for r in resultados[:3]
    ]
    return {"sucesso": True, "capitulos": limpos}


def obter_capitulo_regulamento(capitulo: str) -> dict[str, Any]:
    """Devolve exclusivamente o texto integral de um único capítulo solicitado."""
    cap = _resolver_numero_capitulo(capitulo)
    if cap is None:
        disponiveis = [f"{c.numero} ({c.titulo})" for c in carregar_indice_regulamento()]
        return {
            "sucesso": False,
            "erro": f"Capítulo '{capitulo}' não encontrado.",
            "capitulos_validos": disponiveis,
        }
    return {
        "sucesso": True,
        "capitulo": cap.numero,
        "titulo": cap.titulo,
        "conteudo": cap.conteudo,
    }

