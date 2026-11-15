"""Put an MCP server's instructions where every client's model will see them: the tool list.

The MCP ``initialize`` result has an ``instructions`` field for guidance that covers all of a
server's tools. Many clients drop it or cut it short, while every client shows the model the
tool list. :class:`Brief` moves the instructions into the tool list in one of two ways:

* ``mode="tool"`` (default): add a read-only tool (``server_instructions``) that returns the
  instructions, and end each tool's description with one line saying to call it first. Long
  instructions stay whole, because tool results are not cut the way descriptions are.
* ``mode="inline"``: put the instructions at the top of one tool's description (the first
  tool, or ``target=``). No extra call, but clients that cut long descriptions cut them too.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

MODES = ("tool", "inline")
DEFAULT_TOOL_NAME = "server_instructions"

#: Clients that cut descriptions tend to do it around 1,024 characters (mcptest lint MCP006).
INLINE_LIMIT = 1024


class Brief:
    """Fold ``instructions`` into MCP tool lists and answer calls to the instructions tool.

    >>> brief = Brief("Use search before get. Dates are UTC.")
    >>> tools = brief.tools([{"name": "search", "description": "Find rows",
    ...                       "inputSchema": {"type": "object"}}])
    >>> [t["name"] for t in tools]
    ['search', 'server_instructions']
    """

    def __init__(self, instructions: Optional[str] = None, *, mode: str = "tool",
                 tool_name: str = DEFAULT_TOOL_NAME, target: Optional[str] = None,
                 hint: bool = True, extra: Optional[str] = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        if not tool_name or not isinstance(tool_name, str):
            raise ValueError("tool_name must be a non-empty string")
        self.given = _clean(instructions)  # replaces the server's own instructions
        self.extra = _clean(extra)  # added after them
        self.server: Optional[str] = None  # learned from the initialize result
        self.mode = mode
        self.tool_name = tool_name
        self.target = target
        self.hint = hint

    @property
    def instructions(self) -> Optional[str]:
        """The text the model gets: the given (else the server's) instructions, then ``extra``."""
        return _clean(_join(self.given or self.server, self.extra))

    # ------------------------------------------------------------------ tool list

    def tools(self, tools: List[Any]) -> List[Any]:
        """Return a copy of ``tools`` (MCP tool dicts) with the instructions folded in.

        Without instructions the list comes back unchanged. If the server already has a tool
        named like the instructions tool, the server's tool wins and nothing is added.
        """
        text = self.instructions
        if not text or not isinstance(tools, list):
            return tools
        out = [copy.deepcopy(t) for t in tools]
        named = [t for t in out if isinstance(t, dict) and isinstance(t.get("name"), str)]
        if self.mode == "inline":
            pick = next((t for t in named if t["name"] == self.target), None) if self.target else None
            pick = pick or (named[0] if named else None)
            if pick is not None:
                pick["description"] = _join(self.inline_text(), pick.get("description"))
            return out
        if any(t["name"] == self.tool_name for t in named):
            return out  # the server already answers this name; leave its tools alone
        if self.hint:
            for t in named:
                t["description"] = _join(t.get("description"), self.hint_text())
        out.append(self.tool())
        return out

    def tool(self) -> Dict[str, Any]:
        """The MCP definition of the instructions tool (``mode="tool"``)."""
        return {
            "name": self.tool_name,
            "title": "Server instructions",
            "description": ("Read this first. Returns this server's instructions: how its tools "
                            "fit together and the rules for using them. Call it once, before "
                            "the first call to any other tool from this server."),
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
        }

    def hint_text(self) -> str:
        return f"(Call {self.tool_name} once before using this server's tools.)"

    def inline_text(self) -> str:
        return ("How to use this server's tools (applies to all of them):\n\n"
                f"{self.instructions}\n\n---")

    # ------------------------------------------------------------------ tool calls

    def handles(self, name: Any) -> bool:
        """True if a ``tools/call`` for ``name`` should be answered by :meth:`call_result`."""
        return bool(self.instructions) and self.mode == "tool" and name == self.tool_name

    def call_result(self) -> Dict[str, Any]:
        """The ``tools/call`` result of the instructions tool."""
        return {"content": [{"type": "text", "text": self.instructions or ""}]}

    # ------------------------------------------------------------------ initialize

    def initialize_result(self, result: Any, *, keep: bool = True) -> Any:
        """Learn the server's instructions from an ``initialize`` (or ``server/discover``) result.

        Instructions given to the constructor replace the server's; ``extra`` is added to either. With ``keep=False`` the
        field is removed from the result, so clients that do read it don't get it twice.
        """
        if not isinstance(result, dict):
            return result
        found = _clean(result.get("instructions"))
        if found:
            self.server = found
        if keep or "instructions" not in result:
            return result
        return {k: v for k, v in result.items() if k != "instructions"}


def fold(tools: List[Any], instructions: Optional[str], **options: Any) -> List[Any]:
    """``Brief(instructions, **options).tools(tools)`` in one call."""
    return Brief(instructions, **options).tools(tools)


def _clean(text: Any) -> Optional[str]:
    if not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def _join(first: Any, second: Any) -> str:
    parts = [p.strip() for p in (first, second) if isinstance(p, str) and p.strip()]
    return "\n\n".join(parts)
