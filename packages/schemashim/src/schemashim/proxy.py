"""``schemashim proxy``: put schemashim between any MCP client and any stdio MCP server.

The proxy starts the server, rewrites every tool's ``inputSchema`` in ``tools/list``
results into the subset the target provider accepts, and maps the arguments of each
``tools/call`` back to what the server expects. Everything else passes through unchanged.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from ._relay import Relay
from .core import Shim, adapt


class SchemaProxy:
    """The message hooks, kept apart from the process handling so they are easy to test."""

    def __init__(self, target: str, *, quiet: bool = False) -> None:
        adapt({"type": "object"}, target)  # raises ValueError for an unknown target
        self.target = target
        self.quiet = quiet
        self.shims: Dict[str, Shim] = {}
        self._list_ids: set = set()
        self._lock = threading.Lock()

    def to_server(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        method = msg.get("method")
        if method == "tools/list" and "id" in msg:
            with self._lock:
                self._list_ids.add(_key(msg["id"]))
            return msg, []
        if method == "tools/call" and isinstance(msg.get("params"), dict):
            params = msg["params"]
            shim = self.shims.get(params.get("name"))
            if shim is not None and isinstance(params.get("arguments"), dict):
                restored = shim.restore(params["arguments"])
                # Compare as JSON: 5 == 5.0 in Python, but not to a strict server.
                if not _same(restored, params["arguments"]):
                    return dict(msg, params=dict(params, arguments=restored)), []
        return msg, []

    def to_client(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        if "method" in msg or "id" not in msg:  # a request or notification from the server
            return msg, []
        with self._lock:
            if _key(msg["id"]) not in self._list_ids:
                return msg, []
            self._list_ids.discard(_key(msg["id"]))
        result = msg.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return msg, []
        tools: List[Any] = []
        changed = fixed = 0
        for tool in result["tools"]:
            schema = tool.get("inputSchema") if isinstance(tool, dict) else None
            if schema is None or not isinstance(tool.get("name"), str):
                tools.append(tool)
                continue
            shim = adapt(schema, self.target)
            self.shims[tool["name"]] = shim
            if shim.ok:
                tools.append(tool)
                continue
            fixed += 1
            changed += len(shim.changes)
            tools.append(dict(tool, inputSchema=shim.schema))
            if not self.quiet:
                for change in shim.changes:
                    print(f"schemashim: {tool['name']}: {change}", file=sys.stderr)
        if not fixed:
            return msg, []
        if not self.quiet:
            print(f"schemashim: adapted {fixed} of {len(tools)} tools for {self.target} "
                  f"({changed} changes)", file=sys.stderr)
        return dict(msg, result=dict(result, tools=tools)), []


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests


def run(command: List[str], target: str, *, quiet: bool = False,
        stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None) -> int:
    proxy = SchemaProxy(target, quiet=quiet)
    relay = Relay(command, to_server=proxy.to_server, to_client=proxy.to_client,
                  stdin=stdin, stdout=stdout, name="schemashim proxy")
    return relay.run()
