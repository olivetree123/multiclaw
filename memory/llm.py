from __future__ import annotations

from litellm import completion

from .tokens import estimate_tokens


SUMMARY_SYSTEM_PROMPT = """You summarize conversation history into long-term memory.
Only keep stable facts that are likely to be useful later:
- user preferences
- project facts and decisions
- long-running goals
- unresolved tasks
- durable constraints

Ignore one-off chatter, tool traces, and temporary implementation details.
Write concise Chinese summaries unless the source content requires another language."""


class SummaryLLM:
    def __init__(self, *, model: str, api_key: str | None, base_url: str | None) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def summarize(self, messages: list[dict[str, str]]) -> str:
        conversation = "\n".join(
            f"{message['role']}: {message['content']}" for message in messages if message.get("content")
        )
        if not conversation.strip():
            return ""

        response = completion(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": conversation},
            ],
        )
        content = response["choices"][0]["message"].get("content") or ""
        return content.strip()

    def count_text_tokens(self, text: str) -> int:
        return estimate_tokens(text)
