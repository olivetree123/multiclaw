import os
import json
import uuid
import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv
from litellm import completion

from memory import MemoryApp
from skills import format_skill_summaries, load_skill_summaries
from tools import call_tool, configure_file_workspace, get_tool_schemas

load_dotenv(dotenv_path="./docker/.env")

skill_prompt = format_skill_summaries(load_skill_summaries())
system_prompt = "# Assistant Instructions\n\nYou are a helpful assistant that can answer questions and help with tasks."
if skill_prompt:
    system_prompt = f"{system_prompt}\n\n## Available Skills\n{skill_prompt}"

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


async def _build_system_prompt(
    memory_app: MemoryApp,
    query: str,
    session_id: str,
    workspace: Path | None,
) -> str:
    current_system_prompt = system_prompt
    if workspace is None:
        current_system_prompt = (
            f"{current_system_prompt}\n\n## Workspace\n"
            "No workspace was provided. File and directory operations are disabled. "
            "Do not claim to read, write, list, create, or delete files.")
    else:
        current_system_prompt = (
            f"{current_system_prompt}\n\n## Workspace\n"
            f"Workspace: {workspace}\n"
            "File and directory operations are allowed only inside this workspace.")

    try:
        memory_context = await memory_app.build_context(
            query=query,
            user_id=memory_app.config.user_id,
            session_id=session_id,
        )
    except Exception as error:
        print(f"Memory search failed: {error}")
        return current_system_prompt

    if not memory_context:
        return current_system_prompt

    return f"{current_system_prompt}\n\n## Relevant Long-Term Memory\n{memory_context}"


async def run_agent(workspace: Path | None, session_id: str | None):
    memory_app = MemoryApp()
    await memory_app.initialize()
    session = await memory_app.ensure_session(
        user_id=memory_app.config.user_id,
        session_id=str(session_id) if session_id else None,
        workspace=str(workspace) if workspace else None,
    )
    active_session_id = session.id
    active_workspace = Path(
        session.workspace).expanduser().resolve() if session.workspace else None
    print(f"Active session_id: {active_session_id}")
    print(f"Active  workspace: {active_workspace}")
    configure_file_workspace(str(active_workspace) if active_workspace else None)

    try:
        await _chat_loop(memory_app, active_session_id, active_workspace)
    finally:
        await memory_app.close()


async def _chat_loop(memory_app: MemoryApp, session_id: str, workspace: Path | None):
    file_tools_enabled = workspace is not None
    tool_schemas = get_tool_schemas(file_tools_enabled=file_tools_enabled)

    while True:
        input_text = await asyncio.to_thread(input, "Enter your message: ")
        if input_text == "exit":
            break

        user_message = {"role": "user", "content": input_text}
        history.append(user_message)
        turn_messages = [user_message]
        current_system_prompt = await _build_system_prompt(memory_app, input_text, session_id,
                                                           workspace)

        while True:
            response = await asyncio.to_thread(
                completion,
                model=os.getenv("LLM_MODEL"),
                base_url=os.getenv("LLM_BASE_URL"),
                api_key=os.getenv("LLM_API_KEY"),
                tools=tool_schemas,
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
                tool_result = call_tool(
                    tool_name,
                    tool_arguments,
                    file_tools_enabled=file_tools_enabled,
                )

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


@click.command()
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Restrict file and directory tools to this workspace directory.",
)
@click.option(
    "--session-id",
    type=click.UUID,
    default=None,
    help="Continue an existing conversation session. If omitted, a new session id is generated.",
)
def main(workspace: Path | None, session_id: uuid.UUID | None):
    resolved_workspace = workspace.expanduser().resolve() if workspace else None
    asyncio.run(run_agent(resolved_workspace, session_id))


if __name__ == "__main__":
    main()
