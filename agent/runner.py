from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from litellm import completion

from memory import MemoryApp
from tools import call_tool, get_tool_schemas

from .console import AgentConsole
from .message_history import (
    history_row_to_message,
    sanitize_messages_for_llm,
    synthetic_tool_messages,
)
from .prompt import PromptBuilder

ToolHandler = Callable[[str, str], Awaitable[Any]]


class AgentRunner:
    """负责一次 agent 进程的完整生命周期。

    main.py 只负责解析 CLI 参数；真正的运行状态都收敛在这里：
    - 根据 session_id/workspace 恢复或创建会话
    - 启动时修复异常中断留下的不完整 history
    - 将 user/assistant/tool 消息增量写入 memory
    - 调用 LLM、执行工具，并维护当前进程内的短期 history
    """

    def __init__(
        self,
        *,
        workspace: Path | None,
        session_id: str | None,
        role_prompt: str | None = None,
        label: str | None = None,
        console: AgentConsole | None = None,
        memory_app: MemoryApp | None = None,
        start_worker: bool = True,
        owns_memory_app: bool | None = None,
    ) -> None:
        self.requested_workspace = workspace
        self.requested_session_id = session_id
        self.role_prompt = role_prompt
        self.label = label
        self.console = console or AgentConsole()
        self.memory_app = memory_app or MemoryApp(start_worker=start_worker)
        self._owns_memory_app = (
            owns_memory_app if owns_memory_app is not None else memory_app is None
        )
        self.history: list[dict[str, Any]] = []
        self._history_restored = False
        self._session_id: str | None = None
        self._workspace: Path | None = None
        self._initialized = False

    async def run(
        self,
        *,
        extra_tool_schemas: list[dict[str, Any]] | None = None,
        tool_handler: ToolHandler | None = None,
    ) -> None:
        """初始化 memory 组件，准备会话，然后进入交互循环。"""
        try:
            session_id, workspace = await self.initialize()
            prompt_builder = PromptBuilder(
                memory_app=self.memory_app,
                console=self.console,
                role_prompt=self.role_prompt,
            )
            await self._chat_loop(
                session_id=session_id,
                workspace=workspace,
                prompt_builder=prompt_builder,
                extra_tool_schemas=extra_tool_schemas,
                tool_handler=tool_handler,
            )
        finally:
            await self.close()

    async def close(self) -> None:
        if not self._initialized:
            self.history = []
            self._history_restored = False
            return
        if self._owns_memory_app:
            await self.memory_app.close()
        self.history = []
        self._history_restored = False
        self._initialized = False

    async def initialize(self) -> tuple[str, Path | None]:
        """初始化 memory 并准备会话，可重复调用。"""
        if self._initialized and self._session_id is not None:
            return self._session_id, self._workspace

        await self.memory_app.initialize()
        session_id, workspace = await self._prepare_session()
        self._session_id = session_id
        self._workspace = workspace
        self._initialized = True
        return session_id, workspace

    async def run_task(
        self,
        user_message: str,
        *,
        extra_tool_schemas: list[dict[str, Any]] | None = None,
        tool_handler: ToolHandler | None = None,
    ) -> str:
        """执行单个任务并返回 assistant 最终回复，不读取 stdin。"""
        session_id, workspace = await self.initialize()
        prompt_builder = PromptBuilder(
            memory_app=self.memory_app,
            console=self.console,
            role_prompt=self.role_prompt,
        )
        return await self._process_user_message(
            session_id=session_id,
            workspace=workspace,
            prompt_builder=prompt_builder,
            user_message=user_message,
            extra_tool_schemas=extra_tool_schemas,
            tool_handler=tool_handler,
            print_user=False,
        )

    async def _prepare_session(self) -> tuple[str, Path | None]:
        """准备会话状态，并执行启动恢复流程。"""
        session = await self.memory_app.ensure_session(
            user_id=self.memory_app.config.user_id,
            session_id=self.requested_session_id,
            workspace=str(self.requested_workspace) if self.requested_workspace else None,
        )
        session_id = session.id
        workspace = Path(session.workspace).expanduser().resolve() if session.workspace else None

        self.console.print_session(session_id=session_id, workspace=workspace)

        await self._restore_history_if_needed(session_id=session_id)

        repaired = await self.memory_app.repair_incomplete_turn(
            user_id=self.memory_app.config.user_id,
            session_id=session_id,
        )
        if repaired:
            await self._restore_history_if_needed(session_id=session_id, force=True)
            self.console.print_status("Recovered an incomplete previous turn.")

        try:
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
        extra_tool_schemas: list[dict[str, Any]] | None = None,
        tool_handler: ToolHandler | None = None,
    ) -> None:
        """读取用户输入，调用模型，并处理可能出现的工具调用。"""
        while True:
            input_text = await asyncio.to_thread(self.console.input_message)
            if input_text == "exit":
                break

            await self._process_user_message(
                session_id=session_id,
                workspace=workspace,
                prompt_builder=prompt_builder,
                user_message=input_text,
                extra_tool_schemas=extra_tool_schemas,
                tool_handler=tool_handler,
            )

    async def _process_user_message(
        self,
        *,
        session_id: str,
        workspace: Path | None,
        prompt_builder: PromptBuilder,
        user_message: str,
        extra_tool_schemas: list[dict[str, Any]] | None = None,
        tool_handler: ToolHandler | None = None,
        print_user: bool = True,
    ) -> str:
        if print_user:
            self.console.print_user_message(user_message)

        user_entry = {"role": "user", "content": user_message}
        await self._append_history_message(session_id=session_id, message=user_entry)
        current_system_prompt = await prompt_builder.build(
            query=user_message,
            session_id=session_id,
            workspace=workspace,
        )
        return await self._run_agent_turn(
            session_id=session_id,
            workspace=workspace,
            system_prompt=current_system_prompt,
            extra_tool_schemas=extra_tool_schemas,
            tool_handler=tool_handler,
        )

    async def _run_agent_turn(
        self,
        *,
        session_id: str,
        workspace: Path | None,
        system_prompt: str,
        extra_tool_schemas: list[dict[str, Any]] | None = None,
        tool_handler: ToolHandler | None = None,
    ) -> str:
        file_tools_enabled = workspace is not None
        tool_schemas = [
            *get_tool_schemas(file_tools_enabled=file_tools_enabled),
            *(extra_tool_schemas or []),
        ]
        workspace_value = str(workspace) if workspace else None

        while True:
            llm_messages = sanitize_messages_for_llm(self.history)
            response = await asyncio.to_thread(
                completion,
                model=os.getenv("LLM_MODEL"),
                base_url=os.getenv("LLM_BASE_URL"),
                api_key=os.getenv("LLM_API_KEY"),
                tools=tool_schemas,
                tool_choice="auto",
                messages=[{
                    "role": "system",
                    "content": system_prompt,
                }] + llm_messages,
            )

            assistant_message = _message_to_dict(response["choices"][0]["message"])
            self.console.print_thinking(_extract_thinking(assistant_message), label=self.label)
            await self._append_history_message(session_id=session_id, message=assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content = assistant_message.get("content", "")
                self.console.print_assistant_message(content, label=self.label)
                return content

            for tool_call in tool_calls:
                tool_call = _tool_call_to_dict(tool_call)
                function_call = tool_call["function"]
                tool_name = function_call["name"]
                tool_arguments = function_call.get("arguments", "{}")
                self.console.print_tool_call(tool_name, tool_arguments, label=self.label)

                try:
                    if tool_handler is not None:
                        tool_result = await tool_handler(tool_name, tool_arguments)
                    else:
                        tool_result = call_tool(
                            tool_name,
                            tool_arguments,
                            file_tools_enabled=file_tools_enabled,
                            workspace=workspace_value,
                        )
                    tool_content = _tool_result_to_content(tool_result)
                except Exception as error:
                    tool_content = f"Tool error: {error}"

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_name,
                    "content": tool_content,
                }
                self.console.print_tool_result(
                    tool_name,
                    tool_message["content"],
                    label=self.label,
                )
                await self._append_history_message(session_id=session_id, message=tool_message)

            self.history = sanitize_messages_for_llm(self.history)

    async def _restore_history_if_needed(
        self,
        *,
        session_id: str,
        force: bool = False,
    ) -> None:
        if self._history_restored and not force:
            return

        rows = await self.memory_app.repository.get_pending_history(
            user_id=self.memory_app.config.user_id,
            session_id=session_id,
        )
        loaded = [history_row_to_message(row) for row in rows]
        repaired = sanitize_messages_for_llm(loaded)
        placeholders = synthetic_tool_messages(loaded, repaired)
        if placeholders:
            await self.memory_app.add_messages(
                session_id=session_id,
                user_id=self.memory_app.config.user_id,
                messages=placeholders,
            )
        self.history = repaired
        self._history_restored = True

    async def _append_history_message(self, *, session_id: str, message: dict[str, Any]) -> None:
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
    if isinstance(tool_result, str):
        return tool_result
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
