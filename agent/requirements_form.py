from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import Any

from litellm import completion

ALLOWED_FIELD_TYPES = frozenset({"radio", "checkbox", "select", "text", "textarea"})

FORM_CONVERSION_SYSTEM_PROMPT = """\
你是一个表单结构转换器。将产品经理的需求澄清文本转换为 JSON 表单 schema。

只输出合法 JSON，不要 markdown 代码块，不要额外说明。

输出格式：
{
  "id": "<form_id>",
  "title": "需求澄清",
  "status": "needs_clarification",
  "fields": [
    {
      "id": "field_snake_case",
      "label": "显示标签",
      "type": "radio|checkbox|select|text|textarea",
      "required": true,
      "options": [{"value": "value_id", "label": "显示文本"}],
      "placeholder": "可选占位符"
    }
  ]
}

规则：
- 从原文提取所有澄清问题，每个问题对应一个 field
- 单选用 radio，多选用 checkbox，下拉用 select，短答用 text，长答用 textarea
- field.id 使用英文 snake_case，且在同一表单内唯一
- radio/checkbox/select 必须提供 options；text/textarea 不要 options
- 根对象 id 必须使用用户提供的 form_id
- status 固定为 "needs_clarification"
"""


def needs_clarification(agent_response: str) -> bool:
    normalized = agent_response.lower()
    return (
        "<!-- status: needs_clarification -->" in normalized
        or "status: needs_clarification" in normalized
    )


async def generate_clarification_form(
    content: str,
    *,
    form_id: str | None = None,
) -> dict[str, Any]:
    """调用大模型，将澄清文本转为前端可渲染的表单 JSON。"""
    resolved_form_id = form_id or f"req-clarify-{uuid.uuid4().hex[:8]}"
    response = await asyncio.to_thread(
        completion,
        model=os.getenv("LLM_MODEL"),
        base_url=os.getenv("LLM_BASE_URL"),
        api_key=os.getenv("LLM_API_KEY"),
        messages=[
            {"role": "system", "content": FORM_CONVERSION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"form_id: {resolved_form_id}\n\n"
                    f"需求澄清原文：\n{content}"
                ),
            },
        ],
    )
    raw_content = response["choices"][0]["message"]["content"]
    form = _parse_form_json(raw_content)
    return validate_clarification_form(form, expected_form_id=resolved_form_id)


def validate_clarification_form(
    form: dict[str, Any],
    *,
    expected_form_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(form, dict):
        raise ValueError("表单必须是 JSON 对象")

    form_id = form.get("id")
    if not isinstance(form_id, str) or not form_id.strip():
        raise ValueError("表单缺少 id")
    if expected_form_id is not None:
        form["id"] = expected_form_id

    title = form.get("title")
    if not isinstance(title, str) or not title.strip():
        form["title"] = "需求澄清"

    status = form.get("status")
    if status != "needs_clarification":
        form["status"] = "needs_clarification"

    fields = form.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("表单 fields 不能为空")

    seen_ids: set[str] = set()
    normalized_fields: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("field 必须是对象")
        field_id = field.get("id")
        if not isinstance(field_id, str) or not field_id.strip():
            raise ValueError("field 缺少 id")
        if field_id in seen_ids:
            raise ValueError(f"field id 重复: {field_id}")
        seen_ids.add(field_id)

        label = field.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"field {field_id} 缺少 label")

        field_type = field.get("type")
        if field_type not in ALLOWED_FIELD_TYPES:
            raise ValueError(f"field {field_id} type 无效: {field_type}")

        required = field.get("required", False)
        if not isinstance(required, bool):
            required = bool(required)

        normalized: dict[str, Any] = {
            "id": field_id,
            "label": label,
            "type": field_type,
            "required": required,
        }

        if field_type in {"radio", "checkbox", "select"}:
            options = field.get("options")
            if not isinstance(options, list) or not options:
                raise ValueError(f"field {field_id} 缺少 options")
            normalized_options: list[dict[str, str]] = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError(f"field {field_id} options 格式无效")
                value = option.get("value")
                option_label = option.get("label")
                if not isinstance(value, str) or not isinstance(option_label, str):
                    raise ValueError(f"field {field_id} options 缺少 value/label")
                normalized_options.append({"value": value, "label": option_label})
            normalized["options"] = normalized_options

        placeholder = field.get("placeholder")
        if isinstance(placeholder, str) and placeholder.strip():
            normalized["placeholder"] = placeholder

        normalized_fields.append(normalized)

    return {
        "id": form["id"],
        "title": form["title"],
        "status": "needs_clarification",
        "fields": normalized_fields,
    }


def _parse_form_json(raw_content: str) -> dict[str, Any]:
    text = raw_content.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("表单 JSON 必须是对象")
    return parsed
