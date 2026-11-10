"""Adapt whole MCP tools (name, description, inputSchema) for a provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .core import GEMINI, Change, Shim, adapt
from .names import unique_name


def _get(tool: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(tool, dict):
            if key in tool:
                return tool[key]
        elif hasattr(tool, key):
            return getattr(tool, key)
    return None


@dataclass
class AdaptedTool:
    """One tool, ready to send to the provider."""

    name: str
    original_name: str
    description: str
    target: str
    shim: Shim
    changes: List[Change] = field(default_factory=list)

    @property
    def parameters(self) -> Dict[str, Any]:
        return self.shim.schema

    @property
    def function(self) -> Dict[str, Any]:
        """``{"name", "description", "parameters"}`` (plus ``"strict": True`` for OpenAI).

        For the OpenAI Responses API send ``{"type": "function", **tool.function}``.
        """
        out: Dict[str, Any] = {"name": self.name, "description": self.description}
        if self.target == GEMINI:
            if self.parameters.get("properties"):
                out["parameters"] = self.parameters
            return out
        out["parameters"] = self.parameters
        out["strict"] = True
        return out

    @property
    def declaration(self) -> Dict[str, Any]:
        """A Gemini ``functionDeclaration``, or an OpenAI Chat Completions ``tools`` entry."""
        if self.target == GEMINI:
            return self.function
        return {"type": "function", "function": self.function}

    def restore(self, args: Any) -> Any:
        return self.shim.restore(args)


def adapt_tool(tool: Any, target: str, *, name: Optional[str] = None,
               taken: Optional[set] = None, max_depth: int = 10) -> AdaptedTool:
    """Adapt one MCP tool: a ``tools/list`` entry as a dict, or an ``mcp.types.Tool``."""
    original = _get(tool, "name")
    if not isinstance(original, str) or not original:
        raise ValueError("tool has no name")
    schema = _get(tool, "inputSchema", "input_schema", "parameters")
    description = _get(tool, "description") or ""
    shim = adapt(schema, target, max_depth=max_depth)
    provider_name = name or unique_name(original, target, taken or set())
    changes = list(shim.changes)
    if provider_name != original:
        changes.insert(0, Change("(name)", f"renamed to {provider_name}"))
    return AdaptedTool(provider_name, original, description, target, shim, changes)


class ToolKit:
    """A set of adapted tools that maps the model's calls back to the original tools."""

    def __init__(self, tools: Iterable[Any], target: str, *, max_depth: int = 10) -> None:
        if isinstance(tools, dict) and isinstance(tools.get("tools"), list):
            tools = tools["tools"]
        elif _get(tools, "tools") is not None and not isinstance(tools, (list, tuple)):
            tools = _get(tools, "tools")
        self.target = target
        self.tools: List[AdaptedTool] = []
        self._by_name: Dict[str, AdaptedTool] = {}
        seen = set()
        for tool in tools:
            adapted = adapt_tool(tool, target, taken=set(self._by_name), max_depth=max_depth)
            if adapted.original_name in seen:
                raise ValueError(f"duplicate tool name {adapted.original_name!r}")
            seen.add(adapted.original_name)
            self.tools.append(adapted)
            self._by_name[adapted.name] = adapted

    def __iter__(self):
        return iter(self.tools)

    def __len__(self) -> int:
        return len(self.tools)

    @property
    def declarations(self) -> List[Dict[str, Any]]:
        return [t.declaration for t in self.tools]

    def get(self, name: str) -> AdaptedTool:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"the model called an unknown tool {name!r}") from None

    def restore(self, name: str, args: Any) -> Tuple[str, Any]:
        """Map a call from the model to ``(original tool name, original-shaped arguments)``."""
        tool = self.get(name)
        return tool.original_name, tool.restore(args)


def adapt_tools(tools: Iterable[Any], target: str, *, max_depth: int = 10) -> ToolKit:
    """Adapt a list of MCP tools (or a ``tools/list`` result) for ``target``."""
    return ToolKit(tools, target, max_depth=max_depth)
