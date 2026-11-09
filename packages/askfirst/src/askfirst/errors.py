"""Exceptions raised by askfirst.

They subclass the MCP SDK's own ``ToolError`` classes (whichever of them are
installed), so the server sends their message to the model as the tool's error
result instead of hiding it as a crash.
"""

from __future__ import annotations

import importlib
from typing import Optional, Tuple, Type


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
    # Drop any base that is a subclass of another, so the MRO stays valid.
    bases = [b for b in bases if not any(o is not b and issubclass(o, b) for o in bases)]
    return tuple(bases) or (Exception,)


class AskFirstError(*_tool_error_bases()):  # type: ignore[misc]
    """Base class. The message is written for the model that called the tool."""


class ConfirmationRequired(AskFirstError):
    """The user has not confirmed yet. The message tells the model how to continue.

    Raised when the client cannot show a confirmation prompt (no elicitation
    support). ``token`` is the confirmation token the model must send back on its
    next call, after the user agrees.
    """

    def __init__(self, text: str, *, token: str, expires_in: int, reason: Optional[str] = None):
        super().__init__(text)
        self.token = token
        self.expires_in = expires_in
        #: Why an earlier token was refused, if one was sent ("expired", "used", ...).
        self.reason = reason


class Declined(AskFirstError):
    """The user said no, closed the prompt, or did not answer in time."""

    def __init__(self, text: str, *, action: str = "decline"):
        super().__init__(text)
        #: "decline", "cancel" or "timeout".
        self.action = action


class ConfirmationUnavailable(AskFirstError):
    """``mode="elicit"`` was set and the client cannot show a confirmation prompt."""
