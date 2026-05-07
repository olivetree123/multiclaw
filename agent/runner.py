from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from litellm import completion

from memory import MemoryApp
from tools import call_tool, configure_file_workspace, configure_shell_workspace, get_tool_schemas

from .console import AgentConsole
from .prompt import PromptBuilder


class AgentRunner:
    """负责一次 agent 进程的完整生命周期。

    main.py 只负责解析 CLI 参数；真正的运行状态都收敛在这里：
    - 根据 session_id/workspace 恢复或创建会话
    - 启动时修复异常中断留下的不完整 history
    - 将 user/assistant/tool 消息增量写入 memory
    - 调用 LLM、执行工具，并维护当前进程内的短期 history
    """

    def __init__(self, *, workspace: Path | None, session_id: str | None) -> None:
        self.requested_workspace = workspace
        self.requested_session_id = session_id
        self.console = AgentConsole()
        self.memory_app = MemoryApp()
        # 这里只保存当前进程内的上下文，作为本轮 LLM 请求的 messages。
        # 持久化上下文由 MemoryApp 写入数据库；重启后不会从这个列表恢复。
        self.history: list[dict[str, Any]] = []

    async def run(self) -> None:
        """初始化 memory 组件，准备会话，然后进入交互循环。"""
        await self.memory_app.initialize()
        try:
            session_id, workspace = await self._prepare_session()
            prompt_builder = PromptBuilder(memory_app=self.memory_app, console=self.console)
            await self._chat_loop(
                session_id=session_id,
                workspace=workspace,
                prompt_builder=prompt_builder,
            )
        finally:
            await self.memory_app.close()

    async def _prepare_session(self) -> tuple[str, Path | None]:
        """准备会话状态，并执行启动恢复流程。

        中断恢复放在进入聊天循环之前做，原因是：
        1. 先修复历史记录，后续 build_context 才能看到一致的上下文；
        2. 先补 Qdrant 索引，memory search 才尽量基于完整索引；
        3. 不在恢复阶段重新调用 LLM 或工具，避免重复执行上一轮用户请求。
        """
        session = await self.memory_app.ensure_session(
            user_id=self.memory_app.config.user_id,
            session_id=self.requested_session_id,
            workspace=str(self.requested_workspace) if self.requested_workspace else None,
        )
        session_id = session.id
        workspace = Path(session.workspace).expanduser().resolve() if session.workspace else None

        self.console.print_session(session_id=session_id, workspace=workspace)
        configure_file_workspace(str(workspace) if workspace else None)
        configure_shell_workspace(str(workspace) if workspace else None)

        # 如果上次进程在用户消息写入后、assistant 回复写入前崩溃，
        # 数据库最后一条 history 会停在 user。这里只补一条 assistant 恢复消息，
        # 不重新提交给模型，也不重新执行任何工具，避免重复副作用。
        repaired = await self.memory_app.repair_incomplete_turn(
            user_id=self.memory_app.config.user_id,
            session_id=session_id,
        )
        if repaired:
            self.console.print_status("Recovered an incomplete previous turn.")

        try:
            # PostgreSQL 是主存储，Qdrant 是可重建的派生索引。
            # 启动时补齐 indexed_at 为空的记录，可以修复“DB 已写入但索引未完成”的中断状态。
            indexed_count = await self.memory_app.reindex_unindexed(
                user_id=self.memory_app.config.user_id,
                session_id=session_id,
            )
        except Exception as error:
            self.console.print_error("Memory reindex failed", error)
        else:
            if indexed_count:
                self.console.print_status(f"Reindexed {indexed_count} memory records.")

        return session_id, workspace

    async def _chat_loop(
        self,
        *,
        session_id: str,
        workspace: Path | None,
        prompt_builder: PromptBuilder,
    ) -> None:
        """读取用户输入，调用模型，并处理可能出现的工具调用。"""
        file_tools_enabled = workspace is not None
        tool_schemas = get_tool_schemas(file_tools_enabled=file_tools_enabled)

        while True:
            input_text = await asyncio.to_thread(self.console.input_message)
            if input_text == "exit":
                break

            self.console.print_user_message(input_text)
            user_message = {"role": "user", "content": input_text}
            # user 消息一产生就立刻落库。这样即使随后 LLM 调用或工具执行中断，
            # 下次启动也能检测到“最后一条是 user”，并补偿一条恢复说明。
            await self._append_history_message(session_id=session_id, message=user_message)
            current_system_prompt = await prompt_builder.build(
                query=input_text,
                session_id=session_id,
                workspace=workspace,
            )

            while True:
                response = await asyncio.to_thread(
                    completion,
                    model=os.getenv("LLM_MODEL"),
                    base_url=os.getenv("LLM_BASE_URL"),
                    api_key=os.getenv("LLM_API_KEY"),
                    tools=tool_schemas,
                    tool_choice="auto",
                    messages=[{
                        "role": "system",
                        "content": current_system_prompt,
                    }] + self.history,
                )

                assistant_message = _message_to_dict(response["choices"][0]["message"])
                self.console.print_thinking(_extract_thinking(assistant_message))
                # assistant 消息也增量落库，避免“模型已经回复但整轮尚未结束”时崩溃导致回复丢失。
                await self._append_history_message(session_id=session_id, message=assistant_message)

                tool_calls = assistant_message.get("tool_calls") or []
                if not tool_calls:
                    self.console.print_assistant_message(assistant_message.get("content", ""))
                    break

                for tool_call in tool_calls:
                    tool_call = _tool_call_to_dict(tool_call)
                    function_call = tool_call["function"]
                    tool_name = function_call["name"]
                    tool_arguments = function_call.get("arguments", "{}")
                    self.console.print_tool_call(tool_name, tool_arguments)
                    # 工具调用可能带来文件写入、shell 命令等外部副作用。
                    # 这里保留“执行后立即记录结果”的策略；如果执行过程中进程崩溃，
                    # history 中会留下 assistant 的 tool_call，但没有对应 tool result，
                    # 后续可以据此判断上一轮工具调用未完成。
                    tool_result = call_tool(
                        tool_name,
                        tool_arguments,
                        file_tools_enabled=file_tools_enabled,
                    )

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_name,
                        "content": _tool_result_to_content(tool_result),
                    }
                    await self._append_history_message(session_id=session_id, message=tool_message)

    async def _append_history_message(self, *, session_id: str, message: dict[str, Any]) -> None:
        """把消息同时追加到进程内 history 和持久化 memory。

        先追加内存 history，是为了保证当前进程后续 LLM 请求能看到这条消息。
        持久化失败时只打印错误，不中断主循环；这样对话仍可继续，但终端会提示
        当前消息没有成功保存到 memory。
        """
        self.history.append(message)
        try:
            await self.memory_app.add_messages(
                session_id=session_id,
                user_id=self.memory_app.config.user_id,
                messages=[message],
            )
        except Exception as error:
            self.console.print_error("Memory save failed", error)


def _message_to_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    return dict(message)


def _tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
    if hasattr(tool_call, "model_dump"):
        return tool_call.model_dump(exclude_none=True)
    return dict(tool_call)


def _tool_result_to_content(tool_result: Any) -> str:
    return json.dumps(tool_result, ensure_ascii=False)


def _extract_thinking(message: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    provider_fields = message.get("provider_specific_fields") or {}
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = provider_fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""
