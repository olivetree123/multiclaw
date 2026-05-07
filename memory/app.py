from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .config import MemoryConfig, load_memory_config
from .embeddings import HashEmbeddingProvider
from .llm import SummaryLLM
from .qdrant_store import MemorySearchResult, QdrantMemoryStore
from .repository import MemoryRepository
from .summary_worker import SummaryWorker
from .tokens import count_message_tokens


RECOVERY_ASSISTANT_MESSAGE = "上一轮对话因系统中断未完成，没有生成有效回复。请重新发送该请求。"


@dataclass(frozen=True)
class SessionInfo:
    id: str
    workspace: str | None


class MemoryApp:
    def __init__(self, config: MemoryConfig | None = None, *, start_worker: bool = True) -> None:
        self.config = config or load_memory_config()
        self.repository = MemoryRepository(self.config.database_url)
        self.start_worker = start_worker

        embedding_provider = HashEmbeddingProvider(dimensions=self.config.embedding_dimensions)
        self.qdrant_store = QdrantMemoryStore(
            url=self.config.qdrant_url,
            api_key=self.config.qdrant_api_key,
            collection_name=self.config.qdrant_collection,
            embedding_provider=embedding_provider,
        )
        self.qdrant_store.ensure_collection()

        self.summary_llm = SummaryLLM(
            model=self.config.llm_model,
            base_url=self.config.llm_base_url,
            api_key=self.config.llm_api_key,
        )
        self.summary_worker = SummaryWorker(
            repository=self.repository,
            qdrant_store=self.qdrant_store,
            summary_llm=self.summary_llm,
            interval_seconds=self.config.summary_interval_seconds,
            max_batch_tokens=self.config.summary_batch_max_tokens,
            recent_history_max_messages=self.config.recent_history_max_messages,
            recent_history_token_limit=self.config.recent_history_token_limit,
        )
    async def initialize(self) -> None:
        await self.repository.initialize()
        await asyncio.to_thread(self.qdrant_store.ensure_collection)
        if self.start_worker:
            self.summary_worker.start()

    async def add_messages(
        self,
        *,
        session_id: str,
        user_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        messages = [self._with_token_count(message) for message in messages]
        history_rows = await self.repository.add_history_messages(
            session_id=session_id,
            user_id=user_id,
            messages=messages,
        )
        await asyncio.to_thread(self.qdrant_store.upsert_history, history_rows)
        await self.repository.mark_history_indexed([row.id for row in history_rows])
        await self._wake_summary_worker_if_needed(user_id=user_id, session_id=session_id)

    async def repair_incomplete_turn(self, *, user_id: str, session_id: str) -> bool:
        latest = await self.repository.get_latest_history(user_id=user_id, session_id=session_id)
        if latest is None or latest.role != "user":
            return False

        await self.repository.add_history_messages(
            session_id=session_id,
            user_id=user_id,
            messages=[{
                "role": "assistant",
                "content": RECOVERY_ASSISTANT_MESSAGE,
                "token_count": count_message_tokens(
                    {"role": "assistant", "content": RECOVERY_ASSISTANT_MESSAGE},
                    model=self.config.llm_model,
                ),
                "recovery": True,
            }],
        )
        return True

    async def reindex_unindexed(self, *, user_id: str, session_id: str, batch_size: int = 100) -> int:
        indexed_count = 0

        while True:
            history_rows = await self.repository.get_unindexed_history(
                user_id=user_id,
                session_id=session_id,
                limit=batch_size,
            )
            if not history_rows:
                break

            # PostgreSQL 是主存储；Qdrant 只是派生索引，启动时可以安全地重复 upsert。
            await asyncio.to_thread(self.qdrant_store.upsert_history, history_rows)
            await self.repository.mark_history_indexed([row.id for row in history_rows])
            indexed_count += len(history_rows)

        while True:
            memory_rows = await self.repository.get_unindexed_memory(
                user_id=user_id,
                session_id=session_id,
                limit=batch_size,
            )
            if not memory_rows:
                break

            await asyncio.to_thread(self.qdrant_store.upsert_memory, memory_rows)
            await self.repository.mark_memory_indexed([row.id for row in memory_rows])
            indexed_count += len(memory_rows)

        return indexed_count

    async def ensure_session(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        workspace: str | None = None,
    ) -> SessionInfo:
        session = await self.repository.ensure_session(
            user_id=user_id,
            session_id=session_id,
            workspace=workspace,
        )
        return SessionInfo(id=str(session.id), workspace=session.workspace)

    async def search(
        self,
        *,
        query: str,
        user_id: str,
        session_id: str,
        limit: int | None = None,
    ) -> list[MemorySearchResult]:
        return await asyncio.to_thread(
            self.qdrant_store.search,
            query=query,
            user_id=user_id,
            session_id=session_id,
            limit=limit or self.config.search_limit,
        )

    async def build_context(
        self,
        *,
        query: str,
        user_id: str,
        session_id: str,
        limit: int | None = None,
    ) -> str:
        summaries = await self.repository.get_session_summaries(
            user_id=user_id,
            session_id=session_id,
            limit=limit or self.config.search_limit,
        )
        pending_history = await self.repository.get_pending_history(
            user_id=user_id,
            session_id=session_id,
        )
        results = await self.search(
            query=query,
            user_id=user_id,
            session_id=session_id,
            limit=limit,
        )
        if not summaries and not pending_history and not results:
            return ""

        lines = []
        for summary in reversed(summaries):
            lines.append(f"- [Summary] {summary.summary}")
        for history in pending_history:
            lines.append(f"- [History:{history.role}] {history.content}")
        for result in results:
            label = "Summary" if result.type == "summary" else "History"
            lines.append(f"- [{label}] {result.content}")
        return "\n".join(lines)

    async def get_session_workspace(self, *, user_id: str, session_id: str) -> str | None:
        return await self.repository.get_session_workspace(
            user_id=user_id,
            session_id=session_id,
        )

    async def set_session_workspace(self, *, user_id: str, session_id: str, workspace: str) -> None:
        await self.repository.set_session_workspace(
            user_id=user_id,
            session_id=session_id,
            workspace=workspace,
        )

    async def _wake_summary_worker_if_needed(self, *, user_id: str, session_id: str) -> None:
        # 触发判断看完整上下文压力：summary + 未总结 history；
        # 但 worker 仍只压缩未总结 history，避免反复总结已经压缩过的内容。
        summary_tokens = await self.repository.get_summary_token_count(
            user_id=user_id,
            session_id=session_id,
        )
        pending_tokens = await self.repository.get_pending_token_count(
            user_id=user_id,
            session_id=session_id,
        )
        context_tokens = summary_tokens + pending_tokens
        if context_tokens <= self.summary_trigger_tokens:
            return

        self.summary_worker.wake(
            user_id=user_id,
            session_id=session_id,
        )

    def _with_token_count(self, message: dict[str, Any]) -> dict[str, Any]:
        message_with_tokens = dict(message)
        # token_count 写入 history，后续用它判断何时触发 summary 以及每批总结多少内容。
        message_with_tokens["token_count"] = count_message_tokens(
            message,
            model=self.config.llm_model,
        )
        return message_with_tokens

    @property
    def summary_trigger_tokens(self) -> int:
        return int(self.config.model_max_context_tokens * self.config.summary_trigger_ratio)

    async def close(self) -> None:
        await self.summary_worker.stop()
        await asyncio.to_thread(self.qdrant_store.close)
        await self.repository.close()
