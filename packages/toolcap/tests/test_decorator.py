from __future__ import annotations  # the decorator must resolve string annotations

import asyncio
import inspect
import json
from typing import Optional

import pytest

from toolcap import Cap, CursorError, paginate

cap = Cap(400)
ROWS = [{"id": i, "text": "abc " * 10} for i in range(100)]


@cap.paginate
def sync_tool(repo: str, limit: Optional[int] = None) -> list:
    """List things."""
    return ROWS[:limit]


@cap.paginate
async def async_tool(repo: str, *, flag: bool = False, **extra) -> list:
    return ROWS


@cap.paginate
def own_cursor(cursor: str = "") -> list:
    own_cursor.seen.append(cursor)
    return ROWS


own_cursor.seen = []


def test_signature_has_cursor_and_real_types():
    sig = inspect.signature(sync_tool)
    assert list(sig.parameters) == ["repo", "limit", "cursor"]
    assert sig.parameters["repo"].annotation is str
    assert sig.parameters["limit"].annotation == Optional[int]
    assert sig.parameters["cursor"].default == "" and sig.parameters["cursor"].annotation is str
    # No return type, so MCP SDKs don't duplicate the page as structuredContent.
    assert sig.return_annotation is inspect.Signature.empty
    assert "return" not in sync_tool.__annotations__
    assert not hasattr(sync_tool, "__wrapped__")
    assert sync_tool.__name__ == "sync_tool"
    assert sync_tool.__doc__.startswith("List things.") and "next_cursor" in sync_tool.__doc__


def test_cursor_goes_before_var_keyword():
    assert list(inspect.signature(async_tool).parameters) == ["repo", "flag", "cursor", "extra"]
    assert inspect.iscoroutinefunction(async_tool)


def test_sync_and_async_pages():
    first = json.loads(sync_tool("r"))
    second = json.loads(sync_tool("r", cursor=first["page"]["next_cursor"]))
    assert second["items"][0]["id"] == len(first["items"])
    out = json.loads(asyncio.run(async_tool("r", cursor=first["page"]["next_cursor"])))
    assert out["items"][0]["id"] == len(first["items"])


def test_existing_cursor_param_is_passed_through():
    first = json.loads(own_cursor())
    own_cursor(cursor=first["page"]["next_cursor"])
    assert own_cursor.seen == ["", first["page"]["next_cursor"]]
    assert list(inspect.signature(own_cursor).parameters) == ["cursor"]


def test_bad_cursor_is_rejected_before_the_tool_runs():
    calls = []

    @cap.paginate
    def tool() -> list:
        calls.append(1)
        return ROWS

    with pytest.raises(CursorError):
        tool(cursor="nope!")
    assert calls == []


def test_module_level_decorator_forms():
    @paginate
    def a() -> list:
        return ROWS

    @paginate(max_tokens=300, key="rows")
    def b() -> dict:
        return {"rows": ROWS}

    assert json.loads(a()) == ROWS  # default budget: all fits
    assert json.loads(b())["page"]["total"] == 100
