"""The official MCP Python SDK's stdio client, talking through cleanpipe to an SDK server that
print()s to stdout. Runs with whichever SDK is installed (1.x or 2.x); needs Python 3.10+."""

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

SERVER = str(Path(__file__).parent / "servers" / "sdk_server.py")


def test_sdk_client_through_cleanpipe(tmp_path):
    log = tmp_path / "noise.log"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cleanpipe", "--log", str(log), "--quiet", "--", sys.executable, SERVER])

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                result = await session.call_tool("add", {"a": 2, "b": 3})
                return tools, result

    tools, result = asyncio.run(asyncio.wait_for(go(), 60))
    assert [t.name for t in tools] == ["add"]
    assert result.content[0].text == "5"
    # SDK 2.x keeps print() inside tools off stdout by itself; the import-time banner still lands there.
    assert "Loading demo server..." in log.read_text()


def test_check_finds_the_print_calls():
    from cleanpipe import check

    report = check([sys.executable, SERVER], timeout=30)
    assert report.handshake and report.tools == 1
    assert report.noise == ["Loading demo server..."]
