"""End-to-end tests against real MCP Python SDK servers and in-memory clients.

They run with whichever SDK is installed: 1.x (FastMCP) or 2.x (MCPServer).
The MCP SDK needs Python 3.10+, so on 3.9 only the SDK-free tests run.
"""

import asyncio
import re
from contextlib import asynccontextmanager

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types  # noqa: E402

from askfirst import Confirmer, restrict  # noqa: E402

try:  # SDK 2.x
    from mcp.server.mcpserver import Context, MCPServer as Server
    from mcp import Client

    V2 = True
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import Context, FastMCP as Server
    from mcp.shared.memory import create_connected_server_and_client_session

    V2 = False

from mcp.types import ToolAnnotations  # noqa: E402

DELETED = []


def build(**confirmer):
    ask = Confirmer(**confirmer)
    srv = Server("demo")

    @srv.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_branches(repo: str) -> str:
        return "main, release"

    @srv.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_branch(repo: str, branch: str, ctx: Context, confirm_token: str = "") -> str:
        await ask(ctx, f"Delete branch {branch} in {repo}?", action="delete_branch",
                  args={"repo": repo, "branch": branch}, token=confirm_token)
        DELETED.append(branch)
        return f"deleted {branch}"

    @srv.tool()
    def unannotated() -> str:
        return "?"

    return srv


@asynccontextmanager
async def connect(srv, elicit=None, modern=False):
    if V2:
        async with Client(srv, mode="auto" if modern else "legacy", elicitation_callback=elicit) as c:
            yield c
    else:
        if modern:
            pytest.skip("the 2026-07-28 protocol needs SDK 2.x")
        async with create_connected_server_and_client_session(
            getattr(srv, "_mcp_server", srv), elicitation_callback=elicit
        ) as c:
            yield c


def is_error(result):
    return getattr(result, "is_error", None) if V2 else result.isError


def text(result):
    return result.content[0].text


def answer(action, confirm=None):
    async def cb(context, params):
        cb.messages.append(params.message)
        content = None if confirm is None else {"confirm": confirm}
        return types.ElicitResult(action=action, content=content)

    cb.messages = []
    return cb


def run(coro):
    return asyncio.run(coro)


def setup_function():
    DELETED.clear()


def test_prompt_yes_runs_the_tool():
    cb = answer("accept", True)

    async def go():
        async with connect(build(), cb) as c:
            return await c.call_tool("delete_branch", {"repo": "acme/api", "branch": "old"})

    r = run(go())
    assert not is_error(r) and text(r) == "deleted old"
    assert cb.messages == ["Delete branch old in acme/api?"]
    assert DELETED == ["old"]


@pytest.mark.parametrize("action, confirm", [("decline", None), ("cancel", None), ("accept", False)])
def test_prompt_no_stops_the_tool(action, confirm):
    async def go():
        async with connect(build(), answer(action, confirm)) as c:
            return await c.call_tool("delete_branch", {"repo": "acme/api", "branch": "old"})

    r = run(go())
    assert is_error(r)
    assert "Nothing was done" in text(r)
    assert DELETED == []


def _token(message):
    m = re.search(r'confirm_token="([^"]+)"', message)
    assert m, message
    return m.group(1)


@pytest.mark.parametrize("modern", [False, True], ids=["no-elicitation", "2026-07-28"])
def test_token_fallback_round_trip(modern):
    # modern: the client declares elicitation, but the connection has no
    # back-channel for server requests, so the prompt cannot be sent.
    elicit = answer("accept", True) if modern else None

    async def go():
        async with connect(build(), elicit, modern=modern) as c:
            args = {"repo": "acme/api", "branch": "old"}
            first = await c.call_tool("delete_branch", args)
            tok = _token(text(first))
            wrong = await c.call_tool("delete_branch", {**args, "branch": "main", "confirm_token": tok})
            ok = await c.call_tool("delete_branch", {**args, "confirm_token": tok})
            again = await c.call_tool("delete_branch", {**args, "confirm_token": tok})
            return first, wrong, ok, again

    first, wrong, ok, again = run(go())
    assert is_error(first) and "Confirmation required" in text(first)
    assert "Delete branch old in acme/api?" in text(first)
    assert is_error(wrong) and "different call" in text(wrong)
    assert not is_error(ok) and text(ok) == "deleted old"
    assert is_error(again) and "already used" in text(again)
    assert DELETED == ["old"]


def test_mode_elicit_refuses_on_clients_without_prompt():
    async def go():
        async with connect(build(mode="elicit")) as c:
            return await c.call_tool("delete_branch", {"repo": "a/b", "branch": "x"})

    r = run(go())
    assert is_error(r) and "cannot ask for it" in text(r)
    assert DELETED == []


def test_read_only_mode_removes_and_blocks_tools():
    srv = build()
    assert restrict(srv, read_only=True) == ["delete_branch", "unannotated"]

    async def go():
        async with connect(srv) as c:
            listed = await c.list_tools()
            call = await c.call_tool("delete_branch", {"repo": "a/b", "branch": "x"})
            return [t.name for t in listed.tools], call

    names, call = run(go())
    assert names == ["list_branches"]
    assert is_error(call)
    assert DELETED == []
