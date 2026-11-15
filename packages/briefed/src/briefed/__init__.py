"""briefed: deliver an MCP server's instructions to clients that drop them.

Many MCP clients ignore (or cut) the ``instructions`` a server sends in ``initialize``, but
every client shows the model the tool list. briefed moves the instructions there.

>>> from briefed import Brief
>>> brief = Brief("Call search before get. All dates are UTC.")
>>> tools = brief.tools(tools)        # adds a server_instructions tool + a hint per tool

As a proxy for any stdio server: ``briefed proxy -- <server command>``.
As ASGI middleware for a remote server: ``briefed.asgi.Briefed(app)``.
"""

from __future__ import annotations

from .core import DEFAULT_TOOL_NAME, INLINE_LIMIT, MODES, Brief, fold

__version__ = "0.1.0"

__all__ = ["Brief", "DEFAULT_TOOL_NAME", "INLINE_LIMIT", "MODES", "fold"]
