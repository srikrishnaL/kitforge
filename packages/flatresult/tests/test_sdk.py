"""End-to-end tests with real MCP Python SDK servers and clients (whichever SDK is installed).

The MCP SDK needs Python 3.10+, so on 3.9 only the SDK-free tests run.
"""

import asyncio
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SERVER = str(Path(__file__).parent / "servers" / "sdk_server.py")
PARAMS = StdioServerParameters(command=sys.executable, args=[
    "-m", "flatresult", "proxy", "--quiet", "--", sys.executable, SERVER])
FRAME_TEXT = ('Resource: frame-1 <file:///design/frame-1.json> (application/json)\n\n'
              '{"width": 320, "height": 640}')


def dump(result):
    return [c.model_dump(exclude_none=True) for c in result.content]


def test_session_client_through_the_proxy():
    async def go():
        async with stdio_client(PARAMS) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (dump(await session.call_tool("get_design", {})),
                        dump(await session.call_tool("get_notes", {})))

    design, notes = asyncio.run(go())
    assert design == [{"type": "text", "text": FRAME_TEXT}]
    assert notes == [{"type": "text", "text": "file:///notes.md (text/markdown):\n# Notes"}]


def test_modern_client_through_the_proxy():
    """SDK 2.x's Client speaks 2026-07-28: no initialize, an envelope in every request."""
    client_mod = pytest.importorskip("mcp.client.client")

    async def go():
        async with client_mod.Client(PARAMS) as client:
            return dump(await client.call_tool("get_design", {}))

    assert asyncio.run(go()) == [{"type": "text", "text": FRAME_TEXT}]
