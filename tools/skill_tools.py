from __future__ import annotations

import json
from typing import Any

from skills import load_skill


SKILL_TOOL_FUNCTIONS = {
    "load_skill": load_skill,
}

SKILL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Load the full SKILL.md content for a named skill before using it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name, for example summarize or weather.",
                    }
                },
                "required": ["name"],
            },
        },
    }
]


def call_skill_tool(name: str, arguments: str | dict[str, Any]) -> Any:
    function = SKILL_TOOL_FUNCTIONS.get(name)
    if function is None:
        raise ValueError(f"Unknown skill tool: {name}")

    parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    return function(**parsed_arguments)
