from .file_tools import (
    FILE_TOOL_FUNCTIONS,
    FILE_TOOL_SCHEMAS,
    append_text_file,
    call_file_tool,
    configure_file_workspace,
    create_directory,
    delete_directory,
    delete_file,
    detect_file_encoding,
    list_directory,
    path_info,
    read_text_file,
    write_text_file,
)
from .skill_tools import SKILL_TOOL_FUNCTIONS, SKILL_TOOL_SCHEMAS, call_skill_tool
from .shell_tools import (
    SHELL_TOOL_FUNCTIONS,
    SHELL_TOOL_SCHEMAS,
    call_shell_tool,
    configure_shell_workspace,
)

TOOL_SCHEMAS = [
    *FILE_TOOL_SCHEMAS,
    *SKILL_TOOL_SCHEMAS,
    *SHELL_TOOL_SCHEMAS,
]

TOOL_FUNCTIONS = {
    **FILE_TOOL_FUNCTIONS,
    **SKILL_TOOL_FUNCTIONS,
    **SHELL_TOOL_FUNCTIONS,
}


def get_tool_schemas(*, file_tools_enabled):
    if file_tools_enabled:
        return TOOL_SCHEMAS
    return [
        *SKILL_TOOL_SCHEMAS,
        *SHELL_TOOL_SCHEMAS,
    ]


def call_tool(name, arguments, *, file_tools_enabled):
    if name in FILE_TOOL_FUNCTIONS:
        if not file_tools_enabled:
            raise PermissionError("File tools are disabled because no workspace was provided.")
        return call_file_tool(name, arguments)
    if name in SKILL_TOOL_FUNCTIONS:
        return call_skill_tool(name, arguments)
    if name in SHELL_TOOL_FUNCTIONS:
        return call_shell_tool(name, arguments)
    raise ValueError(f"Unknown tool: {name}")

__all__ = [
    "FILE_TOOL_FUNCTIONS",
    "FILE_TOOL_SCHEMAS",
    "SKILL_TOOL_FUNCTIONS",
    "SKILL_TOOL_SCHEMAS",
    "SHELL_TOOL_FUNCTIONS",
    "SHELL_TOOL_SCHEMAS",
    "TOOL_FUNCTIONS",
    "TOOL_SCHEMAS",
    "append_text_file",
    "call_file_tool",
    "call_skill_tool",
    "call_shell_tool",
    "call_tool",
    "configure_file_workspace",
    "configure_shell_workspace",
    "create_directory",
    "delete_directory",
    "delete_file",
    "detect_file_encoding",
    "get_tool_schemas",
    "list_directory",
    "path_info",
    "read_text_file",
    "write_text_file",
]
