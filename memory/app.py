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
    """Memory 子系统的高层门面。

    Repository 负责 PostgreSQL 读写，QdrantMemoryStore 负责向量索引，
    SummaryWorker 负责后台压缩 history。外部调用方尽量只通过 MemoryApp
    进入这些能力，避免在 agent 主流程里散落数据库和索引细节。
    """

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
        """初始化数据库、Qdrant collection，并按需启动 summary worker。"""
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
        """写入一批对话消息，并同步写入 Qdrant。

        当前 agent 主循环会按 user/assistant/tool 消息逐条调用本方法。
        逐条落库的好处是：进程在一轮对话中途崩溃时，最多只丢失正在生成的那条消息，
        已经产生的消息会保留在 PostgreSQL 中，重启后可以基于最后一条 history 做恢复判断。
        """
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
        """修复进程异常中断留下的不完整对话轮次。

        典型中断场景：
        1. 用户消息已经写入 PostgreSQL；
        2. 程序随后在 LLM 调用、工具执行或 assistant 回复保存前崩溃；
        3. 重启后数据库里该 session 的最后一条 history 是 role=user。

        这里采用最保守的恢复方式：只往 history 表补一条 assistant 说明消息。
        不重新调用 LLM，也不重新执行工具，因为上一轮请求可能包含文件写入或 shell
        命令等外部副作用，自动重放可能造成重复修改。

        注意这里故意不调用 add_messages()：
        - 恢复消息只是修补主存储里的对话结构；
        - 不需要立刻写 Qdrant；
        - 不需要触发 summary worker；
        - 后续启动补索引 reindex_unindexed() 会统一处理 indexed_at 为空的记录。
        """
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
        """补齐 PostgreSQL 已写入但尚未写入 Qdrant 的记录。

        add_messages() 的顺序是：先写 PostgreSQL，再 upsert Qdrant，最后标记 indexed_at。
        如果进程在中间任意一步崩溃，就可能出现 indexed_at 为空的 history/memory。
        Qdrant upsert 是幂等的，所以启动时按批重新 upsert 这些记录是安全的。
        """
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
            # 只有 upsert 成功后才标记 indexed_at，避免索引失败但数据库误认为已索引。
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

            # Memory.summary 每个 session 通常只有一条聚合长期记忆；
            # upsert_memory 使用稳定 point id，因此重复补索引会覆盖同一个 Qdrant 点。
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
        """确保 session 存在，并返回数据库中的 session id 与 workspace。"""
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
        """在 Qdrant 中按 user_id + session_id 检索相关 history/summary。"""
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
        """构建要放入 system prompt 的 memory 上下文。

        上下文由三部分组成：
        - 当前 session 的长期 summary；
        - 还没有被 summary worker 压缩的原始 history；
        - Qdrant 搜索命中的相关记录。
        """
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
        """给将要写入 history 的消息补 token_count。"""
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
        """关闭后台 worker、Qdrant client 和数据库连接。"""
        await self.summary_worker.stop()
        await asyncio.to_thread(self.qdrant_store.close)
        await self.repository.close()
