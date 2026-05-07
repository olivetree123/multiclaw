from __future__ import annotations

from pathlib import Path

from memory import MemoryApp
from skills import format_skill_summaries, load_skill_summaries

from .console import AgentConsole


BASE_SYSTEM_PROMPT = "# Assistant Instructions\n\nYou are a helpful assistant that can answer questions and help with tasks."


class PromptBuilder:
    def __init__(self, *, memory_app: MemoryApp, console: AgentConsole) -> None:
        self.memory_app = memory_app
        self.console = console
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

        return f"{current_system_prompt}\n\n## Relevant Long-Term Memory\n{memory_context}"

    def _build_base_prompt(self) -> str:
        skill_prompt = format_skill_summaries(load_skill_summaries())
        if not skill_prompt:
            return BASE_SYSTEM_PROMPT
        return f"{BASE_SYSTEM_PROMPT}\n\n## Available Skills\n{skill_prompt}"

    def _with_workspace_prompt(self, workspace: Path | None) -> str:
        if workspace is None:
            return (
                f"{self.system_prompt}\n\n## Workspace\n"
                "No workspace was provided. File and directory operations are disabled. "
                "Do not claim to read, write, list, create, or delete files."
            )

        return (
            f"{self.system_prompt}\n\n## Workspace\n"
            f"Workspace: {workspace}\n"
            "File and directory operations are allowed only inside this workspace."
        )
