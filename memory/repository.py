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

    async def ensure_session(self,
                             *,
                             user_id: str,
                             session_id: str | None = None,
                             workspace: str | None = None) -> Session:
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
                ))

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

        await History.filter(id__in=source_history_ids).update(is_summarized=True, )
        return row

    async def get_pending_history(self, *, user_id: str, session_id: str) -> list[History]:
        return await (History.filter(
            user_id=user_id,
            session_id=self._session_uuid(session_id),
            is_summarized=False,
        ).order_by("created_at"))

    async def get_latest_history(self, *, user_id: str, session_id: str) -> History | None:
        return await (History.filter(
            user_id=user_id,
            session_id=self._session_uuid(session_id),
        ).order_by("-created_at").first())

    async def get_unindexed_history(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[History]:
        return await (History.filter(
            user_id=user_id,
            session_id=self._session_uuid(session_id),
            indexed_at__isnull=True,
        ).order_by("created_at").limit(limit))

    async def get_unindexed_memory(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[Memory]:
        return await (Memory.filter(
            user_id=user_id,
            session_id=self._session_uuid(session_id),
            indexed_at__isnull=True,
        ).order_by("created_at").limit(limit))

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
        """选择下一批应该被压缩成 summary 的旧 history。

        设计思想是：最近的对话保留原文，更早的对话压缩成 summary。
        summary 不是为了替代全部 history，而是为了在上下文变长时，把较早的信息
        压缩进长期记忆，同时给最新一段对话保留足够的原始细节。

        具体策略：
        - 取出当前会话中所有还没有总结过的 history；
        - 只保留 N 条最新 history 原文；
        - N 不是固定值，而是由最近消息的 token 数决定。

        N 的计算规则：
        1. 如果最近 20 条消息的总 token 数小于 `0.1 * model_max_context_tokens`，
           就保留最近 20 条消息；
        2. 如果最近 20 条消息的总 token 数超过了 `0.1 * model_max_context_tokens`，
           就从 N=20 开始不断减小 N，直到这 N 条最新消息的总 token 数低于这个限制。

        剩下没有被保留的旧 history 就是可以总结的内容。
        如果这些旧 history 的总 token 数超过 `summary_batch_max_tokens`，则不会一次性
        全部交给 summary LLM，而是从最旧的 history 开始截取一批；
        总结完这一批后会再次请求下一批，直到只剩需要保留的最近窗口。

        返回空列表表示：当前没有可总结的旧 history，或者所有未总结 history 都属于
        需要保留原文的最近窗口。
        """
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
            # 从最旧的 history 开始取，保证 summary 的时间顺序稳定。
            # 这里先把当前消息放入 batch，再判断是否超过上限；
            # 因此单条消息很长时可能会让本批略超过 max_batch_tokens，
            # 当前策略暂时接受这种情况，避免出现永远无法处理的单条超长消息。
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
        """根据“最近窗口”策略，找出最近窗口之前的旧 history。

        这个函数对应上面 N 的计算：
        - 优先尝试保留最多 20 条最新消息；
        - 如果这批消息太长，就缩小 N；
        - N 越小，保留的最近原文越少，可进入 summary 的旧 history 越多。

        最终返回值不是“要保留的最近消息”，而是“最近窗口之前、可以被 summary
        worker 压缩的旧消息”。

        即使最新一条消息本身已经超过 token 限制，也至少保留它，避免刚刚发生的
        对话立即被压缩掉。
        """
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

    async def get_session_summaries(self, *, user_id: str, session_id: str,
                                    limit: int) -> list[Memory]:
        del limit
        return await (Memory.filter(
            user_id=user_id, session_id=self._session_uuid(session_id)).order_by("-created_at"))

    async def get_session_workspace(self, *, user_id: str, session_id: str) -> str | None:
        row = await Session.get_or_none(user_id=user_id, id=self._session_uuid(session_id))
        return row.workspace if row else None

    async def set_session_workspace(self, *, user_id: str, session_id: str,
                                    workspace: str) -> Session:
        return await self.ensure_session(user_id=user_id,
                                         session_id=session_id,
                                         workspace=workspace)

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
        await connection.execute_script("""
            ALTER TABLE "session" ALTER COLUMN "id" SET DEFAULT uuidv7();
            ALTER TABLE "memory" ALTER COLUMN "id" SET DEFAULT uuidv7();
            ALTER TABLE "history" ALTER COLUMN "id" SET DEFAULT uuidv7();
            """)

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
