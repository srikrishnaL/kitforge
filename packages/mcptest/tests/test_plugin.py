import sys

from conftest import FAKE

CMD = f'"{sys.executable}" "{FAKE}"'


def test_ini_configured_fixture(pytester):
    pytester.makeini(f"[pytest]\nmcp_command = {CMD}\n")
    pytester.makepyfile(
        """
        from mcptest import assert_lint_clean

        def test_add(mcp):
            assert mcp.call_tool("add", {"a": 1, "b": 2}).text == "3"

        def test_lint(mcp):
            assert_lint_clean(mcp)
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)


def test_marker_configured_fixture(pytester):
    pytester.makepyfile(
        f"""
        import sys, pytest

        @pytest.mark.mcp_server(command=[sys.executable, {FAKE!r}], env={{"FAKE_TOOLS": "many"}})
        def test_many(mcp):
            assert len(mcp.list_tools()) == 25

        def test_factory(mcp_server):
            a = mcp_server([sys.executable, {FAKE!r}])
            b = mcp_server([sys.executable, {FAKE!r}], env={{"FAKE_TOOLS": "v2"}})
            assert a.tool("add") != b.tool("add")
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)


def test_missing_config_is_a_clear_error(pytester):
    pytester.makepyfile("def test_x(mcp):\n    pass\n")
    result = pytester.runpytest()
    result.stdout.fnmatch_lines(["*needs a server*"])
    assert result.ret != 0


def test_snapshot_lifecycle(pytester):
    pytester.makepyfile(
        test_snap=f"""
        import os, sys, pytest

        @pytest.mark.mcp_server(command=[sys.executable, {FAKE!r}],
                                env={{"FAKE_TOOLS": os.environ.get("TOOLSET", "good")}})
        def test_tools(mcp, mcp_snapshot):
            mcp_snapshot(mcp)
        """
    )
    # First run writes the snapshot.
    pytester.runpytest().assert_outcomes(passed=1)
    snap_file = pytester.path / "__mcp_snapshots__" / "test_snap" / "tools.json"
    assert snap_file.exists()
    # Same server: passes.
    pytester.runpytest().assert_outcomes(passed=1)
    # Changed server: fails and names the breaking change.
    pytester._monkeypatch.setenv("TOOLSET", "v2")
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*BREAKING echo: tool was removed*", "*--mcp-update-snapshots*"])
    # Accept the change, then it passes.
    pytester.runpytest("--mcp-update-snapshots").assert_outcomes(passed=1)
    pytester.runpytest().assert_outcomes(passed=1)
    assert "upper" in snap_file.read_text()


def test_snapshot_allow_compatible(pytester):
    pytester.makepyfile(
        test_snap="""
        import json, pathlib

        TOOLS = [{"name": "a", "description": "first", "inputSchema": {"type": "object"}}]

        def test_one(mcp_snapshot):
            mcp_snapshot.assert_match(TOOLS)

        def test_two(mcp_snapshot):
            more = TOOLS + [{"name": "b", "description": "x", "inputSchema": {"type": "object"}}]
            mcp_snapshot.assert_match(TOOLS, name="other")
            mcp_snapshot.assert_match(more, name="other", allow_compatible=True)
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)
