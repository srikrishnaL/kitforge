"""The one exception bomscout raises.

It subclasses the MCP SDK's own ``ToolError`` (whichever SDK is installed), so the server
sends its message to the model as the tool's error result instead of hiding it as an
internal error.
"""

from __future__ import annotations

import importlib
from typing import Optional, Tuple, Type


def _tool_error_bases() -> Tuple[Type[Exception], ...]:
    bases = []
    for module in (
        "mcp.server.mcpserver.exceptions",  # MCP Python SDK 2.x (MCPServer)
        "mcp.server.fastmcp.exceptions",  # MCP Python SDK 1.x (FastMCP)
    ):
        try:
            cls = getattr(importlib.import_module(module), "ToolError")
        except Exception:  # not installed, or a version without it
            continue
        if isinstance(cls, type) and issubclass(cls, Exception) and cls not in bases:
            bases.append(cls)
    bases = [b for b in bases if not any(o is not b and issubclass(o, b) for o in bases)]
    return tuple(bases) or (Exception,)


class DTError(*_tool_error_bases()):  # type: ignore[misc]
    """A Dependency-Track request failed. The message says why and what to do."""

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        #: The HTTP status, when the server answered.
        self.status = status
