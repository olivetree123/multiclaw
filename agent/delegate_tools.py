from __future__ import annotations

from typing import Any

from .specs import AgentSpec


def build_delegate_tool_schemas(agents: tuple[AgentSpec, ...]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"delegate_to_{spec.name}",
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "详细的任务描述，包含 Agent 所需的全部上下文。",
                        },
                    },
                    "required": ["task"],
                },
            },
        }
        for spec in agents
    ]


def parse_delegate_tool_name(tool_name: str) -> str | None:
    prefix = "delegate_to_"
    if not tool_name.startswith(prefix):
        return None
    return tool_name[len(prefix):]
