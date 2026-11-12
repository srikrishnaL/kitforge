"""End-to-end tests through a real MCP Python SDK server and in-memory client.

They run with whichever SDK is installed: 1.x (FastMCP) or 2.x (MCPServer).
"""

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager

import pytest
from fakedt import KEY, FakeDT
from mcptest import assert_lint_clean

from bomscout import DependencyTrack, Scout
from bomscout.server import build_server, check, main

try:  # SDK 2.x
    from mcp import Client

    V2 = True
except ImportError:  # SDK 1.x
    from mcp.shared.memory import create_connected_server_and_client_session

    V2 = False

READ_TOOLS = {"portfolio_overview", "list_projects", "project_findings", "vulnerability_impact",
              "project_components", "project_licenses", "find_component", "policy_violations"}


@asynccontextmanager
async def connect(srv):
    if V2:
        async with Client(srv) as c:
            yield c
    else:
        async with create_connected_server_and_client_session(srv._mcp_server) as c:
            yield c


def server(fake=None, **kw):
    fake = fake or FakeDT()
    scout = Scout(DependencyTrack("https://dtrack.example.com", KEY, transport=fake))
    return build_server(scout, **kw)


def call(srv, name, args=None):
    async def go():
        async with connect(srv) as c:
            return await c.call_tool(name, args or {})

    return asyncio.run(go())


def list_tools(srv):
    async def go():
        async with connect(srv) as c:
            return (await c.list_tools()).tools

    return asyncio.run(go())


def is_error(result):
    return result.is_error if V2 else result.isError


def as_dict(tool):
    return tool.model_dump(by_alias=True, exclude_none=True)


def test_read_only_by_default():
    tools = list_tools(server(env={}))
    assert {t.name for t in tools} == READ_TOOLS
    for t in tools:
        ann = t.annotations
        assert (getattr(ann, "read_only_hint", None) or getattr(ann, "readOnlyHint", None)) is True


def test_write_tool_is_opt_in():
    assert "record_analysis" in {t.name for t in list_tools(server(env={"BOMSCOUT_ALLOW_WRITE": "1"}))}
    assert "record_analysis" in {t.name for t in list_tools(server(allow_write=True, env={}))}
    assert "record_analysis" not in {t.name for t in list_tools(server(env={"BOMSCOUT_ALLOW_WRITE": "no"}))}


def test_tools_pass_mcptest_lint():
    tools = list_tools(server(allow_write=True))
    assert_lint_clean([as_dict(t) for t in tools], fail_on="warning")


def test_tool_call_returns_compact_json():
    r = call(server(), "project_findings", {"project": "shop", "min_severity": "HIGH"})
    assert not is_error(r)
    assert len(r.content) == 1
    assert (getattr(r, "structured_content", None) or getattr(r, "structuredContent", None)) is None
    data = json.loads(r.content[0].text)
    assert data["findings"][0]["vuln"] == "CVE-2021-44228"


def test_errors_reach_the_model():
    fake = FakeDT(perms={"VIEW_PORTFOLIO"})
    r = call(server(fake), "project_findings", {"project": "shop"})
    assert is_error(r)
    assert "VIEW_VULNERABILITY permission" in r.content[0].text


def test_unconfigured_server_still_lists_tools_and_explains():
    srv = build_server(env={})
    assert len(list_tools(srv)) == len(READ_TOOLS)
    r = call(srv, "list_projects")
    assert is_error(r) and "set DT_URL" in r.content[0].text


def test_record_analysis_through_mcp():
    fake = FakeDT()
    r = call(server(fake, allow_write=True), "record_analysis", {
        "project": "shop", "component_uuid": "c", "vulnerability_uuid": "v",
        "state": "FALSE_POSITIVE", "comment": "Not used"})
    assert not is_error(r), r.content[0].text
    assert fake.puts[0]["analysisState"] == "FALSE_POSITIVE" and "isSuppressed" not in fake.puts[0]


def test_check_command(capsys, monkeypatch):
    fake = FakeDT(perms={"VIEW_PORTFOLIO", "VIEW_VULNERABILITY"})
    real = DependencyTrack.__init__

    def init(self, url, key, **kw):
        kw["transport"] = fake
        real(self, url, key, **kw)

    monkeypatch.setattr(DependencyTrack, "__init__", init)
    code = check({"DT_URL": "https://dtrack.example.com", "DT_API_KEY": KEY})
    out = capsys.readouterr().out
    assert code == 1
    assert "Dependency-Track 4.14.6" in out and "ok    VIEW_PORTFOLIO" in out
    assert "FAIL  VIEW_POLICY_VIOLATION" in out


def test_cli_version():
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0


def test_stdio_server_starts_and_lists_tools():
    """The installed `python -m bomscout` speaks MCP over stdio (what clients run)."""
    from mcptest import MCPClient

    with MCPClient.stdio([sys.executable, "-m", "bomscout"], env={"DT_URL": "", "DT_API_KEY": ""}) as c:
        names = {t["name"] for t in c.list_tools()}
    assert names == READ_TOOLS


def test_module_help():
    out = subprocess.run([sys.executable, "-m", "bomscout", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "check" in out.stdout
