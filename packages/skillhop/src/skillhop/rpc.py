"""A small MCP client: stdio and Streamable HTTP, modern (2026-07-28) and legacy servers.

Only what skillhop needs: discover/initialize, skills/list, skills/get, resources/read and
resources/directory/read. Standard library only.
"""

from __future__ import annotations

import base64
import collections
import itertools
import json
import os
import queue
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from . import __version__

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
SKILLS_EXTENSION = "io.modelcontextprotocol/skills"

UNSUPPORTED_PROTOCOL_VERSION = -32022
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
HEADER_MISMATCH = -32020
_MODERN_ERRORS = {UNSUPPORTED_PROTOCOL_VERSION, MISSING_REQUIRED_CLIENT_CAPABILITY, HEADER_MISMATCH}

CLIENT_INFO = {"name": "skillhop", "version": __version__}
CLIENT_CAPABILITIES: Dict[str, Any] = {"extensions": {SKILLS_EXTENSION: {}}}


class McpError(Exception):
    """A JSON-RPC error returned by the server."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message
        self.data = data


class TransportError(Exception):
    """The server could not be reached, died, or sent something that is not MCP."""


def _raise_for_error(resp: Dict[str, Any]) -> Any:
    if "error" in resp:
        err = resp["error"] or {}
        raise McpError(int(err.get("code", 0)), str(err.get("message", "error")), err.get("data"))
    if "result" not in resp:
        raise TransportError(f"response without result or error: {json.dumps(resp)[:200]}")
    return resp["result"]


def _is_message(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("jsonrpc") == "2.0"


class StdioTransport:
    """Runs the server as a child process and speaks newline-delimited JSON-RPC.

    Lines on the server's stdout that are not JSON-RPC (banners, logs) are skipped, so a
    server with a noisy stdout still works.
    """

    def __init__(self, argv: Sequence[str], env: Optional[Dict[str, str]] = None,
                 cwd: Optional[str] = None, timeout: float = 30.0, show_stderr: bool = False):
        if not argv:
            raise TransportError("empty server command")
        exe = shutil.which(argv[0]) or argv[0]
        full_env = dict(os.environ)
        full_env.update(env or {})
        self.timeout = timeout
        self.stderr_tail: collections.deque = collections.deque(maxlen=20)
        try:
            self.proc = subprocess.Popen(
                [exe, *argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None if show_stderr else subprocess.PIPE, env=full_env, cwd=cwd,
            )
        except OSError as exc:
            raise TransportError(f"cannot start {argv[0]!r}: {exc}") from exc
        self._inbox: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        if not show_stderr:
            threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                continue  # stdout pollution: not a JSON-RPC message
            items = msg if isinstance(msg, list) else [msg]
            for item in items:
                if _is_message(item):
                    self._inbox.put(item)
        self._inbox.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    def _write(self, msg: Dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise TransportError(self._died("server closed its input")) from exc

    def _died(self, what: str) -> str:
        tail = "\n".join(list(self.stderr_tail)[-5:])
        code = self.proc.poll()
        msg = what + (f" (exit code {code})" if code is not None else "")
        return msg + (f"; last stderr lines:\n{tail}" if tail else "")

    def request(self, msg: Dict[str, Any], protocol_version: Optional[str] = None,
                timeout: Optional[float] = None) -> Dict[str, Any]:
        self._write(msg)
        wait = self.timeout if timeout is None else timeout
        while True:
            try:
                item = self._inbox.get(timeout=wait)
            except queue.Empty:
                raise TransportError(f"no answer to {msg['method']} within {wait:g}s") from None
            if item is None:
                self._inbox.put(None)
                raise TransportError(self._died(f"server exited before answering {msg['method']}"))
            if "method" in item:
                if "id" in item:  # a server-to-client request (legacy): ping or unsupported
                    if item["method"] == "ping":
                        self._write({"jsonrpc": "2.0", "id": item["id"], "result": {}})
                    else:
                        self._write({"jsonrpc": "2.0", "id": item["id"],
                                     "error": {"code": -32601, "message": "not supported by skillhop"}})
                continue  # notifications are ignored
            if item.get("id") == msg.get("id"):
                return item

    def notify(self, msg: Dict[str, Any], protocol_version: Optional[str] = None) -> None:
        self._write(msg)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def header_value(value: str) -> str:
    """Encode a value for Mcp-Name and similar headers (base64 sentinel when not plain ASCII)."""
    plain = (
        all(0x20 <= ord(c) <= 0x7E or c == "\t" for c in value)
        and value == value.strip()
        and not (value.startswith("=?base64?") and value.endswith("?="))
    )
    if plain:
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def _parse_sse(body: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    data: List[str] = []
    for line in body.splitlines() + [""]:
        if line == "":
            if data:
                try:
                    obj = json.loads("\n".join(data))
                except ValueError:
                    obj = None
                for item in obj if isinstance(obj, list) else [obj]:
                    if _is_message(item):
                        out.append(item)
            data = []
        elif line.startswith("data:"):
            data.append(line[5:][1:] if line[5:6] == " " else line[5:])
    return out


class HttpTransport:
    """Streamable HTTP: one POST per message, JSON or SSE response."""

    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session_id: Optional[str] = None  # legacy (2025-11-25) servers only

    def _post(self, msg: Dict[str, Any], protocol_version: Optional[str]):
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"skillhop/{__version__}",
            "Mcp-Method": msg.get("method", ""),
        }
        if protocol_version:
            hdrs["MCP-Protocol-Version"] = protocol_version
        params = msg.get("params") or {}
        if msg.get("method") in ("resources/read", "tools/call", "prompts/get"):
            name = params.get("uri") if "uri" in params else params.get("name")
            if isinstance(name, str):
                hdrs["Mcp-Name"] = header_value(name)
        if self.session_id:
            hdrs["Mcp-Session-Id"] = self.session_id
        hdrs.update(self.headers)
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode("utf-8"),
                                     headers=hdrs, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            status, rheaders, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, rheaders, body = exc.code, exc.headers, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"cannot reach {self.url}: {getattr(exc, 'reason', exc)}") from exc
        sid = rheaders.get("Mcp-Session-Id") if rheaders else None
        if sid:
            self.session_id = sid
        return status, (rheaders.get("Content-Type", "") if rheaders else ""), body

    def request(self, msg: Dict[str, Any], protocol_version: Optional[str] = None,
                timeout: Optional[float] = None) -> Dict[str, Any]:
        status, ctype, body = self._post(msg, protocol_version)
        text = body.decode("utf-8", "replace")
        if "text/event-stream" in ctype:
            messages = _parse_sse(text)
        else:
            try:
                obj = json.loads(text) if text.strip() else None
            except ValueError:
                obj = None
            messages = [m for m in (obj if isinstance(obj, list) else [obj]) if _is_message(m)]
        for m in messages:
            if m.get("id") == msg.get("id") and ("result" in m or "error" in m):
                return m
        if status == 401 or status == 403:
            raise TransportError(f"HTTP {status} from {self.url}: the server needs credentials "
                                 "(pass --header 'Authorization: Bearer ...')")
        raise TransportError(f"HTTP {status} from {self.url} with no JSON-RPC answer to {msg['method']}")

    def notify(self, msg: Dict[str, Any], protocol_version: Optional[str] = None) -> None:
        self._post(msg, protocol_version)

    def close(self) -> None:
        pass


class Client:
    """A connected MCP server. Use :func:`connect`."""

    def __init__(self, transport: Any, label: str):
        self.transport = transport
        self.label = label
        self.protocol_version: Optional[str] = None
        self.modern = False
        self.capabilities: Dict[str, Any] = {}
        self.server_info: Dict[str, Any] = {}
        self._ids = itertools.count(1)

    # --- low level -------------------------------------------------------------------
    def _meta(self, version: str) -> Dict[str, Any]:
        return {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": CLIENT_CAPABILITIES,
        }

    def _message(self, method: str, params: Optional[Dict[str, Any]], version: Optional[str],
                 modern: bool) -> Dict[str, Any]:
        p = dict(params or {})
        if modern and version:
            meta = dict(p.get("_meta") or {})
            meta.update(self._meta(version))
            p["_meta"] = meta
        return {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": p}

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        msg = self._message(method, params, self.protocol_version, self.modern)
        result = _raise_for_error(self.transport.request(msg, self.protocol_version))
        if not isinstance(result, dict):
            raise TransportError(f"{method} returned {type(result).__name__}, not an object")
        if result.get("resultType") == "input_required":
            raise TransportError(f"{method}: the server asked for user input, which skillhop "
                                 "cannot provide")
        return result

    # --- handshake ---------------------------------------------------------------------
    def handshake(self, discover_timeout: float = 10.0) -> None:
        msg = self._message("server/discover", {}, MODERN_VERSION, True)
        wait = min(discover_timeout, getattr(self.transport, "timeout", discover_timeout))
        try:
            resp = self.transport.request(msg, MODERN_VERSION, timeout=wait)
        except TransportError:
            if isinstance(self.transport, StdioTransport) and self.transport.proc.poll() is not None:
                raise
            resp = None  # no answer or a non-MCP error body: a legacy server
        if resp is not None and "result" in resp and isinstance(resp["result"], dict):
            result = resp["result"]
            versions = result.get("supportedVersions") or [MODERN_VERSION]
            self._use_modern(versions, result)
            return
        if resp is not None and "error" in resp:
            err = resp["error"] or {}
            if err.get("code") == UNSUPPORTED_PROTOCOL_VERSION:
                supported = (err.get("data") or {}).get("supported") or []
                modern = [v for v in supported if v >= MODERN_VERSION]
                if modern:
                    self.protocol_version = max(modern)
                    self.modern = True
                    result = self.call("server/discover")
                    self._use_modern(result.get("supportedVersions") or [self.protocol_version], result)
                    return
            elif err.get("code") in _MODERN_ERRORS:
                raise McpError(int(err["code"]), str(err.get("message")), err.get("data"))
        self._initialize()

    def _use_modern(self, versions: List[str], result: Dict[str, Any]) -> None:
        ours = [v for v in versions if v >= MODERN_VERSION]
        self.protocol_version = MODERN_VERSION if MODERN_VERSION in versions else (
            min(ours) if ours else MODERN_VERSION)
        self.modern = True
        self.capabilities = result.get("capabilities") or {}
        meta = result.get("_meta") or {}
        self.server_info = meta.get("io.modelcontextprotocol/serverInfo") or result.get("serverInfo") or {}

    def _initialize(self) -> None:
        if isinstance(self.transport, HttpTransport):
            self.transport.session_id = None
        msg = {"jsonrpc": "2.0", "id": next(self._ids), "method": "initialize", "params": {
            "protocolVersion": LEGACY_VERSION,
            "capabilities": CLIENT_CAPABILITIES,
            "clientInfo": CLIENT_INFO,
        }}
        result = _raise_for_error(self.transport.request(msg, None))
        self.protocol_version = result.get("protocolVersion") or LEGACY_VERSION
        self.modern = False
        self.capabilities = result.get("capabilities") or {}
        self.server_info = result.get("serverInfo") or {}
        self.transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"},
                              self.protocol_version)

    # --- skills ------------------------------------------------------------------------
    @property
    def skills_extension(self) -> Optional[Dict[str, Any]]:
        ext = (self.capabilities.get("extensions") or {}).get(SKILLS_EXTENSION)
        return ext if isinstance(ext, dict) else None

    def list_skills(self, max_pages: int = 1000) -> List[Dict[str, Any]]:
        skills: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        seen = set()
        for _ in range(max_pages):
            result = self.call("skills/list", {"cursor": cursor} if cursor else {})
            page = result.get("skills")
            if not isinstance(page, list):
                raise TransportError("skills/list result has no skills array")
            skills.extend(page)
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        return skills

    def get_skill(self, uri: str) -> Dict[str, Any]:
        skill = self.call("skills/get", {"uri": uri}).get("skill")
        if not isinstance(skill, dict):
            raise TransportError("skills/get result has no skill object")
        return skill

    def read_resource(self, uri: str) -> bytes:
        contents = self.call("resources/read", {"uri": uri}).get("contents")
        if not isinstance(contents, list) or not contents:
            raise TransportError(f"resources/read {uri} returned no contents")
        item = next((c for c in contents if isinstance(c, dict) and c.get("uri") == uri), contents[0])
        if "blob" in item:
            try:
                return base64.b64decode(item["blob"], validate=True)
            except ValueError as exc:
                raise TransportError(f"resources/read {uri}: invalid base64 blob") from exc
        if "text" in item:
            return str(item["text"]).encode("utf-8")
        raise TransportError(f"resources/read {uri}: contents have neither text nor blob")

    def read_directory(self, uri: str, max_pages: int = 100) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params: Dict[str, Any] = {"uri": uri}
            if cursor:
                params["cursor"] = cursor
            result = self.call("resources/directory/read", params)
            out.extend(r for r in result.get("resources") or [] if isinstance(r, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return out

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
