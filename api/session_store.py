from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agent.multi_runner import AgentReply, MultiAgentRunner, Phase, SubmitResult, create_api_runner
from agent.stream_events import StreamEvent

from .session_repository import MessageRecord, SessionRecord, SessionRepository


@dataclass
class SessionMessageRecord:
    id: str
    role: str
    content: str
    agent: str | None = None
    phase: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class StoredSession:
    record: SessionRecord
    runner: MultiAgentRunner | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def session_id(self) -> str:
        return self.record.session_id

    @property
    def user_id(self) -> str:
        return self.record.user_id

    @property
    def memory_session_id(self) -> str:
        return self.record.memory_session_id

    @property
    def project_root(self) -> Path:
        return self.record.project_root

    @property
    def continue_maintenance(self) -> bool:
        return self.record.continue_maintenance

    @property
    def phase(self) -> str:
        if self.runner is not None:
            return self.runner.phase.value
        return self.record.phase

    @property
    def created_at(self) -> datetime:
        return self.record.created_at

    @property
    def updated_at(self) -> datetime:
        return self.record.updated_at

    @property
    def requirements_form(self) -> dict | None:
        return self.record.requirements_form


class SessionStore:
    def __init__(self, repository: SessionRepository | None = None) -> None:
        self._repository = repository or SessionRepository()
        self._runners: dict[str, StoredSession] = {}

    async def create(
        self,
        *,
        user_id: str,
        project_root: Path,
        continue_maintenance: bool = False,
        memory_session_id: str | None = None,
    ) -> tuple[StoredSession, SubmitResult]:
        session_id = str(uuid.uuid4())
        runner = create_api_runner(
            project_root=project_root,
            session_id=memory_session_id,
            continue_maintenance=continue_maintenance,
            user_id=user_id,
        )
        open_result = await runner.open()
        record = await self._repository.create_session(
            session_id=session_id,
            user_id=user_id,
            memory_session_id=open_result.memory_session_id,
            project_root=project_root,
            continue_maintenance=continue_maintenance,
            phase=open_result.phase.value,
        )
        stored = StoredSession(record=record, runner=runner)
        self._runners[session_id] = stored
        await self._record_agent_messages(stored, open_result.phase.value, open_result.messages)
        return stored, open_result

    async def list_sessions(self, user_id: str) -> list[tuple[SessionRecord, int]]:
        records = await self._repository.list_sessions(user_id)
        summaries: list[tuple[SessionRecord, int]] = []
        for record in records:
            message_count = await self._repository.count_messages(record.session_id)
            summaries.append((record, message_count))
        return summaries

    async def get_record(self, session_id: str, user_id: str) -> SessionRecord:
        record = await self._repository.get_session(session_id, user_id)
        if record is None:
            raise KeyError(session_id)
        return record

    async def get_messages(self, session_id: str, user_id: str) -> list[SessionMessageRecord]:
        await self.get_record(session_id, user_id)
        rows = await self._repository.list_messages(session_id)
        return [_to_session_message(row) for row in rows]

    async def count_messages(self, session_id: str) -> int:
        return await self._repository.count_messages(session_id)

    async def update_project_root(
        self,
        session_id: str,
        user_id: str,
        project_root: Path,
    ) -> tuple[StoredSession, int]:
        stored = await self._attach_runner(session_id, user_id)
        resolved = project_root.expanduser().resolve()
        async with stored.lock:
            await stored.runner.set_project_root(resolved)
            stored.record = await self._repository.update_session(
                session_id,
                user_id,
                project_root=resolved,
            ) or stored.record
            await self._record_status_message(
                stored,
                stored.runner.phase.value,
                f"工作目录已更新为：{resolved}",
            )
        message_count = await self._repository.count_messages(session_id)
        return stored, message_count

    async def submit(self, session_id: str, user_id: str, message: str) -> SubmitResult:
        stored = await self._attach_runner(session_id, user_id)
        async with stored.lock:
            phase = stored.runner.phase.value
            await self._record_user_message(stored, phase, message)
            await self._clear_requirements_form_if_pending(stored)
            result = await stored.runner.submit(message)
            await self._record_agent_messages(stored, result.phase.value, result.messages)
            await self._sync_runner_state(stored, result)
            return result

    async def submit_stream(
        self,
        session_id: str,
        user_id: str,
        message: str,
    ) -> AsyncIterator[StreamEvent]:
        stored = await self._attach_runner(session_id, user_id)
        async with stored.lock:
            phase = stored.runner.phase.value
            await self._record_user_message(stored, phase, message)
            cleared_form = await self._clear_requirements_form_if_pending(stored)
            if cleared_form:
                yield StreamEvent("form_clear", {})
            result: SubmitResult | None = None
            async for event in stored.runner.submit_stream(message):
                if event.event == "done":
                    result = SubmitResult(
                        phase=Phase(event.data["phase"]),
                        phase_changed=event.data.get("phase_changed", False),
                        memory_session_id=event.data["memory_session_id"],
                        messages=[
                            AgentReply(
                                role=item["role"],
                                content=item["content"],
                                agent=item.get("agent"),
                            )
                            for item in event.data.get("messages", [])
                        ],
                        requirements_form=event.data.get("requirements_form"),
                    )
                yield event
            if result is not None:
                await self._record_agent_messages(stored, result.phase.value, result.messages)
                await self._sync_runner_state(stored, result)

    async def delete(self, session_id: str, user_id: str) -> None:
        await self.get_record(session_id, user_id)
        cached = self._runners.pop(session_id, None)
        if cached is not None and cached.runner is not None:
            await cached.runner.close()
        deleted = await self._repository.delete_session(session_id, user_id)
        if not deleted:
            raise KeyError(session_id)

    async def close_all(self) -> None:
        session_ids = list(self._runners.keys())
        for session_id in session_ids:
            stored = self._runners.pop(session_id)
            if stored.runner is not None:
                await stored.runner.close()

    async def _attach_runner(self, session_id: str, user_id: str) -> StoredSession:
        cached = self._runners.get(session_id)
        if cached is not None and cached.runner is not None:
            if cached.user_id != user_id:
                raise KeyError(session_id)
            return cached

        record = await self.get_record(session_id, user_id)
        runner = create_api_runner(
            project_root=record.project_root,
            session_id=record.memory_session_id,
            continue_maintenance=record.continue_maintenance,
            user_id=user_id,
        )
        runner.apply_persisted_state(
            phase=Phase(record.phase),
            confirmed_requirements=record.confirmed_requirements,
            confirmed_prototype=record.confirmed_prototype,
            backend_result=record.backend_result,
        )
        await runner.restore()
        stored = StoredSession(record=record, runner=runner)
        self._runners[session_id] = stored
        return stored

    async def _sync_runner_state(
        self,
        stored: StoredSession,
        result: SubmitResult | None = None,
    ) -> None:
        if stored.runner is None:
            return
        update_kwargs: dict = {
            "phase": stored.runner.phase.value,
            "confirmed_requirements": stored.runner.confirmed_requirements,
            "confirmed_prototype": stored.runner.confirmed_prototype,
            "backend_result": stored.runner.backend_result,
        }
        if result is not None:
            if result.requirements_form is not None:
                update_kwargs["requirements_form"] = result.requirements_form
            else:
                update_kwargs["clear_requirements_form"] = True
        stored.record = await self._repository.update_session(
            stored.session_id,
            stored.user_id,
            **update_kwargs,
        ) or stored.record

    async def _clear_requirements_form_if_pending(self, stored: StoredSession) -> bool:
        if stored.record.requirements_form is None:
            return False
        stored.record = await self._repository.update_session(
            stored.session_id,
            stored.user_id,
            clear_requirements_form=True,
        ) or stored.record
        return True

    async def _record_user_message(self, stored: StoredSession, phase: str, content: str) -> None:
        await self._persist_messages(stored.session_id, [
            SessionMessageRecord(
                id=str(uuid.uuid4()),
                role="user",
                content=content,
                phase=phase,
            )
        ])

    async def _record_status_message(self, stored: StoredSession, phase: str, content: str) -> None:
        await self._persist_messages(stored.session_id, [
            SessionMessageRecord(
                id=str(uuid.uuid4()),
                role="status",
                content=content,
                phase=phase,
            )
        ])

    async def _record_agent_messages(
        self,
        stored: StoredSession,
        phase: str,
        messages: list[AgentReply],
    ) -> None:
        records = [
            SessionMessageRecord(
                id=str(uuid.uuid4()),
                role=message.role,
                content=message.content,
                agent=message.agent,
                phase=phase,
            )
            for message in messages
        ]
        await self._persist_messages(stored.session_id, records)

    async def _persist_messages(
        self,
        session_id: str,
        messages: list[SessionMessageRecord],
    ) -> None:
        await self._repository.add_messages(
            session_id,
            [
                MessageRecord(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    agent=message.agent,
                    phase=message.phase,
                    created_at=message.created_at,
                )
                for message in messages
            ],
        )


def _to_session_message(row: MessageRecord) -> SessionMessageRecord:
    return SessionMessageRecord(
        id=row.id,
        role=row.role,
        content=row.content,
        agent=row.agent,
        phase=row.phase,
        created_at=row.created_at,
    )
