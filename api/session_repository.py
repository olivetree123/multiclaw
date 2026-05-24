from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ApiSession, ApiSessionMessage


@dataclass
class SessionRecord:
    session_id: str
    user_id: str
    memory_session_id: str
    project_root: Path
    continue_maintenance: bool
    phase: str
    confirmed_requirements: str | None
    confirmed_prototype: str | None
    backend_result: str | None
    requirements_form: dict | None
    created_at: datetime
    updated_at: datetime


@dataclass
class MessageRecord:
    id: str
    role: str
    content: str
    agent: str | None
    phase: str | None
    created_at: datetime


class SessionRepository:

    async def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        memory_session_id: str,
        project_root: Path,
        continue_maintenance: bool,
        phase: str,
    ) -> SessionRecord:
        row = await ApiSession.create(
            id=uuid.UUID(session_id),
            user_id=user_id,
            memory_session_id=uuid.UUID(memory_session_id),
            project_root=str(project_root),
            continue_maintenance=continue_maintenance,
            phase=phase,
        )
        return _to_session_record(row)

    async def get_session(self, session_id: str, user_id: str) -> SessionRecord | None:
        row = await ApiSession.get_or_none(id=uuid.UUID(session_id), user_id=user_id)
        if row is None:
            return None
        return _to_session_record(row)

    async def list_sessions(self, user_id: str) -> list[SessionRecord]:
        rows = await ApiSession.filter(user_id=user_id).order_by("created_at")
        return [_to_session_record(row) for row in rows]

    async def update_session(
        self,
        session_id: str,
        user_id: str,
        *,
        project_root: Path | None = None,
        phase: str | None = None,
        confirmed_requirements: str | None = None,
        confirmed_prototype: str | None = None,
        backend_result: str | None = None,
        requirements_form: dict[str, Any] | None = None,
        clear_confirmed_requirements: bool = False,
        clear_confirmed_prototype: bool = False,
        clear_backend_result: bool = False,
        clear_requirements_form: bool = False,
    ) -> SessionRecord | None:
        row = await ApiSession.get_or_none(id=uuid.UUID(session_id), user_id=user_id)
        if row is None:
            return None

        if project_root is not None:
            row.project_root = str(project_root)
        if phase is not None:
            row.phase = phase
        if confirmed_requirements is not None:
            row.confirmed_requirements = confirmed_requirements
        elif clear_confirmed_requirements:
            row.confirmed_requirements = None
        if confirmed_prototype is not None:
            row.confirmed_prototype = confirmed_prototype
        elif clear_confirmed_prototype:
            row.confirmed_prototype = None
        if backend_result is not None:
            row.backend_result = backend_result
        elif clear_backend_result:
            row.backend_result = None
        if requirements_form is not None:
            row.requirements_form = requirements_form
        elif clear_requirements_form:
            row.requirements_form = None

        await row.save()
        return _to_session_record(row)

    async def delete_session(self, session_id: str, user_id: str) -> bool:
        session_uuid = uuid.UUID(session_id)
        deleted = await ApiSession.filter(id=session_uuid, user_id=user_id).delete()
        if not deleted:
            return False
        await ApiSessionMessage.filter(api_session_id=session_uuid).delete()
        return True

    async def add_messages(self, session_id: str, messages: list[MessageRecord]) -> None:
        if not messages:
            return
        session_uuid = uuid.UUID(session_id)
        await ApiSessionMessage.bulk_create([
            ApiSessionMessage(
                id=uuid.UUID(message.id),
                api_session_id=session_uuid,
                role=message.role,
                content=message.content,
                agent=message.agent,
                phase=message.phase,
                created_at=message.created_at,
            )
            for message in messages
        ])

    async def list_messages(self, session_id: str) -> list[MessageRecord]:
        rows = await ApiSessionMessage.filter(
            api_session_id=uuid.UUID(session_id),
        ).order_by("created_at")
        return [_to_message_record(row) for row in rows]

    async def count_messages(self, session_id: str) -> int:
        return await ApiSessionMessage.filter(api_session_id=uuid.UUID(session_id)).count()


def _to_session_record(row: ApiSession) -> SessionRecord:
    return SessionRecord(
        session_id=str(row.id),
        user_id=row.user_id,
        memory_session_id=str(row.memory_session_id),
        project_root=Path(row.project_root),
        continue_maintenance=row.continue_maintenance,
        phase=row.phase,
        confirmed_requirements=row.confirmed_requirements,
        confirmed_prototype=row.confirmed_prototype,
        backend_result=row.backend_result,
        requirements_form=row.requirements_form,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_message_record(row: ApiSessionMessage) -> MessageRecord:
    return MessageRecord(
        id=str(row.id),
        role=row.role,
        content=row.content,
        agent=row.agent,
        phase=row.phase,
        created_at=row.created_at,
    )
