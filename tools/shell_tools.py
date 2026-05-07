from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

SHELL_WORKSPACE: Path | None = None
DEFAULT_ALLOWED_COMMANDS = {"curl", "curl.exe", "summarize", "summarize.exe"}
CURL_BLOCKED_ARGS = {"-o", "--output", "-O", "--remote-name", "-K", "--config"}
console = Console()


def configure_shell_workspace(workspace: str | None) -> None:
    global SHELL_WORKSPACE
    SHELL_WORKSPACE = Path(workspace).expanduser().resolve() if workspace else None


def execute_shell_command(command: str, timeout_seconds: int = 30) -> dict[str, Any]:
    args = _parse_command(command)
    executable = Path(args[0]).name.lower()
    if executable not in _allowed_commands():
        if not _confirm_unlisted_command(command):
            return {
                "command": executable,
                "approved": False,
                "exit_code": None,
                "stdout": "",
                "stderr": "Command was not approved by the user.",
            }

    _validate_command_args(executable, args[1:])

    completed = subprocess.run(
        args,
        cwd=str(SHELL_WORKSPACE) if SHELL_WORKSPACE else None,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "command": executable,
        "approved": True,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _parse_command(command: str) -> list[str]:
    args = shlex.split(command, posix=os.name != "nt")
    if not args:
        raise ValueError("Command cannot be empty.")
    return args


def _allowed_commands() -> set[str]:
    configured = os.getenv("SHELL_TOOL_ALLOWED_COMMANDS")
    if not configured:
        return DEFAULT_ALLOWED_COMMANDS
    return {command.strip().lower() for command in configured.split(",") if command.strip()}


def _confirm_unlisted_command(command: str) -> bool:
    console.print(
        Panel(
            command,
            title="Shell Command Requires Approval",
            subtitle="Command is outside the allowlist",
            border_style="yellow",
        ))
    return Confirm.ask("Run this command?", default=False)


def _validate_command_args(executable: str, args: list[str]) -> None:
    if executable.startswith("curl"):
        _validate_curl_args(args)
    elif executable.startswith("summarize"):
        _validate_summarize_args(args)


def _validate_curl_args(args: list[str]) -> None:
    for arg in args:
        if arg in CURL_BLOCKED_ARGS or arg.startswith("@") or arg.lower().startswith("file:"):
            raise PermissionError(f"curl argument is not allowed: {arg}")


def _validate_summarize_args(args: list[str]) -> None:
    if SHELL_WORKSPACE is None:
        raise PermissionError("summarize is disabled because no workspace was provided.")

    for arg in args:
        if arg.startswith("-") or _is_url(arg):
            continue
        _resolve_workspace_path(arg)


def _resolve_workspace_path(path: str) -> Path:
    raw_path = Path(path).expanduser()
    target = raw_path if raw_path.is_absolute() else SHELL_WORKSPACE / raw_path
    resolved = target.resolve()
    if resolved != SHELL_WORKSPACE and SHELL_WORKSPACE not in resolved.parents:
        raise PermissionError(f"Path is outside workspace: {resolved}")
    return resolved


def _is_url(value: str) -> bool:
    normalized = value.lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


SHELL_TOOL_FUNCTIONS = {
    "execute_shell_command": execute_shell_command,
}

SHELL_TOOL_SCHEMAS = [{
    "type": "function",
    "function": {
        "name":
        "execute_shell_command",
        "description": ("Execute a shell command with safety checks. "
                        "Commands outside the allowlist require user confirmation. "
                        "The command is not run through a system shell."),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type":
                    "string",
                    "description":
                    "The command to execute, for example: curl wttr.in/Beijing?format=3",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
    },
}]


def call_shell_tool(name: str, arguments: str | dict[str, Any]) -> Any:
    function = SHELL_TOOL_FUNCTIONS.get(name)
    if function is None:
        raise ValueError(f"Unknown shell tool: {name}")

    parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    return function(**parsed_arguments)
