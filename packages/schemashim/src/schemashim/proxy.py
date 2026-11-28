"""``schemashim proxy``: put schemashim between any MCP client and any stdio MCP server.

The proxy starts the server, rewrites every tool's ``inputSchema`` in ``tools/list``
results into the subset the target provider accepts, and maps the arguments of each
``tools/call`` back to what the server expects. With ``max_name_length`` it also shortens
tool names that are too long (or have characters the target rejects) and maps each call's
name back. Everything else passes through unchanged.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from ._relay import Relay
from .core import Shim, adapt
from .names import MAX_LEN, tool_name, unique_name


class SchemaProxy:
    """The message hooks, kept apart from the process handling so they are easy to test."""

    def __init__(self, target: Optional[str] = None, *, quiet: bool = False,
                 max_name_length: Optional[int] = None) -> None:
        if target is None and max_name_length is None:
            raise ValueError("give a target, a max_name_length, or both")
        if target is not None:
            adapt({"type": "object"}, target)  # raises ValueError for an unknown target
        tool_name("x", target, MAX_LEN if max_name_length is None else max_name_length)
        self.target = target
        self.quiet = quiet
        self.max_name_length = max_name_length
        self.shims: Dict[str, Shim] = {}
        self.names: Dict[str, str] = {}  # name the client sees -> the server's name
        self._list_ids: Dict[str, bool] = {}  # pending tools/list id -> asked for page 1
        self._lock = threading.Lock()

    def _log(self, text: str) -> None:
        if not self.quiet:
            print(f"schemashim: {text}", file=sys.stderr)

    def _rename(self, tools: List[Any]) -> Dict[int, str]:
        """New names for the tools at these positions; every other name stays as it is.

        A target always gets names it accepts; ``max_name_length`` also shortens long ones.
        Names that need no change are claimed first, so a shortened name never takes one.
        """
        limit = MAX_LEN if self.max_name_length is None else self.max_name_length
        with self._lock:
            # names from earlier pages of a paginated tools/list stay taken
            taken = set(self.names)
        todo = []
        for i, tool in enumerate(tools):
            name = tool.get("name") if isinstance(tool, dict) else None
            if not isinstance(name, str) or not name:
                continue
            if tool_name(name, self.target, limit) == name:
                taken.add(name)
            else:
                todo.append(i)
        out = {}
        for i in todo:
            new = unique_name(tools[i]["name"], self.target, taken, limit)
            taken.add(new)
            out[i] = new
        return out

    def to_server(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        method = msg.get("method")
        if method == "tools/list" and "id" in msg:
            params = msg.get("params")
            first = not (isinstance(params, dict) and params.get("cursor"))
            with self._lock:
                self._list_ids[_key(msg["id"])] = first
            return msg, []
        if method == "tools/call" and isinstance(msg.get("params"), dict):
            params = msg["params"]
            name = params.get("name")
            with self._lock:
                original = self.names.get(name) if isinstance(name, str) else None
            if original is not None and original != name:
                params = dict(params, name=original)
                msg = dict(msg, params=params)
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
            first = self._list_ids.pop(_key(msg["id"]))
        result = msg.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return msg, []
        tools: List[Any] = []
        if first:  # a fresh list (e.g. after list_changed): earlier names no longer count
            with self._lock:
                self.names = {}
        renames = self._rename(result["tools"])
        changed = fixed = 0
        for i, tool in enumerate(result["tools"]):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                tools.append(tool)
                continue
            name = tool["name"]
            new_name = renames.get(i, name)
            with self._lock:
                self.names[new_name] = name
            out = tool if new_name == name else dict(tool, name=new_name)
            if new_name != name:
                fixed += 1
                changed += 1
                self._log(f"{name}: renamed to {new_name}")
            schema = tool.get("inputSchema")
            if self.target is None or schema is None:
                tools.append(out)
                continue
            shim = adapt(schema, self.target)
            self.shims[name] = shim
            if shim.ok:
                tools.append(out)
                continue
            if new_name == name:
                fixed += 1
            changed += len(shim.changes)
            tools.append(dict(out, inputSchema=shim.schema))
            for change in shim.changes:
                self._log(f"{name}: {change}")
        if not fixed:
            return msg, []
        what = " and ".join(x for x in (
            self.target,
            self.max_name_length and f"names of up to {self.max_name_length} characters") if x)
        self._log(f"adapted {fixed} of {len(tools)} tools for {what} ({changed} changes)")
        return dict(msg, result=dict(result, tools=tools)), []


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests


def run(command: List[str], target: Optional[str] = None, *, quiet: bool = False,
        max_name_length: Optional[int] = None,
        stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None) -> int:
    proxy = SchemaProxy(target, quiet=quiet, max_name_length=max_name_length)
    relay = Relay(command, to_server=proxy.to_server, to_client=proxy.to_client,
                  stdin=stdin, stdout=stdout, name="schemashim proxy")
    return relay.run()
