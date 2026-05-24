from __future__ import annotations

from copy import deepcopy
from typing import Any

TOOL_INCOMPLETE_MESSAGE = "工具执行未完成。"


def history_row_to_message(row: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": row.role,
        "content": row.content if row.content else None,
    }
    metadata = row.extra_metadata or {}
    if "tool_calls" in metadata:
        message["tool_calls"] = metadata["tool_calls"]
    if row.role == "tool":
        if "tool_call_id" in metadata:
            message["tool_call_id"] = metadata["tool_call_id"]
        if "name" in metadata:
            message["name"] = metadata["name"]
    for key, value in metadata.items():
        if key in {"tool_calls", "tool_call_id", "name", "recovery", "token_count"}:
            continue
        message[key] = value
    return message


def sanitize_messages_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """确保每个带 tool_calls 的 assistant 消息后都有完整的 tool 回复。"""
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        tool_calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not tool_calls:
            result.append(message)
            index += 1
            continue

        expected_ids = _tool_call_ids(tool_calls)
        if not expected_ids:
            cleaned = deepcopy(message)
            cleaned.pop("tool_calls", None)
            result.append(cleaned)
            index += 1
            continue

        result.append(message)
        index += 1
        tool_messages: list[dict[str, Any]] = []
        while index < len(messages) and messages[index].get("role") == "tool":
            tool_messages.append(messages[index])
            index += 1

        responded_ids = {
            tool_message.get("tool_call_id")
            for tool_message in tool_messages
            if tool_message.get("tool_call_id")
        }
        result.extend(tool_messages)
        for tool_call_id in expected_ids:
            if tool_call_id not in responded_ids:
                result.append(_synthetic_tool_message(tool_call_id))
    return result


def synthetic_tool_messages(
    original: list[dict[str, Any]],
    repaired: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    original_tool_ids = {
        message.get("tool_call_id")
        for message in original
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    return [
        message
        for message in repaired
        if message.get("role") == "tool"
        and message.get("recovery")
        and message.get("tool_call_id") not in original_tool_ids
    ]


def _tool_call_ids(tool_calls: list[Any]) -> list[str]:
    ids: list[str] = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            tool_call_id = tool_call.get("id")
        else:
            tool_call_id = getattr(tool_call, "id", None)
        if tool_call_id:
            ids.append(str(tool_call_id))
    return ids


def _synthetic_tool_message(tool_call_id: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": TOOL_INCOMPLETE_MESSAGE,
        "recovery": True,
    }
