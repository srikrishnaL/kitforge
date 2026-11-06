"""pytest plugin: fixtures for testing MCP servers.

Installed automatically with mcptest. Configure the server once, in
``pytest.ini`` / ``pyproject.toml``::

    [tool.pytest.ini_options]
    mcp_command = "python -m my_server"

or per test with a marker::

    @pytest.mark.mcp_server(command=["node", "dist/index.js"])
    def test_tools(mcp):
        ...
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from . import snapshot as snap
from .client import MCPClient


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mcptest")
    group.addoption(
        "--mcp-update-snapshots",
        action="store_true",
        default=False,
        help="rewrite MCP tool snapshots instead of failing when they differ",
    )
    parser.addini("mcp_command", "command that starts the MCP server over stdio", default="")
    parser.addini("mcp_url", "Streamable HTTP URL of a running MCP server", default="")
    parser.addini("mcp_timeout", "seconds to wait for each MCP reply", default="30")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "mcp_server(command=None, url=None, env=None, cwd=None, headers=None, timeout=None): "
        "the MCP server the `mcp` fixture connects to",
    )


class _Factory:
    def __init__(self, config: pytest.Config):
        self._config = config
        self._clients: List[MCPClient] = []

    def __call__(
        self,
        command: Any = None,
        *,
        url: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> MCPClient:
        t = float(timeout if timeout is not None else self._config.getini("mcp_timeout") or 30)
        if command and url:
            raise pytest.UsageError("give mcp_server a command or a url, not both")
        if url:
            client = MCPClient.http(url, headers=headers, timeout=t)
        elif command:
            client = MCPClient.stdio(command, env=env, cwd=cwd, timeout=t)
        else:
            raise pytest.UsageError("mcp_server needs a command or a url")
        self._clients.append(client)
        return client

    def close(self) -> None:
        while self._clients:
            self._clients.pop().close()


@pytest.fixture
def mcp_server(request: pytest.FixtureRequest):
    """Factory: ``mcp_server(["python", "server.py"])`` returns a connected client.

    Every client it creates is shut down when the test ends.
    """
    factory = _Factory(request.config)
    yield factory
    factory.close()


@pytest.fixture
def mcp(request: pytest.FixtureRequest, mcp_server: _Factory) -> MCPClient:
    """A client connected to the server from the ``mcp_server`` marker or ini settings."""
    marker = request.node.get_closest_marker("mcp_server")
    if marker is not None:
        kwargs = dict(marker.kwargs)
        if marker.args:
            kwargs.setdefault("command", marker.args[0])
        return mcp_server(**kwargs)
    config = request.config
    url = config.getini("mcp_url")
    command = config.getini("mcp_command")
    if url:
        return mcp_server(url=url)
    if command:
        return mcp_server(shlex.split(command), cwd=str(config.rootpath))
    raise pytest.UsageError(
        "the `mcp` fixture needs a server: set mcp_command or mcp_url in your pytest "
        "config, or mark the test with @pytest.mark.mcp_server(command=[...])"
    )


class SnapshotAssertion:
    """Compares a server's tools to a JSON snapshot stored next to the test."""

    def __init__(self, request: pytest.FixtureRequest):
        self._request = request
        self._update = bool(request.config.getoption("--mcp-update-snapshots"))
        test_path = Path(str(request.node.fspath))
        self.directory = test_path.parent / "__mcp_snapshots__" / test_path.stem

    def path(self, name: str) -> Path:
        return self.directory / f"{name}.json"

    def assert_match(self, source: Any, name: str = "tools", *, allow_compatible: bool = False) -> None:
        """Fail if the tools differ from the stored snapshot.

        ``source`` is an :class:`MCPClient` or a tools list. The first run
        writes the snapshot. With ``allow_compatible=True`` only breaking
        changes fail (the snapshot is then left as it was).
        """
        tools = source.list_tools() if isinstance(source, MCPClient) else source
        current = snap.normalize(tools)
        path = self.path(name)
        if not path.exists() or self._update:
            snap.save(path, current)
            return
        stored = snap.load(path)
        if snap.dumps(stored) == snap.dumps(current):
            return
        changes = snap.diff(stored, current)
        failing = snap.breaking(changes) if allow_compatible else changes
        if not failing:
            return
        lines = "\n  ".join(str(c) for c in changes) or "(schema details changed)"
        n_break = len(snap.breaking(changes))
        pytest.fail(
            f"MCP tools differ from snapshot {path.name} "
            f"({n_break} breaking, {len(changes) - n_break} compatible):\n  {lines}\n"
            "Run pytest --mcp-update-snapshots to accept these changes.",
            pytrace=False,
        )

    __call__ = assert_match


@pytest.fixture
def mcp_snapshot(request: pytest.FixtureRequest) -> SnapshotAssertion:
    """``mcp_snapshot(mcp)`` checks the server's tools against a stored snapshot."""
    return SnapshotAssertion(request)
