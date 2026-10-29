from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException, Request, status
from google.adk.events.event import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import (
    CREATE_SCHEMA_SQL,
    PRAGMA_FOREIGN_KEYS,
    SqliteSessionService,
)
from google.adk.utils._function_call_names import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.genai import types
from pydantic import BaseModel, Field

from .agents import build_adk_app
from .config import APP_NAME, SESSION_USER_ID, get_sessions_db_path
from .database import (
    init_database,
    listar_confirmacoes_respondidas_db,
    listar_reservas_apartamento_db,
    listar_visitantes_apartamento_db,
    registrar_resposta_confirmacao_atomica,
)


class AuroraSqliteSessionService(SqliteSessionService):
    """Serviço de sessão SQLite do ADK com WAL e busy_timeout para concorrência segura."""

    @asynccontextmanager
    async def _get_db_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._db_path in ("", ":memory:"):
            async with super()._get_db_connection() as conn:
                yield conn
            return

        async with aiosqlite.connect(
            self._db_connect_path, timeout=15.0, uri=self._db_connect_uri
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA busy_timeout = 15000;")
            await db.execute(PRAGMA_FOREIGN_KEYS)
            if not self._schema_ready:
                async with self._schema_lock:
                    if not self._schema_ready:
                        await db.executescript(CREATE_SCHEMA_SQL)
                        self._schema_ready = True
            yield db


class CriarSessaoRequest(BaseModel):
    apartamento: str = Field(..., min_length=1)


class CriarSessaoResponse(BaseModel):
    session_id: str


class EnviarMensagemRequest(BaseModel):
    texto: str = Field(..., min_length=1)


class ConfirmacaoPendenteModel(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class MensagemResponse(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendenteModel]


class ResponderConfirmacaoRequest(BaseModel):
    id: str = Field(..., min_length=1)
    confirmado: bool


def extrair_confirmacoes_pendentes(
    session_id: str, events: list[Event]
) -> list[dict[str, Any]]:
    """Extrai todas as confirmações de tool pendentes na sessão."""
    respondidas = listar_confirmacoes_respondidas_db(session_id)
    for ev in events:
        for fr in ev.get_function_responses():
            if fr.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME and fr.id:
                respondidas.add(fr.id)

    pendentes: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for ev in events:
        for fc in ev.get_function_calls():
            if (
                fc.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
                and fc.id
                and fc.id not in respondidas
                and fc.id not in vistos
            ):
                vistos.add(fc.id)
                args = fc.args or {}
                orig = args.get("originalFunctionCall")
                if not isinstance(orig, dict):
                    orig = {}
                acao = str(orig.get("name") or "confirmar_acao")
                orig_args = orig.get("args")
                detalhes = dict(orig_args) if isinstance(orig_args, dict) else {}
                pendentes.append(
                    {
                        "id": fc.id,
                        "acao": acao,
                        "detalhes": detalhes,
                    }
                )
    return pendentes


def _extrair_texto_resposta(eventos_turno: list[Event]) -> str:
    """Coleta a resposta textual final emitida pelos agentes no turno."""
    partes_texto: list[str] = []
    for ev in eventos_turno:
        if ev.partial or ev.author == "user" or not ev.content:
            continue
        fcs = ev.get_function_calls()
        if any(fc.name == "transfer_to_agent" for fc in fcs):
            continue
        textos_ev = [
            p.text.strip()
            for p in (ev.content.parts or [])
            if p.text and p.text.strip()
        ]
        if textos_ev:
            partes_texto.append("\n".join(textos_ev))
    return "\n\n".join(partes_texto)


def create_app(model_override: BaseLlm | None = None) -> FastAPI:
    """Cria a aplicação FastAPI conectada ao Runner do Google ADK e persistência SQLite."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_database(force_reset=False)
        sessions_db_path = get_sessions_db_path()
        sessions_db_path.parent.mkdir(parents=True, exist_ok=True)

        session_service = AuroraSqliteSessionService(db_path=str(sessions_db_path))
        adk_app = build_adk_app(model=model_override)
        runner = Runner(app=adk_app, session_service=session_service)

        app.state.session_service = session_service
        app.state.runner = runner
        app.state.session_locks: dict[str, asyncio.Lock] = {}
        try:
            yield
        finally:
            await runner.close()

    fastapi_app = FastAPI(
        title="Residencial Aurora — Assistente Virtual",
        version="0.1.0",
        lifespan=lifespan,
    )

    def _get_session_lock(request: Request, session_id: str) -> asyncio.Lock:
        locks: dict[str, asyncio.Lock] = request.app.state.session_locks
        lock = locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[session_id] = lock
        return lock

    async def _obter_sessao_ou_404(request: Request, session_id: str) -> Any:
        session_service: AuroraSqliteSessionService = request.app.state.session_service
        session = await session_service.get_session(
            app_name=APP_NAME,
            user_id=SESSION_USER_ID,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão não encontrada.",
            )
        return session

    @fastapi_app.post(
        "/sessoes",
        response_model=CriarSessaoResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def criar_sessao(
        body: CriarSessaoRequest, request: Request
    ) -> CriarSessaoResponse:
        apartamento = body.apartamento.strip()
        if not apartamento:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="O campo 'apartamento' não pode ser vazio.",
            )
        session_service: AuroraSqliteSessionService = request.app.state.session_service
        # Garantia 2: apartamento definido uma única vez na criação da sessão
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=SESSION_USER_ID,
            state={"apartamento": apartamento},
        )
        return CriarSessaoResponse(session_id=session.id)

    @fastapi_app.post(
        "/sessoes/{session_id}/mensagens",
        response_model=MensagemResponse,
        status_code=status.HTTP_200_OK,
    )
    async def enviar_mensagem(
        session_id: str,
        body: EnviarMensagemRequest,
        request: Request,
    ) -> MensagemResponse:
        await _obter_sessao_ou_404(request, session_id)
        runner: Runner = request.app.state.runner

        async with _get_session_lock(request, session_id):
            await _obter_sessao_ou_404(request, session_id)
            nova_mensagem = types.Content(
                role="user",
                parts=[types.Part.from_text(text=body.texto)],
            )
            eventos_turno: list[Event] = []
            async for ev in runner.run_async(
                user_id=SESSION_USER_ID,
                session_id=session_id,
                new_message=nova_mensagem,
            ):
                eventos_turno.append(ev)

            sessao_atualizada = await _obter_sessao_ou_404(request, session_id)
            pendentes = extrair_confirmacoes_pendentes(
                session_id, sessao_atualizada.events
            )
            resposta = _extrair_texto_resposta(eventos_turno)
            return MensagemResponse(
                resposta=resposta,
                confirmacoes_pendentes=pendentes,
            )

    @fastapi_app.post(
        "/sessoes/{session_id}/confirmacoes",
        response_model=MensagemResponse,
        status_code=status.HTTP_200_OK,
    )
    async def responder_confirmacao(
        session_id: str,
        body: ResponderConfirmacaoRequest,
        request: Request,
    ) -> MensagemResponse:
        await _obter_sessao_ou_404(request, session_id)
        runner: Runner = request.app.state.runner

        async with _get_session_lock(request, session_id):
            sessao = await _obter_sessao_ou_404(request, session_id)
            pendentes_atuais = extrair_confirmacoes_pendentes(
                session_id, sessao.events
            )
            ids_pendentes = {item["id"] for item in pendentes_atuais}
            if body.id not in ids_pendentes:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Não existe confirmação pendente com esse id nesta sessão.",
                )

            registrado = registrar_resposta_confirmacao_atomica(
                session_id=session_id,
                confirmation_id=body.id,
                confirmado=body.confirmado,
            )
            if not registrado:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Esta confirmação já foi respondida nesta sessão.",
                )

            mensagem_confirmacao = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=body.id,
                            name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                            response={"confirmed": body.confirmado},
                        )
                    )
                ],
            )
            eventos_turno: list[Event] = []
            async for ev in runner.run_async(
                user_id=SESSION_USER_ID,
                session_id=session_id,
                new_message=mensagem_confirmacao,
            ):
                eventos_turno.append(ev)

            sessao_atualizada = await _obter_sessao_ou_404(request, session_id)
            pendentes = extrair_confirmacoes_pendentes(
                session_id, sessao_atualizada.events
            )
            resposta = _extrair_texto_resposta(eventos_turno)
            return MensagemResponse(
                resposta=resposta,
                confirmacoes_pendentes=pendentes,
            )

    @fastapi_app.get(
        "/sessoes/{session_id}/eventos",
        status_code=status.HTTP_200_OK,
    )
    async def listar_eventos_sessao(
        session_id: str, request: Request
    ) -> list[dict[str, Any]]:
        sessao = await _obter_sessao_ou_404(request, session_id)
        return [
            ev.model_dump(mode="json", by_alias=True, exclude_none=True)
            for ev in sessao.events
        ]

    @fastapi_app.get(
        "/apartamentos/{apartamento}/reservas",
        status_code=status.HTTP_200_OK,
    )
    async def verificar_reservas_apartamento(
        apartamento: str,
    ) -> list[dict[str, str]]:
        return listar_reservas_apartamento_db(apartamento.strip())

    @fastapi_app.get(
        "/apartamentos/{apartamento}/visitantes",
        status_code=status.HTTP_200_OK,
    )
    async def verificar_visitantes_apartamento(
        apartamento: str,
    ) -> list[dict[str, str]]:
        return listar_visitantes_apartamento_db(apartamento.strip())

    return fastapi_app


app = create_app()

