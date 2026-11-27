"""``flatresult proxy``: make every tool result readable between any MCP client and server.

The proxy starts the server and passes every message through unchanged, except the results
of the client's ``tools/call`` requests: those are rewritten with :func:`flatresult.flatten`.

When a result carries ``resource_link`` blocks, the proxy asks the server for those
resources itself (``resources/read``), holds the result until the answers are in (or
``timeout`` seconds pass), and puts the contents inline. The client never sees those
requests. If the server says it has no ``resources`` capability, links become text lines.
"""

from __future__ import annotations

import sys
import threading
from typing import Any, BinaryIO, Callable, Dict, List, Optional, TextIO, Tuple

from ._relay import Relay
from .core import DEFAULT_MAX_TEXT, flatten, links

READ_ID_PREFIX = "flatresult-read-"
# 2026-07-28 servers want this envelope (protocol version, client capabilities...) in the
# _meta of every request; the proxy copies it from the client's own tools/call.
ENVELOPE_PREFIX = "io.modelcontextprotocol/"


class _Held:
    def __init__(self, msg: dict, uris: List[str]) -> None:
        self.msg = msg
        self.waiting = set(uris)
        self.contents: Dict[str, Optional[List[dict]]] = {}
        self.timer: Optional[threading.Timer] = None
        self.done = False


class ResultProxy:
    """The message hooks, kept apart from the process handling so they are easy to test.

    ``emit`` sends a message to the client outside a hook call; the proxy needs it only when
    a held result times out.
    """

    def __init__(self, *, keep: bool = False, inline: bool = True, max_links: int = 5,
                 max_text: int = DEFAULT_MAX_TEXT, timeout: float = 10.0, quiet: bool = False,
                 log: Optional[TextIO] = None,
                 emit: Optional[Callable[[dict], None]] = None) -> None:
        self.keep = keep
        self.inline = inline
        self.max_links = max_links
        self.max_text = max_text
        self.timeout = timeout
        self.quiet = quiet
        self.log = log or sys.stderr
        self.emit = emit
        self.can_read: Optional[bool] = None  # unknown until the server's capabilities show
        self.flattened = 0
        self._calls: Dict[str, Optional[dict]] = {}   # client tools/call id -> its _meta
        self._handshakes: set = set()                 # initialize / server/discover ids
        self._reads: Dict[str, Tuple[_Held, str]] = {}
        self._next = 0
        self._lock = threading.Lock()

    # -- client -> server -------------------------------------------------------------

    def to_server(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        method = msg.get("method")
        if "id" not in msg or not isinstance(method, str):
            return msg, []
        with self._lock:
            if method == "tools/call":
                params = msg.get("params")
                meta = params.get("_meta") if isinstance(params, dict) else None
                self._calls[_key(msg["id"])] = meta if isinstance(meta, dict) else None
            elif method in ("initialize", "server/discover"):
                self._handshakes.add(_key(msg["id"]))
        return msg, []

    # -- server -> client -------------------------------------------------------------

    def to_client(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        """Returns (message for the client, messages for the server)."""
        if "method" in msg or "id" not in msg:
            return msg, []
        if isinstance(msg["id"], str) and msg["id"].startswith(READ_ID_PREFIX):
            return self._read_result(msg), []
        key = _key(msg["id"])
        with self._lock:
            if key in self._handshakes:
                self._handshakes.discard(key)
                self._learn_capabilities(msg.get("result"))
                return msg, []
            if key not in self._calls:
                return msg, []
            meta = self._calls.pop(key)
            result = msg.get("result")
            uris = _unique(links(result))[: self.max_links] if self.inline else []
            if not uris or self.can_read is False:
                return self._flat(msg, {}), []
            held = _Held(msg, uris)
            requests = [self._read_request(held, uri, meta) for uri in uris]
            if self.timeout > 0 and self.emit is not None:
                held.timer = threading.Timer(self.timeout, self._expire, (held,))
                held.timer.daemon = True
                held.timer.start()
        return None, requests

    # -- helpers ----------------------------------------------------------------------

    def _learn_capabilities(self, result: Any) -> None:
        if isinstance(result, dict) and isinstance(result.get("capabilities"), dict):
            self.can_read = "resources" in result["capabilities"]

    def _read_request(self, held: _Held, uri: str, meta: Optional[dict]) -> dict:
        self._next += 1
        rid = f"{READ_ID_PREFIX}{self._next}"
        self._reads[rid] = (held, uri)
        params: Dict[str, Any] = {"uri": uri}
        envelope = {k: v for k, v in (meta or {}).items()
                    if isinstance(k, str) and k.startswith(ENVELOPE_PREFIX)}
        if envelope:
            params["_meta"] = envelope
        return {"jsonrpc": "2.0", "id": rid, "method": "resources/read", "params": params}

    def _read_result(self, msg: dict) -> Optional[dict]:
        with self._lock:
            entry = self._reads.pop(msg["id"], None)
            if entry is None:
                return None  # an answer to a read that timed out; the client never asked
            held, uri = entry
            result = msg.get("result")
            contents = result.get("contents") if isinstance(result, dict) else None
            held.contents[uri] = contents if isinstance(contents, list) else None
            if contents is None and not self.quiet:
                print(f"flatresult: can't read {uri}: {str(msg.get('error'))[:200]}",
                      file=self.log)
            held.waiting.discard(uri)
            if held.waiting or held.done:
                return None
            return self._release(held)

    def _expire(self, held: _Held) -> None:
        with self._lock:
            if held.done:
                return
            for rid in [r for r, (h, _) in self._reads.items() if h is held]:
                del self._reads[rid]
            if not self.quiet:
                print(f"flatresult: no answer to resources/read within {self.timeout:g}s; "
                      "sending the result with links as text", file=self.log)
            out = self._release(held)
        if self.emit is not None:
            self.emit(out)

    def _release(self, held: _Held) -> dict:
        held.done = True
        if held.timer is not None:
            held.timer.cancel()
        return self._flat(held.msg, held.contents)

    def _flat(self, msg: dict, contents: Dict[str, Optional[List[dict]]]) -> dict:
        result = msg.get("result")
        flat = flatten(result, keep=self.keep, read=contents.get if contents else None,
                       max_text=self.max_text)
        if not flat.changed:
            return msg
        self.flattened += 1
        if not self.quiet:
            print(f"flatresult: {'; '.join(str(c) for c in flat.changes)}", file=self.log)
        return dict(msg, result=flat.result)


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests


def _unique(items: List[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def run(command: List[str], *, keep: bool = False, inline: bool = True, max_links: int = 5,
        max_text: int = DEFAULT_MAX_TEXT, timeout: float = 10.0, quiet: bool = False,
        log: Optional[TextIO] = None, stdin: Optional[BinaryIO] = None,
        stdout: Optional[BinaryIO] = None) -> int:
    proxy = ResultProxy(keep=keep, inline=inline, max_links=max_links, max_text=max_text,
                        timeout=timeout, quiet=quiet, log=log)
    relay = Relay(command, to_server=proxy.to_server, to_client=proxy.to_client,
                  stdin=stdin, stdout=stdout, name="flatresult proxy")
    proxy.emit = relay.send_to_client
    return relay.run()
