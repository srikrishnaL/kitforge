"""Exceptions raised by toolcap.

``CursorError`` subclasses the MCP SDK's own ``ToolError`` classes (whichever
are installed), so the server sends its message to the model as the tool's
error result instead of hiding it as an internal error.
"""

from __future__ import annotations

import importlib
from typing import Tuple, Type


def _tool_error_bases() -> Tuple[Type[Exception], ...]:
    bases = []
    for module in (
        "mcp.server.mcpserver.exceptions",  # MCP Python SDK 2.x (MCPServer)
        "mcp.server.fastmcp.exceptions",  # MCP Python SDK 1.x (FastMCP)
        "fastmcp.exceptions",  # the standalone fastmcp package
    ):
        try:
            cls = getattr(importlib.import_module(module), "ToolError")
        except Exception:  # not installed, or a version without it
            continue
        if isinstance(cls, type) and issubclass(cls, Exception) and cls not in bases:
            bases.append(cls)
    bases = [b for b in bases if not any(o is not b and issubclass(o, b) for o in bases)]
    if not any(issubclass(b, ValueError) for b in bases):
        bases.append(ValueError)
    return tuple(bases)


class CursorError(*_tool_error_bases()):  # type: ignore[misc]
    """The cursor the model sent is not one toolcap issued. The message says how to recover."""
