"""``toolcap proxy``: page the oversized results of any stdio MCP server.

The proxy starts the server and sits between it and the client. It adds a ``cursor``
parameter to every tool in ``tools/list``. When a ``tools/call`` result is over the budget,
the model gets the first page and a ``next_cursor``; the full result stays in the proxy's
memory, so the next pages come from there without calling the server again (if it was
forgotten, the proxy calls the server again with the same arguments). Everything else
passes through unchanged.
"""

from __future__ import annotations

import json
import sys
import threading
from collections import OrderedDict
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from ._relay import Relay
from .core import Cap, _fingerprint, decode_cursor, jsonable
from .errors import CursorError

#: How many full results the proxy keeps for paging.
CACHE_SIZE = 16


class CapProxy:
    """The message hooks, kept apart from the process handling so they are easy to test."""

    def __init__(self, cap: Optional[Cap] = None, *, trim: bool = False, quiet: bool = False) -> None:
        self.cap = cap or Cap()
        self.trim = trim
        self.quiet = quiet
        self.param = self.cap.cursor_param
        self.paged: set = set()  # tools we added the cursor parameter to
        self._list_ids: set = set()
        self._calls: Dict[str, Tuple[str, Optional[str]]] = {}  # id -> (tool, cursor or None to trim)
        self._cache: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ client -> server

    def to_server(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        method = msg.get("method")
        if "id" not in msg:
            return msg, []
        if method == "tools/list":
            with self._lock:
                self._list_ids.add(_key(msg["id"]))
            return msg, []
        if method != "tools/call" or not isinstance(msg.get("params"), dict):
            return msg, []
        params = msg["params"]
        name = params.get("name")
        if not isinstance(name, str):
            return msg, []
        if name not in self.paged:
            with self._lock:
                self._calls[_key(msg["id"])] = (name, None)
            return msg, []
        args = params.get("arguments")
        args = dict(args) if isinstance(args, dict) else {}
        cursor = args.pop(self.param, "")
        cursor = cursor if isinstance(cursor, str) else ""
        if cursor:
            try:
                _, fp = decode_cursor(cursor)
            except CursorError as exc:
                return None, [_result(msg["id"], str(exc), error=True)]
            with self._lock:
                data = self._cache.get(fp)
                if data is not None:
                    self._cache.move_to_end(fp)
            if data is not None:
                return None, [_result(msg["id"], self.cap.page(data, cursor))]
        with self._lock:
            self._calls[_key(msg["id"])] = (name, cursor)
        if self.param not in (params.get("arguments") or {}):
            return msg, []
        return dict(msg, params=dict(params, arguments=args)), []

    # ------------------------------------------------------------ server -> client

    def to_client(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        if "method" in msg or "id" not in msg:  # a request or notification from the server
            return msg, []
        key = _key(msg["id"])
        with self._lock:
            listing = key in self._list_ids
            self._list_ids.discard(key)
            call = self._calls.pop(key, None)
        if listing:
            return self._tools(msg), []
        if call is not None:
            return self._result(msg, *call), []
        return msg, []

    def _tools(self, msg: dict) -> dict:
        result = msg.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return msg
        hint = (f"Long results come in pages: if a result has a next_cursor, call again with the "
                f"same arguments and {self.param} set to it.")
        tools = []
        for tool in result["tools"]:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                tools.append(tool)
                continue
            tool = dict(tool)
            # A cut or paged result can't match an output schema, and clients that see one
            # expect structuredContent that matches it.
            tool.pop("outputSchema", None)
            schema = tool.get("inputSchema")
            props = schema.get("properties") if isinstance(schema, dict) else None
            if (self.trim or not isinstance(schema, dict)
                    or (props is not None and not isinstance(props, dict))
                    or self.param in (props or {})):
                self.paged.discard(tool["name"])  # results are trimmed instead of paged
                tools.append(tool)
                continue
            props = dict(props or {})
            props[self.param] = {
                "type": "string",
                "description": "Leave empty. To get the next page of a long result, pass the "
                               "next_cursor it gave.",
            }
            tool["inputSchema"] = dict(schema, properties=props)
            desc = tool.get("description") or ""
            tool["description"] = f"{desc}\n\n{hint}" if desc else hint
            self.paged.add(tool["name"])
            tools.append(tool)
        return dict(msg, result=dict(result, tools=tools))

    def _result(self, msg: dict, name: str, cursor: Optional[str]) -> dict:
        result = msg.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            return msg
        blocks = result["content"]
        texts = [b.get("text") for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        if not texts:
            return msg
        text = "\n".join(texts)
        if not cursor and self.cap.fits(text):
            return msg
        data: Any = text
        if text.lstrip()[:1] in ("[", "{"):
            try:
                data = json.loads(text)
            except ValueError:
                pass
        try:
            if cursor is None or self.trim:
                out = self.cap.trim(data)
            else:
                out = self.cap.page(data, cursor)
                with self._lock:
                    self._cache[self._fp(data)] = data
                    self._cache.move_to_end(self._fp(data))
                    while len(self._cache) > CACHE_SIZE:
                        self._cache.popitem(last=False)
        except (CursorError, ValueError) as exc:
            return dict(msg, result={"content": [{"type": "text", "text": str(exc)}], "isError": True})
        if not self.quiet and not cursor:
            print(f"toolcap: {name}: result of about {self.cap.count(text):,} tokens "
                  f"{'cut' if cursor is None or self.trim else 'paged'} to fit "
                  f"{self.cap.max_tokens:,}", file=sys.stderr)
        others = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "text")]
        new = {k: v for k, v in result.items() if k not in ("content", "structuredContent")}
        new["content"] = (others if not cursor else []) + [{"type": "text", "text": out}]
        return dict(msg, result=new)

    def _fp(self, data: Any) -> str:
        if isinstance(data, str):
            return _fingerprint(data)
        return _fingerprint(self.cap._dumps(jsonable(data)))


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests


def _result(id_: Any, text: str, *, error: bool = False) -> dict:
    result: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def run(command: List[str], cap: Optional[Cap] = None, *, trim: bool = False, quiet: bool = False,
        stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None) -> int:
    proxy = CapProxy(cap, trim=trim, quiet=quiet)
    relay = Relay(command, to_server=proxy.to_server, to_client=proxy.to_client,
                  stdin=stdin, stdout=stdout, name="toolcap proxy")
    return relay.run()
