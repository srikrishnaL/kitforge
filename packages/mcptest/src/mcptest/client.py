"""A small, dependency-free MCP client for tests.

It speaks JSON-RPC 2.0 over the two standard MCP transports:

* stdio: the server is a subprocess, one JSON message per line.
* Streamable HTTP: messages are POSTed; replies come back as JSON or as a
  server-sent event stream.

It implements only what a test needs: the initialize handshake, listing
tools, resources and prompts, and calling tools. It does not depend on any
MCP SDK, so it keeps working when an SDK release changes its API.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterator, List, Mapping, Optional, Sequence, Union

#: Protocol version sent in ``initialize``. Servers answer with the version
#: they will use; the client accepts whatever they choose.
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

CLIENT_INFO = {"name": "mcptest", "version": "0.2.0"}


class MCPError(Exception):
    """The server answered a request with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None, method: str = ""):
        self.code = code
        self.message = message
        self.data = data
        self.method = method
        where = f" ({method})" if method else ""
        super().__init__(f"MCP error {code}{where}: {message}")


class MCPTransportError(Exception):
    """The server could not be reached, exited, or sent something unreadable."""


class MCPTimeout(MCPTransportError):
    """No reply arrived within the timeout."""


@dataclass
class ToolResult:
    """The result of ``tools/call``."""

    content: List[Dict[str, Any]]
    is_error: bool = False
    structured: Optional[Any] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """All text content blocks joined by newlines."""
        return "\n".join(
            str(block.get("text", "")) for block in self.content if block.get("type") == "text"
        )

    def json(self) -> Any:
        """Structured content if the server sent it, else the text parsed as JSON."""
        if self.structured is not None:
            return self.structured
        return json.loads(self.text)


# --------------------------------------------------------------------------- transports


