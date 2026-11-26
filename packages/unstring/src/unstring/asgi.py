"""ASGI middleware for Streamable HTTP MCP servers and proxies.

Wrap the server's ASGI app::

    from unstring.asgi import Unstring
    app = Unstring(mcp.streamable_http_app())

The middleware reads each POST body. ``tools/call`` requests get their ``arguments`` repaired
against the tool's ``inputSchema`` before the app sees them. It learns the schemas from the
``tools/list`` responses that pass through (``application/json`` or SSE). When a call names a
tool it has not seen, it asks the app for the tool list itself, with the caller's own headers
(session id, authorization), and repairs the call once the answer is in. The client never sees
that request. Everything else (GET streams, DELETE, other paths, responses) passes through
untouched.

The middleware waits with ``asyncio``, so it runs under asyncio servers such as uvicorn and
hypercorn's default worker.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, TextIO, Tuple
from urllib.parse import urlsplit

from .core import repair

Scope = Dict[str, Any]
Message = Dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]

LIST_ID_PREFIX = "unstring-tools-"
RETRY_AFTER = 30.0  # seconds before asking again for a tool the server did not list
MAX_PAGES = 50
_EVENT_END = re.compile(rb"\r\n\r\n|\n\n|\r\r")


class Unstring:
    """Repair the arguments of every ``tools/call`` that reaches ``app``.

    ``tools`` preloads schemas (a ``tools/list`` result, a list of tools, or a mapping of tool
    name to ``inputSchema``), so the middleware never has to ask. ``path`` limits it to one URL
    path. ``wrap``, ``quiet`` and ``log`` work as in ``unstring proxy``. ``fetch=False`` turns
    off the middleware's own ``tools/list`` requests: then only listed tools are repaired.
    """

    def __init__(self, app: App, *, tools: Any = None, wrap: bool = False, quiet: bool = False,
                 log: Optional[TextIO] = None, path: Optional[str] = None,
                 fetch: bool = True) -> None:
        self.app = app
        self.wrap = wrap
        self.quiet = quiet
        self.log = log
        self.path = path.rstrip("/") if path else None
        self.fetch = fetch
        self.schemas: Dict[str, Any] = {}
        self.fixed_calls = 0
        self._missing: Dict[str, float] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._next = 0
        if tools is not None:
            self.add_tools(tools)

    def add_tools(self, tools: Any) -> None:
        """Learn schemas from a ``tools/list`` result, a list of tools, or {name: inputSchema}."""
        if isinstance(tools, dict) and isinstance(tools.get("result"), dict):
            tools = tools["result"]
        if isinstance(tools, dict) and isinstance(tools.get("tools"), list):
            tools = tools["tools"]
        if isinstance(tools, dict):
            self.schemas.update({str(k): v for k, v in tools.items()})
            return
        for tool in tools or []:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                self.schemas[tool["name"]] = tool.get("inputSchema")
                self._missing.pop(tool["name"], None)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or (self.path is not None and scope.get("path", "").rstrip("/") != self.path)):
            await self.app(scope, receive, send)
            return

        body, early = await _read_body(receive)
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        items = parsed if isinstance(parsed, list) else [parsed]
        messages = [m for m in items if isinstance(m, dict)]
        calls = [m for m in messages if m.get("method") == "tools/call"
                 and isinstance(m.get("params"), dict) and "arguments" in m["params"]]
        lists = {_key(m["id"]) for m in messages if m.get("method") == "tools/list" and "id" in m}

        if calls:
            unknown = [c["params"].get("name") for c in calls
                       if c["params"].get("name") not in self.schemas]
            if unknown and self.fetch and any(self._may_ask(n) for n in unknown):
                await self._fetch_tools(scope, calls[0])
            changed = False
            new_items = []
            for item in items:
                new = self._fix(item) if any(item is c for c in calls) else item
                changed = changed or new is not item
                new_items.append(new)
            if changed:
                out = new_items if isinstance(parsed, list) else new_items[0]
                body = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                scope = dict(scope, headers=_with_length(scope.get("headers", []), len(body)))

        replay = _replayer(body, early, receive)
        if lists:
            await self.app(scope, replay, _Watcher(lists, self.add_tools, send))
        else:
            await self.app(scope, replay, send)

    # -- the middleware's own tools/list ------------------------------------------------

    def _may_ask(self, name: Any) -> bool:
        asked = self._missing.get(name) if isinstance(name, str) else None
        return asked is None or time.monotonic() - asked > RETRY_AFTER

    async def _fetch_tools(self, scope: Scope, call: dict) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            name = call["params"].get("name")
            if name in self.schemas or not self._may_ask(name):
                return  # another request fetched the list while this one waited
            found: List[dict] = []
            cursor: Optional[str] = None
            error: Optional[str] = None
            for _ in range(MAX_PAGES):
                reply, error = await self._ask(scope, call, cursor)
                result = reply.get("result") if reply else None
                if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                    error = error or json.dumps((reply or {}).get("error"))[:200]
                    break
                found += [t for t in result["tools"] if isinstance(t, dict)]
                cursor = result.get("nextCursor")
                if not isinstance(cursor, str) or not cursor:
                    break
            self.add_tools(found)
            if name not in self.schemas and isinstance(name, str):
                self._missing[name] = time.monotonic()
                if error and not self.quiet:
                    self._say(f"the server did not list its tools ({error}); "
                              f"passing {name} on as it is")

    async def _ask(self, scope: Scope, call: dict,
                   cursor: Optional[str]) -> Tuple[Optional[dict], Optional[str]]:
        reply, error, location = await self._ask_once(scope, call, cursor)
        if location is not None:  # e.g. /mcp -> /mcp/ (MCP SDK 1.x): follow it once
            path = urlsplit(location).path
            if path and path != scope.get("path"):
                scope = dict(scope, path=path, raw_path=path.encode("latin-1"))
                reply, error, _ = await self._ask_once(scope, call, cursor)
        return reply, error

    async def _ask_once(self, scope: Scope, call: dict, cursor: Optional[str]
                        ) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
        self._next += 1
        rid = f"{LIST_ID_PREFIX}{self._next}"
        params: Dict[str, Any] = {"cursor": cursor} if cursor else {}
        meta = call["params"].get("_meta")
        if isinstance(meta, dict):  # 2026-07-28 clients send the protocol version here
            keep = {k: v for k, v in meta.items() if k.startswith("io.modelcontextprotocol/")}
            if keep:
                params["_meta"] = keep
        req = {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": params}
        body = json.dumps(req, separators=(",", ":")).encode("utf-8")
        inner = dict(scope, headers=_with_length(scope.get("headers", []), len(body)))
        done = asyncio.Event()
        sent = False

        async def receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await done.wait()
            return {"type": "http.disconnect"}

        collector = _Collector(rid, done)
        try:
            await asyncio.wait_for(self.app(inner, receive, collector), 30)
        except asyncio.TimeoutError:
            if collector.reply is None:
                return None, "no answer within 30 seconds", None
        except Exception as exc:  # the app failed; the real call will show the same error
            if collector.reply is None:
                return None, f"{type(exc).__name__}: {exc}"[:200], None
        finally:
            done.set()
        if collector.reply is not None:
            return collector.reply, None, None
        if collector.status in (307, 308) and collector.location:
            return None, f"HTTP {collector.status}", collector.location
        return None, f"HTTP {collector.status}" if collector.status != 200 else "no reply", None

    # -- repair -------------------------------------------------------------------------

    def _fix(self, msg: dict) -> dict:
        params = msg["params"]
        name = params.get("name")
        schema = self.schemas.get(name)
        result = repair(params["arguments"], schema if schema is not None else {},
                        wrap=self.wrap)
        if not result.changed:
            return msg
        self.fixed_calls += 1
        if not self.quiet:
            self._say(f"{name}: " + "; ".join(str(f) for f in result.fixes))
        return dict(msg, params=dict(params, arguments=result.value))

    def _say(self, text: str) -> None:
        print(f"unstring: {text}", file=self.log or sys.stderr, flush=True)


# -- reading JSON-RPC out of responses ----------------------------------------------------


class _Stream:
    """Collects the JSON-RPC messages in a response body, JSON or SSE, as chunks arrive."""

    def __init__(self) -> None:
        self.kind = "raw"
        self.status = 0
        self.buf = b""

    def start(self, message: Message) -> None:
        self.status = message.get("status", 200)
        headers = {bytes(k).lower(): bytes(v) for k, v in message.get("headers", [])}
        ctype = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
        encoded = headers.get(b"content-encoding", b"identity").lower() != b"identity"
        if self.status != 200 or encoded:
            self.kind = "raw"
        elif ctype == b"application/json":
            self.kind = "json"
        elif ctype == b"text/event-stream":
            self.kind = "sse"

    def feed(self, chunk: bytes, more: bool) -> List[Any]:
        if self.kind == "raw":
            return []
        self.buf += chunk
        found: List[Any] = []
        if self.kind == "json":
            if not more:
                found += _messages(self.buf)
                self.buf = b""
            return found
        while True:
            m = _EVENT_END.search(self.buf)
            if not m:
                break
            event, self.buf = self.buf[:m.start()], self.buf[m.end():]
            found += _event(event, m.group(0))
        if not more and self.buf:
            found += _event(self.buf, b"\n\n")
            self.buf = b""
        return found


def _messages(data: bytes) -> List[Any]:
    try:
        parsed = json.loads(data)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _event(event: bytes, end: bytes) -> List[Any]:
    sep = end[: len(end) // 2]
    data = [ln[5:] for ln in event.split(sep) if ln.startswith(b"data:")]
    if not data:
        return []
    return _messages(b"\n".join(d[1:] if d.startswith(b" ") else d for d in data))


class _Watcher:
    """The ``send`` given to the app for requests that carry a ``tools/list``: passes every
    message on unchanged and learns schemas from the list results it sees."""

    def __init__(self, ids: set, learn: Callable[[Any], None], send: Send) -> None:
        self.ids = ids
        self.learn = learn
        self.send = send
        self.stream = _Stream()

    async def __call__(self, message: Message) -> None:
        mtype = message.get("type")
        if mtype == "http.response.start":
            self.stream.start(message)
        elif mtype == "http.response.body" and self.ids:
            for msg in self.stream.feed(message.get("body", b""),
                                        message.get("more_body", False)):
                if isinstance(msg, dict) and "id" in msg and _key(msg["id"]) in self.ids:
                    self.ids.discard(_key(msg["id"]))
                    result = msg.get("result")
                    if isinstance(result, dict) and isinstance(result.get("tools"), list):
                        self.learn(result)
        await self.send(message)


class _Collector:
    """The ``send`` given to the app for the middleware's own ``tools/list``."""

    def __init__(self, rid: str, done: asyncio.Event) -> None:
        self.rid = rid
        self.done = done
        self.stream = _Stream()
        self.reply: Optional[dict] = None
        self.status = 0
        self.location: Optional[str] = None

    async def __call__(self, message: Message) -> None:
        mtype = message.get("type")
        if mtype == "http.response.start":
            self.stream.start(message)
            self.status = self.stream.status
            for k, v in message.get("headers", []):
                if bytes(k).lower() == b"location":
                    self.location = bytes(v).decode("latin-1")
            if self.stream.kind == "raw":
                self.done.set()
        elif mtype == "http.response.body":
            for msg in self.stream.feed(message.get("body", b""),
                                        message.get("more_body", False)):
                if isinstance(msg, dict) and msg.get("id") == self.rid and "method" not in msg:
                    self.reply = msg
                    self.done.set()  # lets an SSE stream end: the request "disconnects"
            if not message.get("more_body", False):
                self.done.set()


# -- ASGI helpers -------------------------------------------------------------------------


async def _read_body(receive: Receive) -> Tuple[bytes, Optional[Message]]:
    chunks = []
    while True:
        msg = await receive()
        if msg.get("type") != "http.request":
            return b"".join(chunks), msg  # e.g. http.disconnect: hand it on after the body
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body", False):
            return b"".join(chunks), None


def _replayer(body: bytes, early: Optional[Message], receive: Receive) -> Receive:
    queue: List[Message] = [{"type": "http.request", "body": body, "more_body": False}]
    if early is not None:
        queue.append(early)

    async def replay() -> Message:
        if queue:
            return queue.pop(0)
        return await receive()

    return replay


def _with_length(headers: Any, length: int) -> List[Tuple[bytes, bytes]]:
    out = [(bytes(k), bytes(v)) for k, v in headers if bytes(k).lower() != b"content-length"]
    out.append((b"content-length", str(length).encode()))
    return out


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests
