"""ASGI middleware that keeps Streamable HTTP MCP sessions working across server restarts.

Wrap the server's ASGI app::

    from reattach import Reattach
    app = Reattach(mcp.streamable_http_app())

On ``initialize`` the middleware hands the client a session id of its own (a token that
carries the handshake params, see :mod:`reattach.core`) and remembers which server session it
stands for. When a request comes with a token this process has not seen (the server restarted,
redeployed, or the request reached another replica), or the server answers ``404 Session not
found`` (its idle timeout ended the session), the middleware replays ``initialize`` and
``notifications/initialized`` from the token, then sends the request to the new session. The
client never sees the 404.

Requests without a session id (``initialize``, stateless servers, the 2026-07-28 protocol) and
session ids that are not tokens pass through untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .core import Tokens, handshake_params

Scope = Dict[str, Any]
Message = Dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]

SESSION = b"mcp-session-id"
VERSION = b"mcp-protocol-version"
# headers of the client's request that must not be copied into a replayed handshake
# MCP Python SDK before 1.11 answers an unknown session id with this 400 instead of a 404
_SESSION_GONE_400 = b"no valid session id"
_NOT_REPLAYED = {SESSION, VERSION, b"content-length", b"content-type", b"accept",
                 b"last-event-id", b"content-encoding", b"transfer-encoding"}


class Reattach:
    """Make MCP sessions of the wrapped Streamable HTTP app survive restarts and expiry.

    ``secret`` signs the tokens (default: the ``REATTACH_SECRET`` env var; unsigned if unset).
    Give every replica the same secret. ``path`` limits the middleware to one URL path.
    ``max_sessions`` caps the in-memory map from tokens to server sessions (oldest dropped first;
    a dropped token is simply re-attached on its next request). ``on_reattach(token, session_id)``
    is called after each replayed handshake, for logs or metrics.
    """

    def __init__(self, app: App, secret: Optional[str] = None, *, path: Optional[str] = None,
                 max_sessions: int = 10000,
                 on_reattach: Optional[Callable[[str, str], Any]] = None) -> None:
        self.app = app
        self.tokens = Tokens(secret)
        self.path = path.rstrip("/") if path else None
        self.max_sessions = max_sessions
        self.on_reattach = on_reattach
        self.sessions: "OrderedDict[Tuple[str, str], str]" = OrderedDict()
        self.reattached = 0
        self._locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        self._ids = 0

    # -- the map from (token, credential) to the server's session id ---------------------------

    def _remember(self, key: Tuple[str, str], inner: str) -> None:
        self.sessions[key] = inner
        self.sessions.move_to_end(key)
        while len(self.sessions) > self.max_sessions:
            self.sessions.popitem(last=False)

    def _lookup(self, key: Tuple[str, str]) -> Optional[str]:
        inner = self.sessions.get(key)
        if inner is not None:
            self.sessions.move_to_end(key)
        return inner

    # -- ASGI ----------------------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope.get("type") != "http"
                or (self.path is not None and scope.get("path", "").rstrip("/") != self.path)):
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        sid = _get(headers, SESSION)
        if sid is None:
            if scope.get("method") == "POST":
                await self._maybe_initialize(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        params = self.tokens.read(sid)
        if params is None:  # not one of our tokens: leave it to the server
            await self.app(scope, receive, send)
            return
        await self._serve(scope, receive, send, sid, params, headers)

    async def _maybe_initialize(self, scope: Scope, receive: Receive, send: Send) -> None:
        body, early = await _read_body(receive)
        try:
            msg = json.loads(body) if body else None
        except ValueError:
            msg = None
        params = (handshake_params(msg.get("params"))
                  if isinstance(msg, dict) and msg.get("method") == "initialize" else None)
        if params is None:
            await self.app(scope, _replayer(body, early, receive), send)
            return
        key_part = _credential(_headers(scope))
        rewriter = _HeaderRewriter(send)

        def on_session(inner: str) -> Optional[str]:
            token = self.tokens.make(params)
            if token is not None:
                self._remember((token, key_part), inner)
            return token

        rewriter.on_new = on_session
        await self.app(scope, _replayer(body, early, receive), rewriter)

    async def _serve(self, scope: Scope, receive: Receive, send: Send, sid: str,
                     params: Dict[str, Any], headers: List[Tuple[bytes, bytes]]) -> None:
        key = (sid, _credential(headers))
        body, early = await _read_body(receive)
        method = scope.get("method")
        for attempt in (0, 1):
            inner = self._lookup(key)
            fresh = False
            if inner is None:
                if method == "DELETE":  # nothing to end on this side
                    await _empty(send, 200)
                    return
                inner = await self._reattach(scope, receive, key, params, headers)
                if inner is None:  # the server refused the handshake (e.g. 401): let it answer
                    await self.app(scope, _replayer(body, early, receive), send)
                    return
                fresh = True
            retry_on_404 = attempt == 0 and not fresh
            rewriter = _HeaderRewriter(send, inner=inner, outer=sid, swallow_404=retry_on_404)
            new_scope = dict(scope, headers=_with(headers, SESSION, inner))
            await self.app(new_scope, _replayer(body, early, receive), rewriter)
            await rewriter.flush()
            if rewriter.swallowed:
                # the server forgot this session (idle timeout, crash): reattach and retry once
                if self.sessions.get(key) == inner:
                    del self.sessions[key]
                continue
            if method == "DELETE" and rewriter.status is not None and rewriter.status < 300:
                self.sessions.pop(key, None)
            return
        await _empty(send, 404)  # pragma: no cover - the loop always returns above

    async def _reattach(self, scope: Scope, receive: Receive, key: Tuple[str, str],
                        params: Dict[str, Any],
                        headers: List[Tuple[bytes, bytes]]) -> Optional[str]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                inner = self._lookup(key)  # another request may have reattached meanwhile
                if inner is not None:
                    return inner
                inner = await self._handshake(scope, receive, params, headers)
                if inner is not None:
                    self._remember(key, inner)
                    self.reattached += 1
                    if self.on_reattach is not None:
                        self.on_reattach(key[0], inner)
                return inner
        finally:
            if not lock.locked():
                self._locks.pop(key, None)

    async def _handshake(self, scope: Scope, receive: Receive, params: Dict[str, Any],
                         headers: List[Tuple[bytes, bytes]]) -> Optional[str]:
        base = [(k, v) for k, v in headers if k not in _NOT_REPLAYED]
        base += [(b"content-type", b"application/json"),
                 (b"accept", b"application/json, text/event-stream")]
        self._ids += 1
        init = {"jsonrpc": "2.0", "id": f"reattach-{self._ids}", "method": "initialize",
                "params": params}
        status, rheaders, rbody = await self._call(scope, receive, base, init)
        inner = _get(rheaders, SESSION)
        if status != 200 or inner is None:
            return None
        reply = _first_message(rbody, _get(rheaders, b"content-type") or "")
        if not isinstance(reply, dict) or not isinstance(reply.get("result"), dict):
            return None
        version = reply["result"].get("protocolVersion") or params["protocolVersion"]
        await self._call(scope, receive,
                         base + [(SESSION, inner.encode("latin-1")),
                                 (VERSION, str(version).encode("latin-1"))],
                         {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return inner

    async def _call(self, scope: Scope, receive: Receive, headers: List[Tuple[bytes, bytes]],
                    msg: dict) -> Tuple[int, List[Tuple[bytes, bytes]], bytes]:
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        headers = headers + [(b"content-length", str(len(body)).encode())]
        new_scope = dict(scope, method="POST", headers=headers)
        out: Dict[str, Any] = {"status": 0, "headers": [], "body": b""}

        async def capture(message: Message) -> None:
            if message.get("type") == "http.response.start":
                out["status"] = message.get("status", 200)
                out["headers"] = [(bytes(k).lower(), bytes(v))
                                  for k, v in message.get("headers", [])]
            elif message.get("type") == "http.response.body":
                out["body"] += message.get("body", b"")

        await self.app(new_scope, _replayer(body, None, receive), capture)
        return out["status"], out["headers"], out["body"]


class _HeaderRewriter:
    """The ``send`` given to the app: maps the server's session id to the client's token."""

    def __init__(self, send: Send, inner: Optional[str] = None, outer: Optional[str] = None,
                 swallow_404: bool = False) -> None:
        self.send = send
        self.inner = inner
        self.outer = outer
        self.swallow_404 = swallow_404
        self.swallowed = False
        self.status: Optional[int] = None
        self.held: Optional[Message] = None
        self.held_body = b""
        self.on_new: Optional[Callable[[str], Optional[str]]] = None

    async def __call__(self, message: Message) -> None:
        if self.swallowed:
            return
        if self.held is not None:  # a 400 that may be "unknown session" from an older server
            if message.get("type") != "http.response.body":
                return
            self.held_body += message.get("body", b"")
            if message.get("more_body", False) and len(self.held_body) < 4096:
                return
            start, self.held = self.held, None
            if _SESSION_GONE_400 in self.held_body.lower():
                self.swallowed = True
                return
            await self._start(start)
            await self.send({"type": "http.response.body", "body": self.held_body,
                             "more_body": message.get("more_body", False)})
            return
        if message.get("type") == "http.response.start":
            self.status = message.get("status", 200)
            if self.swallow_404 and self.status == 404:
                self.swallowed = True
                return
            if self.swallow_404 and self.status == 400:
                self.held = message
                return
            await self._start(message)
            return
        await self.send(message)

    async def _start(self, message: Message) -> None:
        headers = []
        for k, v in message.get("headers", []):
            if bytes(k).lower() == SESSION:
                value = bytes(v).decode("latin-1")
                if self.on_new is not None and self.status == 200:
                    token = self.on_new(value)
                    value = token if token is not None else value
                elif value == self.inner and self.outer is not None:
                    value = self.outer
                v = value.encode("latin-1")
            headers.append((k, v))
        message = dict(message, headers=headers)
        await self.send(message)

    async def flush(self) -> None:
        """Send a held 400 the app never finished (it ended without a last body)."""
        if self.held is not None:
            start, self.held = self.held, None
            await self._start(start)
            await self.send({"type": "http.response.body", "body": self.held_body,
                             "more_body": False})


def _headers(scope: Scope) -> List[Tuple[bytes, bytes]]:
    return [(bytes(k).lower(), bytes(v)) for k, v in scope.get("headers", [])]


def _get(headers: List[Tuple[bytes, bytes]], name: bytes) -> Optional[str]:
    for k, v in headers:
        if k == name:
            return v.decode("latin-1")
    return None


def _with(headers: List[Tuple[bytes, bytes]], name: bytes, value: str) -> List[Tuple[bytes, bytes]]:
    return [(k, v) for k, v in headers if k != name] + [(name, value.encode("latin-1"))]


def _credential(headers: List[Tuple[bytes, bytes]]) -> str:
    """A digest of the request's credential, so one token never maps across users."""
    auth = b"\0".join(v for k, v in headers if k in (b"authorization", b"x-api-key", b"cookie"))
    return hashlib.sha256(auth).hexdigest()[:32] if auth else ""


def _first_message(body: bytes, content_type: str) -> Any:
    if content_type.split(";")[0].strip().lower() == "text/event-stream":
        for event in body.replace(b"\r\n", b"\n").split(b"\n\n"):
            data = [ln[5:].lstrip(b" ") for ln in event.split(b"\n") if ln.startswith(b"data:")]
            if data:
                try:
                    return json.loads(b"\n".join(data))
                except ValueError:
                    continue
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


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


async def _empty(send: Send, status: int) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b"", "more_body": False})
