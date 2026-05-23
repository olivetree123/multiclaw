from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


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

    def print_status(self, message: str) -> None:
        self.console.print(f"[yellow]{message}[/yellow]")

    def print_error(self, title: str, error: Exception) -> None:
        self.console.print(f"[red]{title}:[/red] {error}")

    def print_session(self, *, session_id: str, workspace: object) -> None:
        self.console.print(f"[bold]Active session_id:[/bold] {session_id}")
        self.console.print(f"[bold]Active workspace:[/bold] {workspace}")
