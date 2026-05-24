from __future__ import annotations

import asyncio

from rich.console import Console
from rich.panel import Panel

from .stream_events import StreamEvent

class AgentConsole:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def input_message(self) -> str:
        return self.console.input("[bold cyan]Enter your message:[/bold cyan] ")

    def print_user_message(self, content: str, *, label: str | None = None) -> None:
        title = label or "User"
        self.console.print(Panel(content, title=title, border_style="cyan"))

    def print_thinking(self, content: str, *, label: str | None = None) -> None:
        if content:
            title = f"{label} Think" if label else "Think"
            self.console.print(Panel(content, title=title, border_style="yellow"))

    def print_assistant_message(self, content: str, *, label: str | None = None) -> None:
        title = label or "Assistant"
        self.console.print(Panel(content or "", title=title, border_style="green"))

    def print_tool_call(self, tool_name: str, tool_arguments: str, *, label: str | None = None) -> None:
        argument_preview = tool_arguments[:100]
        if len(tool_arguments) > 100:
            argument_preview = f"{argument_preview}..."
        title = f"{label} Tool Call" if label else "Tool Call"
        self.console.print(
            Panel(
                f"{tool_name}\n\n{argument_preview}",
                title=title,
                border_style="magenta",
            )
        )

    def print_tool_result(self, tool_name: str, result_preview: str, *, label: str | None = None) -> None:
        return

    def print_status(self, message: str) -> None:
        self.console.print(f"[yellow]{message}[/yellow]")

    def print_error(self, title: str, error: Exception) -> None:
        self.console.print(f"[red]{title}:[/red] {error}")

    def print_session(self, *, session_id: str, workspace: object) -> None:
        self.console.print(f"[bold]Active session_id:[/bold] {session_id}")
        self.console.print(f"[bold]Active workspace:[/bold] {workspace}")


class SilentAgentConsole(AgentConsole):
    """API 模式使用：不读写终端，静默丢弃输出。"""

    def input_message(self) -> str:
        raise NotImplementedError("SilentAgentConsole 不支持交互式输入")

    def print_user_message(self, content: str, *, label: str | None = None) -> None:
        return

    def print_thinking(self, content: str, *, label: str | None = None) -> None:
        return

    def print_assistant_message(self, content: str, *, label: str | None = None) -> None:
        return

    def print_tool_call(self, tool_name: str, tool_arguments: str, *, label: str | None = None) -> None:
        return

    def print_status(self, message: str) -> None:
        return

    def print_error(self, title: str, error: Exception) -> None:
        return

    def print_session(self, *, session_id: str, workspace: object) -> None:
        return


class StreamingAgentConsole(SilentAgentConsole):
    """SSE 模式：将 Agent 输出写入事件队列。"""

    def __init__(self, queue: asyncio.Queue[StreamEvent | None]) -> None:
        super().__init__()
        self._queue = queue

    def _emit(self, event: str, data: dict) -> None:
        self._queue.put_nowait(StreamEvent(event, data))

    def print_thinking(self, content: str, *, label: str | None = None) -> None:
        if content:
            self._emit("thinking", {"content": content, "agent": label})

    def print_assistant_message(self, content: str, *, label: str | None = None) -> None:
        self._emit("assistant", {"content": content or "", "agent": label})

    def print_tool_call(self, tool_name: str, tool_arguments: str, *, label: str | None = None) -> None:
        argument_preview = tool_arguments[:500]
        self._emit(
            "tool_call",
            {
                "tool_name": tool_name,
                "arguments": argument_preview,
                "agent": label,
            },
        )

    def print_tool_result(self, tool_name: str, result_preview: str, *, label: str | None = None) -> None:
        self._emit(
            "tool_result",
            {
                "tool_name": tool_name,
                "content": result_preview[:500],
                "agent": label,
            },
        )

    def print_status(self, message: str) -> None:
        self._emit("status", {"content": message})

    def print_error(self, title: str, error: Exception) -> None:
        self._emit("error", {"title": title, "detail": str(error)})