class _Transport:
    def request(self, message: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        raise NotImplementedError

    def notify(self, message: Dict[str, Any]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def diagnostics(self) -> str:
        return ""


class StdioTransport(_Transport):
    """Run the server as a subprocess and talk to it over stdin/stdout."""

    def __init__(
        self,
        command: Union[str, Sequence[str]],
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv:
            raise ValueError("empty server command")
        self.argv = argv
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        full_env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                cwd=cwd,
                bufsize=0,
            )
        except OSError as exc:
            raise MCPTransportError(f"could not start {argv[0]!r}: {exc}") from exc
        self._inbox: queue.Queue[Optional[Dict[str, Any]]] = queue.Queue()
        self._stderr: Deque[str] = deque(maxlen=200)
        self._junk: Deque[str] = deque(maxlen=20)
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for raw in iter(self._proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                # Something printed to stdout that isn't protocol: a classic bug.
                self._junk.append(line)
                continue
            if isinstance(msg, list):  # a JSON-RPC batch
                for item in msg:
                    self._inbox.put(item)
            else:
                self._inbox.put(msg)
        self._inbox.put(None)  # EOF marker

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip("\n"))

    def _write(self, message: Dict[str, Any]) -> None:
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPTransportError(
                    f"server exited (code {self._proc.poll()}){self.diagnostics()}"
                ) from exc

    def notify(self, message: Dict[str, Any]) -> None:
        self._write(message)

    def request(self, message: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        self._write(message)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise MCPTimeout(
                    f"no reply to {message.get('method')!r} within {timeout:g}s{self.diagnostics()}"
                )
            try:
                msg = self._inbox.get(timeout=left)
            except queue.Empty:
                continue
            if msg is None:
                self._inbox.put(None)  # keep EOF visible to later calls
                # Give the process a moment so the exit code is known.
                try:
                    self._proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                raise MCPTransportError(
                    f"server closed stdout (exit code {self._proc.poll()}){self.diagnostics()}"
                )
            if _is_server_request(msg):
                self._write(_answer_server_request(msg))
                continue
            if msg.get("id") == message["id"] and ("result" in msg or "error" in msg):
                return msg
            # Notifications (logging, progress) and stray replies are ignored.

    def diagnostics(self) -> str:
        parts = []
        if self._junk:
            parts.append(
                "\n  non-JSON lines on stdout (log to stderr instead):\n    "
                + "\n    ".join(list(self._junk)[-5:])
            )
        if self._stderr:
            parts.append("\n  server stderr (last lines):\n    " + "\n    ".join(list(self._stderr)[-15:]))
        return "".join(parts)

    @property
    def stderr(self) -> str:
        return "\n".join(self._stderr)

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


class HttpTransport(_Transport):
    """Talk to a server over MCP Streamable HTTP."""

    def __init__(self, url: str, headers: Optional[Mapping[str, str]] = None):
        self.url = url
        self.headers = dict(headers or {})
        self.session_id: Optional[str] = None
        self.protocol_version: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        h.update(self.headers)
        return h

    def _post(self, message: Dict[str, Any], timeout: float):
        body = json.dumps(message).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise MCPTransportError(f"HTTP {exc.code} from {self.url}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise MCPTransportError(f"cannot reach {self.url}: {exc}") from exc

    def notify(self, message: Dict[str, Any]) -> None:
        resp = self._post(message, timeout=30)
        resp.close()

    def request(self, message: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        resp = self._post(message, timeout)
        try:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "text/event-stream":
                for msg in _iter_sse_messages(resp):
                    if _is_server_request(msg):
                        self._post(_answer_server_request(msg), timeout).close()
                        continue
                    if msg.get("id") == message["id"] and ("result" in msg or "error" in msg):
                        return msg
                raise MCPTransportError(
                    f"event stream ended without a reply to {message.get('method')!r}"
                )
            raw = resp.read().decode("utf-8")
        finally:
            resp.close()
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise MCPTransportError(f"reply is not JSON: {raw[:200]!r}") from exc
        if isinstance(data, list):
            for item in data:
                if item.get("id") == message["id"]:
                    return item
            raise MCPTransportError("batch reply did not contain our id")
        return data

    def close(self) -> None:
        if not self.session_id:
            return
        req = urllib.request.Request(self.url, headers=self._headers(), method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001 - closing is best effort
            pass


def _iter_sse_messages(resp) -> Iterator[Dict[str, Any]]:
    data_lines: List[str] = []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    msg = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(msg, list):
                    yield from msg
                else:
                    yield msg
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if data_lines:
        try:
            yield json.loads("\n".join(data_lines))
        except ValueError:
            pass


def _is_server_request(msg: Dict[str, Any]) -> bool:
    return "method" in msg and "id" in msg and "result" not in msg and "error" not in msg


def _answer_server_request(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Reply to a request the server sent us. We support ping and roots only."""
    method = msg.get("method")
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
    if method == "roots/list":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {"roots": []}}
    return {
        "jsonrpc": "2.0",
        "id": msg["id"],
        "error": {"code": -32601, "message": f"mcptest does not support {method}"},
    }


# --------------------------------------------------------------------------- client


class MCPClient:
    """A synchronous MCP client for tests and CI checks.

    Use :meth:`stdio` or :meth:`http` to create one, ideally as a context
    manager so the server is always shut down::

        with MCPClient.stdio(["python", "server.py"]) as client:
            tools = client.list_tools()
            result = client.call_tool("add", {"a": 2, "b": 3})
            assert result.text == "5"
    """

    def __init__(
        self,
        transport: _Transport,
        *,
        timeout: float = 30.0,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    ):
        self._transport = transport
        self.timeout = timeout
        self.requested_protocol_version = protocol_version
        self._ids = itertools.count(1)
        self.initialized = False
        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}
        self.protocol_version: Optional[str] = None
        self.instructions: Optional[str] = None

    # -- constructors -----------------------------------------------------

    @classmethod
    def stdio(
        cls,
        command: Union[str, Sequence[str]],
        *,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        initialize: bool = True,
    ) -> MCPClient:
        """Start ``command`` as a subprocess and connect to it over stdio."""
        client = cls(StdioTransport(command, env=env, cwd=cwd), timeout=timeout,
                     protocol_version=protocol_version)
        if initialize:
            try:
                client.initialize()
            except BaseException:
                client.close()
                raise
        return client

    @classmethod
    def http(
        cls,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        timeout: float = 30.0,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        initialize: bool = True,
    ) -> MCPClient:
        """Connect to a running server over Streamable HTTP."""
        client = cls(HttpTransport(url, headers=headers), timeout=timeout,
                     protocol_version=protocol_version)
        if initialize:
            try:
                client.initialize()
            except BaseException:
                client.close()
                raise
        return client

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    @property
    def stderr(self) -> str:
        """What a stdio server wrote to stderr so far (empty for HTTP)."""
        return getattr(self._transport, "stderr", "")

    # -- raw protocol -----------------------------------------------------

    def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                *, timeout: Optional[float] = None) -> Any:
        """Send a JSON-RPC request and return its ``result``.

        Raises :class:`MCPError` if the server answers with an error.
        """
        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            message["params"] = params
        reply = self._transport.request(message, self.timeout if timeout is None else timeout)
        if "error" in reply:
            err = reply["error"] or {}
            raise MCPError(int(err.get("code", 0)), str(err.get("message", "")), err.get("data"), method)
        return reply.get("result")

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._transport.notify(message)

    def initialize(self) -> Dict[str, Any]:
        """Run the MCP handshake. Called for you by :meth:`stdio` and :meth:`http`."""
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.requested_protocol_version,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": CLIENT_INFO,
            },
        ) or {}
        self.server_info = result.get("serverInfo") or {}
        self.capabilities = result.get("capabilities") or {}
        self.protocol_version = result.get("protocolVersion")
        self.instructions = result.get("instructions")
        if isinstance(self._transport, HttpTransport):
            self._transport.protocol_version = self.protocol_version
        self.notify("notifications/initialized")
        self.initialized = True
        return result

    def ping(self) -> None:
        self.request("ping")

    # -- listing ----------------------------------------------------------

    def _paginate(self, method: str, key: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        seen = set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request(method, params) or {}
            items.extend(result.get(key) or [])
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen:
                return items
            seen.add(cursor)

    def list_tools(self) -> List[Dict[str, Any]]:
        """All tools, following pagination."""
        return self._paginate("tools/list", "tools")

    def list_resources(self) -> List[Dict[str, Any]]:
        if "resources" not in self.capabilities:
            return []
        return self._paginate("resources/list", "resources")

    def list_prompts(self) -> List[Dict[str, Any]]:
        if "prompts" not in self.capabilities:
            return []
        return self._paginate("prompts/list", "prompts")

    def tool(self, name: str) -> Dict[str, Any]:
        """The definition of one tool. Raises ``KeyError`` if it doesn't exist."""
        for t in self.list_tools():
            if t.get("name") == name:
                return t
        raise KeyError(name)

    # -- calling ----------------------------------------------------------

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                  *, timeout: Optional[float] = None) -> ToolResult:
        """Call a tool. A tool-level failure comes back with ``is_error=True``."""
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        ) or {}
        return ToolResult(
            content=list(result.get("content") or []),
            is_error=bool(result.get("isError", False)),
            structured=result.get("structuredContent"),
            raw=result,
        )

    def read_resource(self, uri: str) -> List[Dict[str, Any]]:
        result = self.request("resources/read", {"uri": uri}) or {}
        return list(result.get("contents") or [])

    def get_prompt(self, name: str, arguments: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return self.request("prompts/get", {"name": name, "arguments": arguments or {}}) or {}
