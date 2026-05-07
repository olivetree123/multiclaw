from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .llm import SummaryLLM
from .qdrant_store import QdrantMemoryStore
from .repository import MemoryRepository


@dataclass(frozen=True)
class SummaryRequest:
    """一次 summary 任务请求。

    这里只携带 user_id/session_id，不携带具体 history id。
    原因是：wake() 只是一个“提醒 worker 该 session 可能需要压缩”的信号；
    真正要总结哪些 history，要在 worker 实际处理时重新从数据库计算。
    这样可以避免 wake 到执行之间又新增消息时，使用过期的批次信息。
    """

    user_id: str
    session_id: str


class SummaryWorker:
    """后台 history 总结 worker。

    触发链路：
    1. AgentRunner 每产生一条 user/assistant/tool 消息，就调用 MemoryApp.add_messages()；
    2. MemoryApp.add_messages() 写入 PostgreSQL 和 Qdrant 后，会调用
       MemoryApp._wake_summary_worker_if_needed()；
    3. _wake_summary_worker_if_needed() 计算：
       context_tokens = summary_tokens + pending_history_tokens
       其中 summary_tokens 来自 Memory.extra_metadata["token_count"]，
       pending_history_tokens 来自所有 is_summarized=False 的 History.token_count；
    4. 当 context_tokens > model_max_context_tokens * summary_trigger_ratio
       时，MemoryApp 调用 SummaryWorker.wake(user_id, session_id)；
    5. worker 收到 wake 后，不直接总结所有未总结 history，而是交给 repository
       按“保留最近窗口 + 批次 token 上限”的规则计算本次要处理的旧 history。

    总结策略：
    - 最新一段 history 保持原文，不进入 summary，避免丢失最近上下文细节；
    - 最近窗口最多保留 recent_history_max_messages 条；
    - 如果最近窗口 token 超过 recent_history_token_limit，则从最新消息开始向前保留，
      直到 token 不超过限制；
    - 最近窗口之前的旧 history 才能被总结；
    - 单批总结最多处理 max_batch_tokens 左右的 history token，旧 history 很长时会分多批。
    """

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
        # Queue 中可能出现同一个 session 的多次 wake。处理时会重新计算待总结批次，
        # 所以重复 wake 通常只是多做一次空检查，不会重复总结已经标记过的 history。
        self._queue: asyncio.Queue[SummaryRequest] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """启动后台 asyncio task。"""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="memory-summary-worker")

    def wake(self, *, user_id: str, session_id: str) -> None:
        """通知 worker：某个 session 的上下文长度可能需要压缩。

        wake() 本身不做 token 计算，也不选择 history 批次。
        token 阈值判断在 MemoryApp._wake_summary_worker_if_needed() 中完成；
        批次选择在 process_once() 调用 repository.get_summarization_batch() 时完成。
        """
        self._queue.put_nowait(
            SummaryRequest(
                user_id=user_id,
                session_id=session_id,
            )
        )

    async def stop(self) -> None:
        """停止后台任务，通常在 MemoryApp.close() 时调用。"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        """worker 主循环。

        这里使用 wait_for + timeout，而不是永久阻塞在 queue.get()：
        - 当前实现 timeout 后只是继续等待；
        - 保留 interval_seconds 是为了后续如果要加周期性扫描，可以直接扩展。
        """
        while True:
            try:
                request = await asyncio.wait_for(self._queue.get(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
            await self.process_once(request)
            self._queue.task_done()

    async def process_once(self, request: SummaryRequest) -> None:
        """处理某个 session 的一次 wake 请求。

        一次 wake 不一定只生成一个 summary。比如 pending history 很长时，
        repository.get_summarization_batch() 会按 max_batch_tokens 返回一批旧 history；
        这一批总结完成并标记 is_summarized=True 后，下一轮循环会重新计算是否还有
        最近窗口之外的旧 history 可总结，直到只剩需要保留的最近窗口为止。
        """
        while True:
            # 一次 wake 可能会遇到很长的旧 history；
            # 因此这里循环总结多批，直到只剩最近窗口需要保留。
            # get_summarization_batch() 的核心计算在 repository 中：
            # 先取所有 is_summarized=False 的 history，再按 recent window 规则排除最新消息，
            # 最后从旧消息中按 max_batch_tokens 取出本批。
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
                # 单批失败时停止本次 wake，避免反复失败刷屏。
                # 后续新消息再次触发 wake 时，还会重新尝试处理这些未总结 history。
                print(f"Memory summary failed: {error}")
                return

    async def _summarize_group(self, history_rows: list[Any]) -> None:
        """把一批旧 history 压缩成长期记忆 summary。"""
        # 只把对话角色交给 summary LLM。tool message 常常是 JSON 结果或大段输出，
        # 直接进入总结容易引入噪声；工具结果仍保留在原始 history/Qdrant 中可被检索。
        messages = [
            {"role": row.role, "content": row.content}
            for row in history_rows
            if row.role in {"user", "assistant", "system"}
        ]
        # summary_llm.summarize 是同步 LLM 调用，用 to_thread 避免阻塞事件循环。
        summary = await asyncio.to_thread(self.summary_llm.summarize, messages)
        if not summary:
            return

        # add_summary 会把 summary 追加到当前 session 的 Memory 记录，并把本批 history
        # 标记为 is_summarized=True。metadata.token_count 后续用于计算上下文压力。
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
        # Memory 表是主存储，Qdrant 是派生索引；这里 upsert 成功后再标记 indexed_at。
        await asyncio.to_thread(self.qdrant_store.upsert_memory, [memory])
        await self.repository.mark_memory_indexed([memory.id])
