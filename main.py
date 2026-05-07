import os
import json
import uuid
import asyncio

from dotenv import load_dotenv
from litellm import completion

from memory import MemoryApp
from tools import FILE_TOOL_SCHEMAS, call_file_tool

load_dotenv()

system_prompt = "You are a helpful assistant that can answer questions and help with tasks."

history = []


def _message_to_dict(message):
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    return dict(message)


def _tool_call_to_dict(tool_call):
    if hasattr(tool_call, "model_dump"):
        return tool_call.model_dump(exclude_none=True)
    return dict(tool_call)


def _tool_result_to_content(tool_result):
    return json.dumps(tool_result, ensure_ascii=False)


async def _build_system_prompt(memory_app: MemoryApp, query: str, session_id: str) -> str:
    try:
        memory_context = await memory_app.build_context(
            query=query,
            user_id=memory_app.config.user_id,
            session_id=session_id,
        )
    except Exception as error:
        print(f"Memory search failed: {error}")
        return system_prompt

    if not memory_context:
        return system_prompt

    return f"{system_prompt}\n\nRelevant long-term memory:\n{memory_context}"


async def main():
    memory_app = MemoryApp()
    await memory_app.initialize()
    session_id = os.getenv("MEMORY_SESSION_ID", str(uuid.uuid4()))

    try:
        await _chat_loop(memory_app, session_id)
    finally:
        await memory_app.close()


async def _chat_loop(memory_app: MemoryApp, session_id: str):
    while True:
        input_text = await asyncio.to_thread(input, "Enter your message: ")
        if input_text == "exit":
            break

        user_message = {"role": "user", "content": input_text}
        history.append(user_message)
        turn_messages = [user_message]
        current_system_prompt = await _build_system_prompt(memory_app, input_text, session_id)

        while True:
            response = await asyncio.to_thread(
                completion,
                model=os.getenv("LLM_MODEL", "deepseek/deepseek-v4-pro"),
                base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
                api_key=os.getenv("LLM_API_KEY"),
                tools=FILE_TOOL_SCHEMAS,
                tool_choice="auto",
                messages=[{
                    "role": "system",
                    "content": current_system_prompt
                }] + history,
            )

            assistant_message = _message_to_dict(response["choices"][0]["message"])
            history.append(assistant_message)
            turn_messages.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                print(assistant_message.get("content", ""))
                break

            for tool_call in tool_calls:
                tool_call = _tool_call_to_dict(tool_call)
                function_call = tool_call["function"]
                tool_name = function_call["name"]
                tool_arguments = function_call.get("arguments", "{}")
                tool_result = call_file_tool(tool_name, tool_arguments)

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_name,
                    "content": _tool_result_to_content(tool_result),
                }
                history.append(tool_message)
                turn_messages.append(tool_message)

        try:
            await memory_app.add_messages(
                session_id=session_id,
                user_id=memory_app.config.user_id,
                messages=turn_messages,
            )
        except Exception as error:
            print(f"Memory save failed: {error}")


if __name__ == "__main__":
    asyncio.run(main())
