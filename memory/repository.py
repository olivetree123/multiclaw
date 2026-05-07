from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from tortoise import Tortoise, connections

from .models import History, Memory, Session, utc_now


class MemoryRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await Tortoise.init(
            db_url=self.database_url,
            modules={"models": ["memory.models"]},
        )
        await Tortoise.generate_schemas(safe=True)
        await self._configure_postgres_uuidv7_defaults()
        self._initialized = True

    async def create_session(self, *, user_id: str, workspace: str | None = None) -> Session:
        return await Session.create(user_id=user_id, workspace=workspace)

    async def ensure_session(self, *, user_id: str, session_id: str | None = None, workspace: str | None = None) -> Session:
        if session_id is None:
            return await self.create_session(user_id=user_id, workspace=workspace)

        session_uuid = self._session_uuid(session_id)
        row = await Session.get_or_none(id=session_uuid, user_id=user_id)
        if row is None:
            return await Session.create(
                id=session_uuid,
                user_id=user_id,
                workspace=workspace,
            )

        if workspace is not None and row.workspace != workspace:
            row.workspace = workspace
            await row.save()
        return row

    async def add_history_messages(
        self,
        *,
        session_id: str,
        user_id: str,
        messages: Iterable[dict[str, Any]],
    ) -> list[History]:
        session_uuid = self._session_uuid(session_id)
        rows: list[History] = []
        for message in messages:
            content = message.get("content")
            if content is None:
                content = ""

            rows.append(
                History(
                    session_id=session_uuid,
                    user_id=user_id,
                    role=message["role"],
                    content=str(content),
                    token_count=int(message.get("token_count") or 0),
                    extra_metadata=self._message_metadata(message),
                )
            )

        if rows:
            await History.bulk_create(rows)
        return rows

    async def add_summary(
        self,
        *,
        user_id: str,
        session_id: str,
        summary: str,
        source_history_ids: list[uuid.UUID],
        metadata: dict[str, Any] | None = None,
    ) -> Memory:
        session_uuid = self._session_uuid(session_id)
        source_history_id_strings = [str(history_id) for history_id in source_history_ids]
        row = await Memory.get_or_none(user_id=user_id, session_id=session_uuid)
        if row is None:
            row = await Memory.create(
                user_id=user_id,
                session_id=session_uuid,
                summary=summary,
                source_history_ids=source_history_id_strings,
                extra_metadata=metadata or {},
            )
        else:
            previous_summary = row.summary.strip()
            row.summary = f"{previous_summary}\n\n{summary}" if previous_summary else summary
            row.source_history_ids = [*row.source_history_ids, *source_history_id_strings]
            row.extra_metadata = metadata or {}
            await row.save()

        await History.filter(id__in=source_history_ids).update(
            is_summarized=True,
        )
        return row

    async def get_pending_history(self, *, user_id: str, session_id: str) -> list[History]:
        return await (
            History.filter(
                user_id=user_id,
                session_id=self._session_uuid(session_id),
                is_summarized=False,
            )
            .order_by("created_at")
        )

    async def get_latest_history(self, *, user_id: str, session_id: str) -> History | None:
        return await (
            History.filter(
                user_id=user_id,
                session_id=self._session_uuid(session_id),
            )
            .order_by("-created_at")
            .first()
        )

    async def get_unindexed_history(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[History]:
        return await (
            History.filter(
                user_id=user_id,
                session_id=self._session_uuid(session_id),
                indexed_at__isnull=True,
            )
            .order_by("created_at")
            .limit(limit)
        )

    async def get_unindexed_memory(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[Memory]:
        return await (
            Memory.filter(
                user_id=user_id,
                session_id=self._session_uuid(session_id),
                indexed_at__isnull=True,
            )
            .order_by("created_at")
            .limit(limit)
        )

    async def get_pending_token_count(self, *, user_id: str, session_id: str) -> int:
        rows = await self.get_pending_history(user_id=user_id, session_id=session_id)
        return sum(row.token_count for row in rows)

    async def get_summary_token_count(self, *, user_id: str, session_id: str) -> int:
        rows = await Memory.filter(user_id=user_id, session_id=self._session_uuid(session_id))
        return sum(int(row.extra_metadata.get("token_count") or 0) for row in rows)

    async def get_summarization_batch(
        self,
        *,
        user_id: str,
        session_id: str,
        recent_history_max_messages: int,
        recent_history_token_limit: int,
        max_batch_tokens: int,
    ) -> list[History]:
        rows = await self.get_pending_history(user_id=user_id, session_id=session_id)
        rows_to_summarize = self._old_history_before_recent_window(
            rows,
            recent_history_max_messages=recent_history_max_messages,
            recent_history_token_limit=recent_history_token_limit,
        )
        if not rows_to_summarize:
            return []

        # 保留一小段最近原文上下文，旧 history 才进入 summary；
        # 如果旧 history 很长，则按 max_batch_tokens 拆成多批。
        batch: list[History] = []
        batch_tokens = 0
        for row in rows_to_summarize:
            batch.append(row)
            batch_tokens += row.token_count
            if batch_tokens >= max_batch_tokens:
                break

        return batch

    @staticmethod
    def _old_history_before_recent_window(
        rows: list[History],
        *,
        recent_history_max_messages: int,
        recent_history_token_limit: int,
    ) -> list[History]:
        recent_rows: list[History] = []
        recent_tokens = 0
        for row in reversed(rows[-recent_history_max_messages:]):
            next_tokens = recent_tokens + row.token_count
            if recent_rows and next_tokens > recent_history_token_limit:
                break
            recent_rows.append(row)
            recent_tokens = next_tokens

        keep_count = len(recent_rows)
        if keep_count == 0:
            return rows
        return rows[:-keep_count]

    async def get_session_summaries(self, *, user_id: str, session_id: str, limit: int) -> list[Memory]:
        del limit
        return await (
            Memory.filter(user_id=user_id, session_id=self._session_uuid(session_id))
            .order_by("-created_at")
        )

    async def get_session_workspace(self, *, user_id: str, session_id: str) -> str | None:
        row = await Session.get_or_none(user_id=user_id, id=self._session_uuid(session_id))
        return row.workspace if row else None

    async def set_session_workspace(self, *, user_id: str, session_id: str, workspace: str) -> Session:
        return await self.ensure_session(user_id=user_id, session_id=session_id, workspace=workspace)

    async def mark_history_indexed(self, history_ids: Iterable[uuid.UUID]) -> None:
        ids = list(history_ids)
        if not ids:
            return
        await self._mark_indexed(History, ids)

    async def mark_memory_indexed(self, memory_ids: Iterable[uuid.UUID]) -> None:
        ids = list(memory_ids)
        if not ids:
            return
        await self._mark_indexed(Memory, ids)

    async def get_history(self, history_id: uuid.UUID) -> History | None:
        return await History.get_or_none(id=history_id)

    async def get_memory(self, memory_id: uuid.UUID) -> Memory | None:
        return await Memory.get_or_none(id=memory_id)

    async def close(self) -> None:
        if self._initialized:
            await Tortoise.close_connections()
            self._initialized = False

    async def _configure_postgres_uuidv7_defaults(self) -> None:
        if not self.database_url.startswith("postgres://"):
            return

        connection = connections.get("default")
        await connection.execute_script(
            """
            ALTER TABLE "session" ALTER COLUMN "id" SET DEFAULT uuidv7();
            ALTER TABLE "memory" ALTER COLUMN "id" SET DEFAULT uuidv7();
            ALTER TABLE "history" ALTER COLUMN "id" SET DEFAULT uuidv7();
            """
        )

    @staticmethod
    async def _mark_indexed(model: type[History] | type[Memory], ids: list[uuid.UUID]) -> None:
        await model.filter(id__in=ids).update(indexed_at=utc_now())

    @staticmethod
    def _session_uuid(session_id: str | uuid.UUID) -> uuid.UUID:
        return session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))

    @staticmethod
    def _message_metadata(message: dict[str, Any]) -> dict[str, Any]:
        excluded = {"role", "content", "token_count"}
        return {key: value for key, value in message.items() if key not in excluded}
