"""ASGI middleware for Streamable HTTP MCP servers (remote connectors such as claude.ai).

Wrap the server's ASGI app::

    from briefed.asgi import Briefed
    app = Briefed(mcp.streamable_http_app())

The middleware reads each POST body to see which JSON-RPC requests it carries, answers calls
to the instructions tool itself, and rewrites the ``initialize`` and ``tools/list`` responses,
whether they come back as ``application/json`` or as an SSE stream. Everything else (GET
streams, DELETE, other paths, compressed responses) passes through untouched.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .core import DEFAULT_TOOL_NAME, Brief
from .hooks import Folder

Scope = Dict[str, Any]
Message = Dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_EVENT_END = re.compile(rb"\r\n\r\n|\n\n|\r\r")


class Briefed:
    """Fold the wrapped MCP server's instructions into its tool list (see :class:`briefed.Brief`).

    ``instructions=None`` uses the text the server sends in ``initialize``. Pass the text
    explicitly for stateless servers, where a ``tools/list`` request may reach a process that
    has not seen an ``initialize`` yet. ``path`` limits the middleware to one URL path.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], instructions: Optional[str] = None, *,
                 mode: str = "tool", tool_name: str = DEFAULT_TOOL_NAME,
                 target: Optional[str] = None, hint: bool = True, extra: Optional[str] = None,
                 keep: bool = True, path: Optional[str] = None) -> None:
        self.app = app
        self.brief = Brief(instructions, mode=mode, tool_name=tool_name, target=target,
                           hint=hint, extra=extra)
        self.keep = keep
        self.path = path.rstrip("/") if path else None
        self.server_tool = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope.get("type") != "http" or scope.get("method") != "POST"
                or (self.path is not None and scope.get("path", "").rstrip("/") != self.path)):
            await self.app(scope, receive, send)
            return

        body, early = await _read_body(receive)
        replay = _replayer(body, early, receive)
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        requests = [m for m in (parsed if isinstance(parsed, list) else [parsed])
                    if isinstance(m, dict)]
        if not requests:
            await self.app(scope, replay, send)
            return

        folder = Folder(self.brief, keep=self.keep)
        folder.server_tool = self.server_tool
        replies: List[dict] = []
        for msg in requests:
            _, extra = folder.to_server(msg)
            replies += extra
        if replies and isinstance(parsed, dict):
            await _send_json(send, replies[0])
            return
        if not folder._pending:
            await self.app(scope, replay, send)
            return
        try:
            await self.app(scope, replay, _Rewriter(folder, send))
        finally:
            self.server_tool = folder.server_tool


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


async def _send_json(send: Send, msg: dict) -> None:
    data = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data, "more_body": False})


class _Rewriter:
    """The ``send`` callable given to the app: rewrites JSON and SSE response bodies."""

    def __init__(self, folder: Folder, send: Send) -> None:
        self.folder = folder
        self.send = send
        self.kind = "raw"
        self.start: Optional[Message] = None
        self.buf = b""

    async def __call__(self, message: Message) -> None:
        mtype = message.get("type")
        if mtype == "http.response.start":
            headers = [(bytes(k).lower(), bytes(v)) for k, v in message.get("headers", [])]
            ctype = dict(headers).get(b"content-type", b"").split(b";")[0].strip().lower()
            encoded = dict(headers).get(b"content-encoding", b"identity").lower() != b"identity"
            if message.get("status", 200) != 200 or encoded:
                self.kind = "raw"
            elif ctype == b"application/json":
                self.kind = "json"
                self.start = message
                return  # sent with the body, once its new length is known
            elif ctype == b"text/event-stream":
                self.kind = "sse"
                headers = [(k, v) for k, v in headers if k != b"content-length"]
                message = dict(message, headers=headers)
            await self.send(message)
            return
        if mtype != "http.response.body" or self.kind == "raw":
            await self.send(message)
            return
        self.buf += message.get("body", b"")
        more = message.get("more_body", False)
        if self.kind == "json":
            if more:
                return
            data = self._json(self.buf)
            self.buf = b""
            assert self.start is not None
            headers = [(k, v) for k, v in self.start.get("headers", [])
                       if bytes(k).lower() != b"content-length"]
            headers.append((b"content-length", str(len(data)).encode()))
            await self.send(dict(self.start, headers=headers))
            await self.send({"type": "http.response.body", "body": data, "more_body": False})
            return
        out = b""
        while True:
            m = _EVENT_END.search(self.buf)
            if not m:
                break
            event, self.buf = self.buf[:m.start()], self.buf[m.end():]
            out += self._event(event, m.group(0))
        if not more:
            out, self.buf = out + self.buf, b""
        if out or not more:
            await self.send({"type": "http.response.body", "body": out, "more_body": more})

    def _json(self, data: bytes) -> bytes:
        try:
            parsed = json.loads(data)
        except ValueError:
            return data
        items = parsed if isinstance(parsed, list) else [parsed]
        changed = False
        new_items = []
        for item in items:
            new = self._message(item)
            changed = changed or new is not item
            new_items.append(new)
        if not changed:
            return data
        out = new_items if isinstance(parsed, list) else new_items[0]
        return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _message(self, item: Any) -> Any:
        if not isinstance(item, dict) or not self.folder.wants(item):
            return item
        new, _ = self.folder.to_client(item)
        return item if new is None else new

    def _event(self, event: bytes, end: bytes) -> bytes:
        sep = end[: len(end) // 2]
        lines = event.split(sep)
        data = [ln[5:] for ln in lines if ln.startswith(b"data:")]
        if not data:
            return event + end
        joined = b"\n".join(d[1:] if d.startswith(b" ") else d for d in data)
        try:
            msg = json.loads(joined)
        except ValueError:
            return event + end
        new = self._message(msg)
        if new is msg:
            return event + end
        payload = json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        kept = [ln for ln in lines if not ln.startswith(b"data:")]
        at = next(i for i, ln in enumerate(lines) if ln.startswith(b"data:"))
        kept.insert(min(at, len(kept)), b"data: " + payload)
        return sep.join(kept) + end
