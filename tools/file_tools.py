from __future__ import annotations

import json
import locale
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Any


TEXT_ENCODING_CANDIDATES = (
    "utf-8",
    "gb18030",
    "gbk",
    "gb2312",
    "big5",
    "cp1252",
    "latin-1",
)

ENCODING_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

FILE_WORKSPACE: ContextVar[Path | None] = ContextVar("file_workspace", default=None)


def configure_file_workspace(workspace: str | None) -> None:
    FILE_WORKSPACE.set(Path(workspace).expanduser().resolve() if workspace else None)


def _current_file_workspace() -> Path | None:
    return FILE_WORKSPACE.get()


def _to_path(path: str) -> Path:
    file_workspace = _current_file_workspace()
    if file_workspace is None:
        raise PermissionError("File tools are disabled because no workspace was provided.")

    raw_path = Path(path).expanduser()
    target = raw_path if raw_path.is_absolute() else file_workspace / raw_path
    resolved = target.resolve()
    if resolved != file_workspace and file_workspace not in resolved.parents:
        raise PermissionError(f"Path is outside workspace: {resolved}")
    return resolved


def detect_file_encoding(path: str, sample_size: int = 64 * 1024) -> dict[str, Any]:
    """Detect the most likely text encoding for a file before reading it."""
    file_path = _to_path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    with file_path.open("rb") as file:
        sample = file.read(sample_size)
    if not sample:
        return {"path": str(file_path), "encoding": "utf-8", "confidence": 1.0}

    for bom, encoding in ENCODING_BOMS:
        if sample.startswith(bom):
            return {"path": str(file_path), "encoding": encoding, "confidence": 1.0}

    encodings = list(TEXT_ENCODING_CANDIDATES)
    preferred_encoding = locale.getpreferredencoding(False)
    if preferred_encoding and preferred_encoding not in encodings:
        encodings.insert(1, preferred_encoding)

    for encoding in encodings:
        try:
            sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        return {"path": str(file_path), "encoding": encoding, "confidence": 0.8}

    return {"path": str(file_path), "encoding": "utf-8", "confidence": 0.0}


def read_text_file(path: str, encoding: str | None = None) -> dict[str, Any]:
    """Read a text file after detecting its encoding when one is not supplied."""
    file_path = _to_path(path)
    detected = detect_file_encoding(str(file_path))
    file_encoding = encoding or detected["encoding"]

    content = file_path.read_text(encoding=file_encoding)
    return {
        "path": str(file_path),
        "encoding": file_encoding,
        "detected_encoding": detected["encoding"],
        "content": content,
    }


def _resolve_write_encoding(file_path: Path, encoding: str | None) -> str:
    if encoding is not None:
        return encoding
    if file_path.is_file():
        return detect_file_encoding(str(file_path))["encoding"]
    return "utf-8"


def write_text_file(
    path: str,
    content: str,
    encoding: str | None = None,
    create_parent_dirs: bool = True,
) -> dict[str, Any]:
    """Write a text file, preserving an existing file's encoding by default."""
    file_path = _to_path(path)
    file_encoding = _resolve_write_encoding(file_path, encoding)
    if create_parent_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(content, encoding=file_encoding)
    return {"path": str(file_path), "encoding": file_encoding, "bytes": file_path.stat().st_size}


def append_text_file(
    path: str,
    content: str,
    encoding: str | None = None,
    create_parent_dirs: bool = True,
) -> dict[str, Any]:
    """Append text to a file, preserving an existing file's encoding by default."""
    file_path = _to_path(path)
    file_encoding = _resolve_write_encoding(file_path, encoding)
    if create_parent_dirs:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("a", encoding=file_encoding) as file:
        file.write(content)

    return {"path": str(file_path), "encoding": file_encoding, "bytes": file_path.stat().st_size}


def list_directory(path: str = ".", recursive: bool = False) -> list[dict[str, Any]]:
    """List files and directories under a directory."""
    directory = _to_path(path)
    if not directory.is_dir():
        raise NotADirectoryError(f"Directory not found: {directory}")

    paths = directory.rglob("*") if recursive else directory.iterdir()
    return [
        {
            "path": str(item),
            "name": item.name,
            "is_dir": item.is_dir(),
            "size": item.stat().st_size if item.is_file() else None,
        }
        for item in sorted(paths, key=lambda item: str(item).lower())
    ]


def create_directory(path: str, parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
    """Create a directory."""
    directory = _to_path(path)
    directory.mkdir(parents=parents, exist_ok=exist_ok)
    return {"path": str(directory), "created": directory.is_dir()}


def delete_file(path: str, missing_ok: bool = True) -> dict[str, Any]:
    """Delete a file."""
    file_path = _to_path(path)
    file_path.unlink(missing_ok=missing_ok)
    return {"path": str(file_path), "exists": file_path.exists()}


def delete_directory(path: str) -> dict[str, Any]:
    """Delete a directory and all of its contents."""
    directory = _to_path(path)
    shutil.rmtree(directory)
    return {"path": str(directory), "exists": directory.exists()}


def path_info(path: str) -> dict[str, Any]:
    """Return basic information about a path."""
    target = _to_path(path)
    exists = target.exists()
    return {
        "path": str(target),
        "exists": exists,
        "is_file": target.is_file() if exists else False,
        "is_dir": target.is_dir() if exists else False,
        "size": target.stat().st_size if exists and target.is_file() else None,
    }


FILE_TOOL_FUNCTIONS = {
    "detect_file_encoding": detect_file_encoding,
    "read_text_file": read_text_file,
    "write_text_file": write_text_file,
    "append_text_file": append_text_file,
    "list_directory": list_directory,
    "create_directory": create_directory,
    "delete_file": delete_file,
    "delete_directory": delete_directory,
    "path_info": path_info,
}

FILE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "detect_file_encoding",
            "description": "Detect the most likely text encoding for a file before reading it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "sample_size": {"type": "integer", "default": 65536},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": "Read a text file. Detects encoding first when encoding is omitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "encoding": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_text_file",
            "description": (
                "Write a text file. Preserves an existing file's encoding by default; "
                "uses UTF-8 for new files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {
                        "type": "string",
                        "description": "Optional explicit encoding. If omitted, existing files keep their detected encoding.",
                    },
                    "create_parent_dirs": {"type": "boolean", "default": True},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_text_file",
            "description": (
                "Append text to a file. Preserves an existing file's encoding by default; "
                "uses UTF-8 for new files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {
                        "type": "string",
                        "description": "Optional explicit encoding. If omitted, existing files keep their detected encoding.",
                    },
                    "create_parent_dirs": {"type": "boolean", "default": True},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories under a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": False},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "parents": {"type": "boolean", "default": True},
                    "exist_ok": {"type": "boolean", "default": True},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "missing_ok": {"type": "boolean", "default": True},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_directory",
            "description": "Delete a directory and all of its contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "path_info",
            "description": "Return basic information about a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]


def call_file_tool(name: str, arguments: str | dict[str, Any]) -> Any:
    """Call one of the file tools from a LiteLLM/OpenAI tool call."""
    function = FILE_TOOL_FUNCTIONS.get(name)
    if function is None:
        raise ValueError(f"Unknown file tool: {name}")

    parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    return function(**parsed_arguments)
