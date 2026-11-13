"""mcptest: automated tests and CI checks for MCP servers."""

from __future__ import annotations

from typing import Any, Iterable

from .client import (
    MCPClient,
    MCPError,
    MCPTimeout,
    MCPTransportError,
    ToolResult,
)
from .lint import RULES, Finding, at_or_above, lint_tools
from .snapshot import Change, diff, normalize

__version__ = "0.2.1"

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPTimeout",
    "MCPTransportError",
    "ToolResult",
    "Finding",
    "RULES",
    "lint_tools",
    "assert_lint_clean",
    "Change",
    "diff",
    "normalize",
    "__version__",
]


def assert_lint_clean(source: Any, *, fail_on: str = "warning", ignore: Iterable[str] = ()) -> None:
    """Raise ``AssertionError`` if the tools have findings at ``fail_on`` or worse.

    ``source`` is an :class:`MCPClient` or a ``tools/list`` result.
    """
    tools = source.list_tools() if isinstance(source, MCPClient) else source
    findings = at_or_above(lint_tools(tools, ignore=ignore), fail_on)
    if findings:
        body = "\n  ".join(str(f) for f in findings)
        raise AssertionError(f"{len(findings)} MCP lint finding(s):\n  {body}")
