from __future__ import annotations

from typing import Any, Optional

from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from .database import (
    autorizar_visitante_db,
    cancelar_reserva_apartamento_db,
    consultar_disponibilidade_db,
    criar_reserva_atomica_db,
    listar_areas_db,
    listar_reservas_apartamento_db,
    listar_visitantes_apartamento_db,
    obter_area_db,
)
from .regulamento import buscar_capitulos_por_termo, obter_capitulo_regulamento


def _obter_apartamento_sessao(tool_context: ToolContext) -> str:
    """Garantia 2: lê o apartamento autenticado exclusivamente do state da sessão."""
    apartamento = tool_context.state.get("apartamento")
    if not apartamento or not str(apartamento).strip():
        raise RuntimeError("Apartamento não definido no estado da sessão.")
    return str(apartamento).strip()


def requer_confirmacao_reserva(
    area: str = "",
    data: str = "",
    tool_context: ToolContext | None = None,
    **_: Any,
) -> bool:
    """Garantia 1: áreas com taxa maior que zero exigem confirmação pelo sistema."""
    area_info = obter_area_db(area)
    if area_info is None:
        return False
    return float(area_info["taxa"]) > 0.0


def listar_areas_comuns() -> dict[str, Any]:
    """Lista as áreas comuns reserváveis do condomínio com id, nome e taxa em reais."""
    return {"areas": listar_areas_db()}


def consultar_disponibilidade(area: str, data: str) -> dict[str, Any]:
    """Consulta se uma área comum está livre ou ocupada em determinada data (AAAA-MM-DD).

    Args:
        area: Identificador da área comum ("salao-de-festas", "churrasqueira" ou "quadra").
        data: Data desejada no formato AAAA-MM-DD (ex.: "2030-04-20").
    """
    return consultar_disponibilidade_db(area=area, data=data)


def listar_minhas_reservas(tool_context: ToolContext) -> dict[str, Any]:
    """Lista todas as reservas ativas pertencentes exclusivamente ao apartamento desta sessão."""
    apartamento = _obter_apartamento_sessao(tool_context)
    return {
        "apartamento": apartamento,
        "reservas": listar_reservas_apartamento_db(apartamento),
    }


def criar_reserva(
    area: str,
    data: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Cria uma reserva de área comum para o apartamento autenticado na sessão.

    Args:
        area: Identificador exato da área comum ("salao-de-festas", "churrasqueira" ou "quadra").
        data: Data da reserva no formato AAAA-MM-DD (ex.: "2030-04-20").
    """
    apartamento = _obter_apartamento_sessao(tool_context)
    return criar_reserva_atomica_db(apartamento=apartamento, area=area, data=data)


def cancelar_minha_reserva(
    tool_context: ToolContext,
    area: Optional[str] = None,
    data: Optional[str] = None,
    codigo: Optional[str] = None,
) -> dict[str, Any]:
    """Cancela uma reserva ativa do próprio apartamento da sessão (sem exigir confirmação).

    Args:
        area: Identificador da área comum ("salao-de-festas", "churrasqueira" ou "quadra"), opcional se informar codigo.
        data: Data da reserva no formato AAAA-MM-DD, opcional se informar codigo.
        codigo: Código da reserva (ex.: "RSV-1377"), opcional se informar area e data.
    """
    apartamento = _obter_apartamento_sessao(tool_context)
    return cancelar_reserva_apartamento_db(
        apartamento=apartamento,
        area=area,
        data=data,
        codigo=codigo,
    )


def listar_meus_visitantes(tool_context: ToolContext) -> dict[str, Any]:
    """Lista os visitantes autorizados exclusivamente para o apartamento desta sessão."""
    apartamento = _obter_apartamento_sessao(tool_context)
    return {
        "apartamento": apartamento,
        "visitantes": listar_visitantes_apartamento_db(apartamento),
    }


def autorizar_visitante(
    nome: str,
    data: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Registra autorização de entrada de visitante para o apartamento autenticado na sessão.

    Args:
        nome: Nome completo do visitante (ex.: "Joana Ribeiro").
        data: Data da visita no formato AAAA-MM-DD (ex.: "2030-04-21").
    """
    apartamento = _obter_apartamento_sessao(tool_context)
    return autorizar_visitante_db(apartamento=apartamento, nome=nome, data=data)


def buscar_indice_regulamento(termo: str) -> dict[str, Any]:
    """Busca no índice do regulamento interno por palavra-chave sem retornar o texto dos capítulos.

    Args:
        termo: Palavra-chave do assunto buscado (ex.: "piscina", "silêncio", "mudança").
    """
    return buscar_capitulos_por_termo(termo=termo)


def consultar_capitulo_regulamento(capitulo: str) -> dict[str, Any]:
    """Lê o texto apenas de um único capítulo específico do regulamento interno.

    Args:
        capitulo: Número romano do capítulo de "I" a "XIV" (ex.: "IV").
    """
    return obter_capitulo_regulamento(capitulo=capitulo)


# Definições de FunctionTool com garantias declarativas de confirmação
TOOLS_RESERVAS = [
    FunctionTool(listar_areas_comuns),
    FunctionTool(consultar_disponibilidade),
    FunctionTool(listar_minhas_reservas),
    FunctionTool(criar_reserva, require_confirmation=requer_confirmacao_reserva),
    FunctionTool(cancelar_minha_reserva, require_confirmation=False),
]

TOOLS_VISITANTES = [
    FunctionTool(listar_meus_visitantes),
    FunctionTool(autorizar_visitante, require_confirmation=True),
]

TOOLS_REGULAMENTO = [
    FunctionTool(buscar_indice_regulamento),
    FunctionTool(consultar_capitulo_regulamento),
]

