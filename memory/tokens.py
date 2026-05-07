from __future__ import annotations

from litellm import token_counter


def count_message_tokens(message: dict, model: str) -> int:
    try:
        return token_counter(model=model, messages=[message])
    except Exception:
        # litellm 可能不认识某些模型名；失败时用粗略估算保证触发逻辑仍可工作。
        return estimate_tokens(str(message.get("content") or ""))


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)
