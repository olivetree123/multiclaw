from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .llm import SummaryLLM
from .qdrant_store import QdrantMemoryStore
from .repository import MemoryRepository


@dataclass(frozen=True)
class SummaryRequest:
    user_id: str
    session_id: str


class SummaryWorker:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        qdrant_store: QdrantMemoryStore,
        summary_llm: SummaryLLM,
        interval_seconds: float,
        max_batch_tokens: int,
        recent_history_max_messages: int,
        recent_history_token_limit: int,
    ) -> None:
        self.repository = repository
        self.qdrant_store = qdrant_store
        self.summary_llm = summary_llm
        self.interval_seconds = interval_seconds
        self.max_batch_tokens = max_batch_tokens
        self.recent_history_max_messages = recent_history_max_messages
        self.recent_history_token_limit = recent_history_token_limit
        self._queue: asyncio.Queue[SummaryRequest] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="memory-summary-worker")

    def wake(self, *, user_id: str, session_id: str) -> None:
        self._queue.put_nowait(
            SummaryRequest(
                user_id=user_id,
                session_id=session_id,
            )
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                request = await asyncio.wait_for(self._queue.get(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
            await self.process_once(request)
            self._queue.task_done()

    async def process_once(self, request: SummaryRequest) -> None:
        while True:
            # 一次 wake 可能会遇到很长的旧 history；
            # 因此这里循环总结多批，直到只剩最近窗口需要保留。
            history_rows = await self.repository.get_summarization_batch(
                user_id=request.user_id,
                session_id=request.session_id,
                recent_history_max_messages=self.recent_history_max_messages,
                recent_history_token_limit=self.recent_history_token_limit,
                max_batch_tokens=self.max_batch_tokens,
            )
            if not history_rows:
                return

            try:
                await self._summarize_group(history_rows)
            except Exception as error:
                print(f"Memory summary failed: {error}")
                return

    async def _summarize_group(self, history_rows: list[Any]) -> None:
        messages = [
            {"role": row.role, "content": row.content}
            for row in history_rows
            if row.role in {"user", "assistant", "system"}
        ]
        summary = await asyncio.to_thread(self.summary_llm.summarize, messages)
        if not summary:
            return

        memory = await self.repository.add_summary(
            user_id=history_rows[0].user_id,
            session_id=history_rows[0].session_id,
            summary=summary,
            source_history_ids=[row.id for row in history_rows],
            metadata={
                "source": "summary_worker",
                "token_count": self.summary_llm.count_text_tokens(summary),
            },
        )
        await asyncio.to_thread(self.qdrant_store.upsert_memory, [memory])
        await self.repository.mark_memory_indexed([memory.id])
