"""End-to-end tests with real MCP Python SDK servers and in-memory clients.

They run with whichever SDK is installed: 1.x (FastMCP) or 2.x (MCPServer).
The MCP SDK needs Python 3.10+, so on 3.9 only the SDK-free tests run.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

mcp = pytest.importorskip("mcp")

from toolcap import Cap  # noqa: E402

try:  # SDK 2.x
    from mcp import Client
    from mcp.server.mcpserver import Context, MCPServer as Server

    V2 = True
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import Context, FastMCP as Server
    from mcp.shared.memory import create_connected_server_and_client_session

    V2 = False

cap = Cap(1500)
ROWS = [{"id": i, "title": f"Flow run {i}", "state": "COMPLETED", "log": "ok " * 40} for i in range(200)]


def build():
    srv = Server("demo")

    @srv.tool()
    @cap.paginate
    def list_runs(state: str = "COMPLETED", limit: int = 200) -> list:
        """List flow runs."""
        return [r for r in ROWS if r["state"] == state][:limit]

    @srv.tool()
    @cap.paginate(key="runs")
    async def search_runs(query: str, ctx: Context) -> dict:
        """Search flow runs."""
        return {"query": query, "runs": ROWS}

    @srv.tool(structured_output=False)
    def read_log(cursor: str = "") -> str:
        """Read the server log."""
        return cap.page("".join(f"line {i}\n" for i in range(3000)), cursor)

    return srv


@asynccontextmanager
async def connect(srv):
    if V2:
        async with Client(srv) as c:
            yield c
    else:
        async with create_connected_server_and_client_session(getattr(srv, "_mcp_server", srv)) as c:
            yield c


def is_error(result):
    return getattr(result, "is_error", None) if V2 else result.isError


def input_schema(tool):
    return getattr(tool, "input_schema", None) or tool.inputSchema


def text(result):
    return result.content[0].text


def test_tool_schema_gets_cursor_and_keeps_other_params():
    async def go():
        async with connect(build()) as c:
            return (await c.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    schema = input_schema(tools["list_runs"])
    props = schema["properties"]
    assert set(props) == {"state", "limit", "cursor"}
    assert props["limit"]["type"] == "integer" and props["cursor"]["type"] == "string"
    assert "required" not in schema or schema["required"] == []
    assert "next_cursor" in tools["list_runs"].description
    search = tools["search_runs"]
    s = input_schema(search)
    assert set(s["properties"]) == {"query", "cursor"}, "Context is not exposed to the model"


def test_pages_are_not_duplicated_as_structured_content():
    async def go():
        async with connect(build()) as c:
            tools = (await c.list_tools()).tools
            r = await c.call_tool("list_runs", {})
            return {t.name: t for t in tools}["list_runs"], r

    tool, r = asyncio.run(go())
    assert (getattr(tool, "output_schema", None) or getattr(tool, "outputSchema", None)) is None
    assert (getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)) is None
    assert len(r.content) == 1


def test_model_can_page_through_a_decorated_tool():
    async def go():
        async with connect(build()) as c:
            ids, cursor, calls = [], "", 0
            while True:
                args = {"limit": 150}
                if cursor:
                    args["cursor"] = cursor
                r = await c.call_tool("list_runs", args)
                assert not is_error(r), text(r)
                assert cap.fits(text(r))
                doc = json.loads(text(r))
                ids += [x["id"] for x in doc["items"]]
                cursor = doc["page"]["next_cursor"]
                calls += 1
                if not cursor:
                    return ids, calls

    ids, calls = asyncio.run(go())
    assert ids == list(range(150)) and calls > 2


def test_async_tool_with_context_and_key():
    async def go():
        async with connect(build()) as c:
            first = await c.call_tool("search_runs", {"query": "x"})
            doc = json.loads(text(first))
            second = await c.call_tool("search_runs", {"query": "x", "cursor": doc["page"]["next_cursor"]})
            return doc, json.loads(text(second))

    a, b = asyncio.run(go())
    assert a["query"] == "x" and a["runs"][0]["id"] == 0
    assert b["runs"][0]["id"] == len(a["runs"])


def test_bad_cursor_reaches_the_model_as_a_tool_error():
    async def go():
        async with connect(build()) as c:
            return await c.call_tool("list_runs", {"cursor": "made-up"})

    r = asyncio.run(go())
    assert is_error(r)
    assert "Invalid cursor" in text(r) and "without a cursor" in text(r)


def test_manual_page_call_on_text():
    async def go():
        async with connect(build()) as c:
            return await c.call_tool("read_log", {})

    r = asyncio.run(go())
    assert text(r).startswith("line 0\n") and 'cursor="' in text(r)
    assert (getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)) is None
