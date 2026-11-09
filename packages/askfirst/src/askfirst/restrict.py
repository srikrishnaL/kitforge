"""Read-only mode and tool allow/deny lists for an MCP server.

``restrict`` removes tools from the server itself, so a hidden tool is not just
missing from ``tools/list``: calling it by name fails too. Read-only mode keeps
only tools that declare ``readOnlyHint=True``. A tool with no annotations is
treated as one that writes (fail closed).
"""

from __future__ import annotations

import fnmatch
import os
from typing import Any, Iterable, List, Mapping, Optional, Sequence

__all__ = ["restrict", "restrict_from_env", "is_read_only"]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


def is_read_only(tool: Any) -> bool:
    """True only if the tool's annotations say ``readOnlyHint`` is true."""
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return False
    if isinstance(annotations, Mapping):
        value = annotations.get("readOnlyHint", annotations.get("read_only_hint"))
    else:
        value = getattr(annotations, "readOnlyHint", None)
        if value is None:
            value = getattr(annotations, "read_only_hint", None)
    return value is True


def _tool_manager(server: Any) -> Any:
    manager = getattr(server, "_tool_manager", None)
    if manager is None or not hasattr(manager, "list_tools"):
        raise TypeError(
            "restrict() needs an MCP Python SDK server (FastMCP from mcp 1.x or MCPServer from "
            f"mcp 2.x); got {type(server).__module__}.{type(server).__name__}"
        )
    return manager


def _matches(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _clean(patterns: Optional[Iterable[str]]) -> Optional[List[str]]:
    if patterns is None:
        return None
    if isinstance(patterns, str):
        patterns = patterns.split(",")
    return [p.strip() for p in patterns if p and p.strip()]


def restrict(
    server: Any,
    *,
    read_only: bool = False,
    allow: Optional[Iterable[str]] = None,
    deny: Optional[Iterable[str]] = None,
) -> List[str]:
    """Remove the tools this deployment must not offer. Call it after registering tools.

    Args:
        server: A ``FastMCP`` (mcp 1.x) or ``MCPServer`` (mcp 2.x) instance.
        read_only: Keep only tools annotated ``readOnlyHint=True``.
        allow: If given, keep only tools whose name matches one of these glob
            patterns (``"list_*"``, ``"get_issue"``). An empty list removes all.
        deny: Remove tools matching any of these patterns. Deny wins over allow.

    Returns:
        The names of the removed tools, sorted.

    Raises:
        ValueError: If an ``allow`` or ``deny`` pattern matches no tool, which is
            almost always a typo that would silently leave a tool exposed or
            hidden.
    """
    manager = _tool_manager(server)
    allow_p = _clean(allow)
    deny_p = _clean(deny) or []
    tools = list(manager.list_tools())
    names = [t.name for t in tools]
    for label, patterns in (("allow", allow_p or []), ("deny", deny_p)):
        unknown = [p for p in patterns if not any(fnmatch.fnmatchcase(n, p) for n in names)]
        if unknown:
            raise ValueError(f"{label} pattern(s) match no tool: {', '.join(unknown)}")

    removed = []
    for tool in tools:
        name = tool.name
        keep = True
        if allow_p is not None and not _matches(name, allow_p):
            keep = False
        if _matches(name, deny_p):
            keep = False
        if read_only and not is_read_only(tool):
            keep = False
        if not keep:
            removed.append(name)
    remove = _remover(server, manager)
    for name in removed:
        remove(name)
    return sorted(removed)


def _remover(server: Any, manager: Any) -> Any:
    if callable(getattr(server, "remove_tool", None)):
        return server.remove_tool
    if callable(getattr(manager, "remove_tool", None)):
        return manager.remove_tool
    tools = getattr(manager, "_tools", None)  # mcp < 1.11 has no remove_tool
    if isinstance(tools, dict):
        return tools.__delitem__
    raise TypeError(f"cannot remove tools from {type(server).__name__}")


def _flag(env: Mapping[str, str], key: str) -> bool:
    raw = env.get(key, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"{key} must be true or false, not {env[key]!r}")


def restrict_from_env(
    server: Any, prefix: str = "MCP", env: Optional[Mapping[str, str]] = None
) -> List[str]:
    """``restrict`` configured by environment variables, for users of your server.

    ``<PREFIX>_READ_ONLY=true``, ``<PREFIX>_ALLOW_TOOLS=list_*,get_*`` and
    ``<PREFIX>_DENY_TOOLS=delete_*`` (comma-separated glob patterns). Unset
    variables change nothing.
    """
    env = os.environ if env is None else env
    allow = env.get(f"{prefix}_ALLOW_TOOLS")
    deny = env.get(f"{prefix}_DENY_TOOLS")
    return restrict(
        server,
        read_only=_flag(env, f"{prefix}_READ_ONLY"),
        allow=_clean(allow) if allow is not None else None,
        deny=_clean(deny) if deny is not None else None,
    )
