"""``unstring proxy``: repair tool-call arguments between any MCP client and any stdio server.

The proxy starts the server and passes every message through unchanged, except
``tools/call``: there it repairs ``params.arguments`` against the tool's ``inputSchema``
(learned from ``tools/list`` results) before the server sees them.

If a call names a tool whose schema the proxy has not seen yet (the client cached the tool
list, or never asked for it), the proxy asks the server for its tool list itself, holds the
call until the answer is in, then sends it on, repaired. The client never sees that request.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, BinaryIO, Dict, List, Optional, TextIO, Tuple

from ._relay import Relay
from .core import repair

LIST_ID_PREFIX = "unstring-tools-"


class ArgProxy:
    """The message hooks, kept apart from the process handling so they are easy to test."""

    def __init__(self, *, wrap: bool = False, quiet: bool = False,
                 log: Optional[TextIO] = None) -> None:
        self.wrap = wrap
        self.quiet = quiet
        self.log = log or sys.stderr
        self.schemas: Dict[str, Any] = {}
        self.complete = False       # a full tool list (every page) has been seen
        self.fetching = False       # the proxy's own tools/list is in flight
        self.held: List[dict] = []  # tools/call requests waiting for that list
        self.fixed_calls = 0
        self._client_lists: set = set()
        self._next = 0
        self._lock = threading.Lock()

    # -- client -> server -------------------------------------------------------------

    def to_server(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        method = msg.get("method")
        if method == "tools/list" and "id" in msg:
            with self._lock:
                self._client_lists.add(_key(msg["id"]))
            return msg, []
        if method != "tools/call" or not isinstance(msg.get("params"), dict):
            return msg, []
        name = msg["params"].get("name")
        with self._lock:
            if name in self.schemas or self.complete:
                return self._fix(msg), []
            self.held.append(msg)
            if self.fetching:
                return None, []
            self.fetching = True
            return self._list_request(None), []

    # -- server -> client -------------------------------------------------------------

    def to_client(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        """Returns (message for the client, messages for the server)."""
        if msg.get("method") == "notifications/tools/list_changed":
            with self._lock:
                self.complete = False
            return msg, []
        if "method" in msg or "id" not in msg:
            return msg, []
        key = _key(msg["id"])
        if isinstance(msg["id"], str) and msg["id"].startswith(LIST_ID_PREFIX):
            return None, self._own_list_result(msg)
        with self._lock:
            if key not in self._client_lists:
                return msg, []
            self._client_lists.discard(key)
            self._learn(msg.get("result"))
        return msg, []

    def _own_list_result(self, msg: dict) -> List[dict]:
        with self._lock:
            result = msg.get("result")
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                self._learn(result)
                cursor = result.get("nextCursor")
                if isinstance(cursor, str) and cursor:
                    return [self._list_request(cursor)]
            elif not self.quiet:
                print(f"unstring: the server did not list its tools "
                      f"({json.dumps(msg.get('error'))[:200]}); passing calls on as they are",
                      file=self.log)
            self.complete = True  # also on error: don't ask again for every call
            self.fetching = False
            held, self.held = self.held, []
            return [self._fix(m) for m in held]

    # -- helpers ----------------------------------------------------------------------

    def _list_request(self, cursor: Optional[str]) -> dict:
        self._next += 1
        params = {"cursor": cursor} if cursor else {}
        return {"jsonrpc": "2.0", "id": f"{LIST_ID_PREFIX}{self._next}", "method": "tools/list",
                "params": params}

    def _learn(self, result: Any) -> None:
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        for tool in result["tools"]:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                self.schemas[tool["name"]] = tool.get("inputSchema")
        if not result.get("nextCursor"):
            self.complete = True

    def _fix(self, msg: dict) -> dict:
        params = msg["params"]
        if "arguments" not in params:
            return msg
        name = params.get("name")
        schema = self.schemas.get(name)
        result = repair(params["arguments"], schema if schema is not None else {},
                        wrap=self.wrap)
        if not result.changed:
            return msg
        self.fixed_calls += 1
        if not self.quiet:
            fixes = "; ".join(str(f) for f in result.fixes)
            print(f"unstring: {name}: {fixes}", file=self.log)
        return dict(msg, params=dict(params, arguments=result.value))


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests


def run(command: List[str], *, wrap: bool = False, quiet: bool = False,
        log: Optional[TextIO] = None, stdin: Optional[BinaryIO] = None,
        stdout: Optional[BinaryIO] = None) -> int:
    proxy = ArgProxy(wrap=wrap, quiet=quiet, log=log)
    relay = Relay(command, to_server=proxy.to_server, to_client=proxy.to_client,
                  stdin=stdin, stdout=stdout, name="unstring proxy")
    return relay.run()
