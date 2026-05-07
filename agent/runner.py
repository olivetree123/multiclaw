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
    def __init__(self, *, workspace: Path | None, session_id: str | None) -> None:
        self.requested_workspace = workspace
        self.requested_session_id = session_id
        self.console = AgentConsole()
        self.memory_app = MemoryApp()
        self.history: list[dict[str, Any]] = []

    async def run(self) -> None:
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

        repaired = await self.memory_app.repair_incomplete_turn(
            user_id=self.memory_app.config.user_id,
            session_id=session_id,
        )
        if repaired:
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
    ) -> None:
        file_tools_enabled = workspace is not None
        tool_schemas = get_tool_schemas(file_tools_enabled=file_tools_enabled)

        while True:
            input_text = await asyncio.to_thread(self.console.input_message)
            if input_text == "exit":
                break

            self.console.print_user_message(input_text)
            user_message = {"role": "user", "content": input_text}
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
