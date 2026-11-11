"""toolcap: keep MCP tool results under the client's token limit, with cursor pagination.

>>> from toolcap import Cap
>>> cap = Cap(max_tokens=20_000)
>>> text = cap.page(rows, cursor)      # a page of whole items + next_cursor
>>> text = cap.trim(rows)              # first part only, with a note on what was cut

Or as a decorator under ``@mcp.tool()``: ``@cap.paginate``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence, Union

from .core import DEFAULT_MAX_TOKENS, Cap, decode_cursor, encode_cursor, estimate_tokens, jsonable
from .errors import CursorError

__version__ = "0.1.0"

__all__ = [
    "Cap",
    "CursorError",
    "DEFAULT_MAX_TOKENS",
    "decode_cursor",
    "encode_cursor",
    "estimate_tokens",
    "jsonable",
    "page",
    "paginate",
    "trim",
]

Key = Union[str, Sequence[str], None]


def page(data: Any, cursor: Optional[str] = "", *, max_tokens: Optional[int] = None, key: Key = None) -> str:
    """``Cap(max_tokens).page(data, cursor)``: one page of ``data`` that fits the budget."""
    return Cap(max_tokens).page(data, cursor, key=key)


def trim(data: Any, *, max_tokens: Optional[int] = None, key: Key = None) -> str:
    """``Cap(max_tokens).trim(data)``: ``data`` cut down to fit, with a note on what was left out."""
    return Cap(max_tokens).trim(data, key=key)


def paginate(fn: Optional[Callable] = None, *, max_tokens: Optional[int] = None, key: Key = None):
    """``@paginate`` / ``@paginate(max_tokens=...)``: decorator form of :func:`page`."""
    cap = Cap(max_tokens)
    return cap.paginate(fn, key=key)
