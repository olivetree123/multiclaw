from __future__ import annotations

from pathlib import Path

from memory import MemoryApp
from skills import format_skill_summaries, load_skill_summaries

from .console import AgentConsole


BASE_SYSTEM_PROMPT = "# 助手说明\n\n你是一个乐于助人的助手，可以回答问题并协助完成任务。"


class PromptBuilder:
    def __init__(
        self,
        *,
        memory_app: MemoryApp,
        console: AgentConsole,
        role_prompt: str | None = None,
    ) -> None:
        self.memory_app = memory_app
        self.console = console
        self.role_prompt = role_prompt
        self.system_prompt = self._build_base_prompt()

    async def build(self, *, query: str, session_id: str, workspace: Path | None) -> str:
        current_system_prompt = self._with_workspace_prompt(workspace)

        try:
            memory_context = await self.memory_app.build_context(
                query=query,
                user_id=self.memory_app.config.user_id,
                session_id=session_id,
            )
        except Exception as error:
            self.console.print_error("Memory search failed", error)
            return current_system_prompt

        if not memory_context:
            return current_system_prompt

        return f"{current_system_prompt}\n\n## 相关长期记忆\n{memory_context}"

    def _build_base_prompt(self) -> str:
        skill_prompt = format_skill_summaries(load_skill_summaries())
        if skill_prompt:
            base = f"{BASE_SYSTEM_PROMPT}\n\n## 可用技能\n{skill_prompt}"
        else:
            base = BASE_SYSTEM_PROMPT
        if self.role_prompt:
            return f"{base}\n\n## 角色\n{self.role_prompt}"
        return base

    def _with_workspace_prompt(self, workspace: Path | None) -> str:
        if workspace is None:
            return (
                f"{self.system_prompt}\n\n## 工作区\n"
                "未提供工作区。文件与目录操作已禁用。"
                "不要声称可以读取、写入、列出、创建或删除文件。"
            )

        return (
            f"{self.system_prompt}\n\n## 工作区\n"
            f"工作区：{workspace}\n"
            "文件与目录操作仅允许在此工作区内进行。"
        )
