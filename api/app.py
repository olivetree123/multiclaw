from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException

from agent.multi_runner import AgentReply
from auth import get_current_active_user, router as auth_router
from auth.models import User

from .database import close_database, init_database
from .schemas import (
    AgentReplyModel,
    CreateSessionRequest,
    CreateSessionResponse,
    SessionInfoResponse,
    SessionListResponse,
    SessionMessageModel,
    SessionMessagesResponse,
    SessionSummaryModel,
    SubmitMessageRequest,
    SubmitMessageResponse,
    UpdateSessionRequest,
    UpdateSessionResponse,
)
from .session_repository import SessionRecord
from .session_store import SessionMessageRecord, SessionStore, StoredSession
from .sse import sse_response, stream_session_message

load_dotenv(dotenv_path="./docker/.env")

session_store = SessionStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    yield
    await session_store.close_all()
    await close_database()


app = FastAPI(
    title="MultiClaw API",
    description="Multi-Agent 开发流程 HTTP 接口",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth_router)


def _to_reply_models(messages: list[AgentReply]) -> list[AgentReplyModel]:
    return [
        AgentReplyModel(role=message.role, content=message.content, agent=message.agent)
        for message in messages
    ]


def _to_message_models(messages: list[SessionMessageRecord]) -> list[SessionMessageModel]:
    return [
        SessionMessageModel(
            id=message.id,
            role=message.role,
            content=message.content,
            agent=message.agent,
            phase=message.phase,
            created_at=message.created_at,
        )
        for message in messages
    ]


def _to_session_summary(record: SessionRecord, message_count: int) -> SessionSummaryModel:
    return SessionSummaryModel(
        session_id=record.session_id,
        memory_session_id=record.memory_session_id,
        phase=record.phase,
        project_root=str(record.project_root),
        continue_maintenance=record.continue_maintenance,
        message_count=message_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_session_info(record: SessionRecord, message_count: int) -> SessionInfoResponse:
    return SessionInfoResponse(
        session_id=record.session_id,
        memory_session_id=record.memory_session_id,
        phase=record.phase,
        project_root=str(record.project_root),
        continue_maintenance=record.continue_maintenance,
        message_count=message_count,
        requirements_form=record.requirements_form,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_update_response(stored: StoredSession, message_count: int) -> UpdateSessionResponse:
    return UpdateSessionResponse(
        session_id=stored.session_id,
        memory_session_id=stored.memory_session_id,
        phase=stored.phase,
        project_root=str(stored.project_root),
        continue_maintenance=stored.continue_maintenance,
        message_count=message_count,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/sessions", response_model=SessionListResponse)
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SessionListResponse:
    sessions = await session_store.list_sessions(current_user.memory_user_id)
    summaries = [_to_session_summary(record, message_count) for record, message_count in sessions]
    return SessionListResponse(sessions=summaries, total=len(summaries))


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(
    body: CreateSessionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> CreateSessionResponse:
    project_root = Path(body.project_root).expanduser().resolve()
    try:
        stored, open_result = await session_store.create(
            user_id=current_user.memory_user_id,
            project_root=project_root,
            continue_maintenance=body.continue_maintenance,
            memory_session_id=body.memory_session_id,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return CreateSessionResponse(
        session_id=stored.session_id,
        memory_session_id=stored.memory_session_id,
        phase=open_result.phase.value,
        project_root=str(stored.project_root),
        messages=_to_reply_models(open_result.messages),
    )


@app.get("/api/v1/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SessionInfoResponse:
    try:
        record = await session_store.get_record(session_id, current_user.memory_user_id)
        message_count = await session_store.count_messages(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error

    return _to_session_info(record, message_count)


@app.patch("/api/v1/sessions/{session_id}", response_model=UpdateSessionResponse)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UpdateSessionResponse:
    project_root = Path(body.project_root).expanduser().resolve()
    try:
        stored, message_count = await session_store.update_project_root(
            session_id,
            current_user.memory_user_id,
            project_root,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return _to_update_response(stored, message_count)


@app.get("/api/v1/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def list_session_messages(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SessionMessagesResponse:
    try:
        messages = await session_store.get_messages(session_id, current_user.memory_user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error

    return SessionMessagesResponse(
        session_id=session_id,
        total=len(messages),
        messages=_to_message_models(messages),
    )


@app.post("/api/v1/sessions/{session_id}/messages")
async def submit_message(
    session_id: str,
    body: SubmitMessageRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    if body.stream:
        return sse_response(
            stream_session_message(
                session_store,
                session_id,
                current_user.memory_user_id,
                body.message,
            )
        )

    try:
        result = await session_store.submit(
            session_id,
            current_user.memory_user_id,
            body.message,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return SubmitMessageResponse(
        session_id=session_id,
        memory_session_id=result.memory_session_id,
        phase=result.phase.value,
        phase_changed=result.phase_changed,
        requirements_form=result.requirements_form,
        messages=_to_reply_models(result.messages),
    )


@app.post("/api/v1/sessions/{session_id}/messages/stream")
async def submit_message_stream(
    session_id: str,
    body: SubmitMessageRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return sse_response(
        stream_session_message(
            session_store,
            session_id,
            current_user.memory_user_id,
            body.message,
        )
    )


@app.delete("/api/v1/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    try:
        await session_store.delete(session_id, current_user.memory_user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error
