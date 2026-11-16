"""A small reverse proxy, so servers written in any language get reattach (``reattach proxy``).

Needs the ``proxy`` extra (``pip install "reattach[proxy]"``: httpx and uvicorn). The proxy
forwards every path and method to the upstream server and streams responses (SSE included)
back as they arrive. Wrapped in :class:`reattach.Reattach`, it keeps clients' sessions alive
while the upstream restarts behind it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from .asgi import Message, Receive, Scope, Send

HOP_BY_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
              b"te", b"trailer", b"trailers", b"transfer-encoding", b"upgrade", b"host",
              b"content-length"}


class Proxy:
    """ASGI app that forwards requests to ``upstream`` (an origin such as http://127.0.0.1:3000).

    ``client`` is an ``httpx.AsyncClient`` to use (tests pass one with a fake transport).
    """

    def __init__(self, upstream: str, client: Any = None, timeout: Optional[float] = None) -> None:
        self.upstream = upstream.rstrip("/")
        self.client = client
        self.timeout = timeout

    def _client(self) -> Any:
        if self.client is None:
            import httpx

            self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        return self.client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        chunks = []
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        headers: List[Tuple[bytes, bytes]] = [
            (bytes(k).lower(), bytes(v)) for k, v in scope.get("headers", [])
            if bytes(k).lower() not in HOP_BY_HOP]
        host = dict((bytes(k).lower(), bytes(v)) for k, v in scope.get("headers", [])).get(b"host")
        if host:
            headers.append((b"x-forwarded-host", host))
        headers.append((b"x-forwarded-proto", scope.get("scheme", "http").encode()))
        url = self.upstream + scope.get("raw_path", scope["path"].encode()).decode("latin-1")
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode("latin-1")
        client = self._client()
        try:
            request = client.build_request(scope["method"], url, headers=headers,
                                           content=b"".join(chunks))
            response = await client.send(request, stream=True)
        except Exception as exc:  # connection refused while the upstream restarts, timeouts
            await _bad_gateway(send, f"upstream unavailable: {type(exc).__name__}")
            return
        watcher = asyncio.ensure_future(_wait_disconnect(receive))
        try:
            out = [(k.lower(), self._location(v) if k.lower() == b"location" else v)
                   for k, v in response.headers.raw if k.lower() not in HOP_BY_HOP]
            length = response.headers.get("content-length")
            if length is not None:
                out.append((b"content-length", length.encode()))
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": out})
            stream = response.aiter_raw().__aiter__()
            while True:
                nxt = asyncio.ensure_future(stream.__anext__())
                done, _ = await asyncio.wait({nxt, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:  # the client went away: stop reading the upstream stream
                    nxt.cancel()
                    break
                try:
                    chunk = nxt.result()
                except StopAsyncIteration:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    break
                except Exception:  # the upstream died mid-stream: end the response
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    break
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            watcher.cancel()
            await response.aclose()

    def _location(self, value: bytes) -> bytes:
        """Redirects to the upstream's own origin (e.g. /mcp -> /mcp/) stay on the proxy."""
        text = value.decode("latin-1")
        if text.startswith(self.upstream + "/") or text == self.upstream:
            text = text[len(self.upstream):] or "/"
        return text.encode("latin-1")

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                if self.client is not None:
                    await self.client.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return


async def _wait_disconnect(receive: Receive) -> None:
    while True:
        msg: Message = await receive()
        if msg.get("type") == "http.disconnect":
            return


async def _bad_gateway(send: Send, text: str) -> None:
    body: Dict[str, Any] = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32000, "message": text}}
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": 502,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data, "more_body": False})
