"""The JSON-RPC rewriting shared by the stdio proxy and the ASGI middleware.

:class:`Folder` follows one connection: it remembers which request ids were ``initialize``,
``tools/list`` or a call to the instructions tool, and rewrites the matching responses.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from .core import Brief

#: Methods whose result may carry the server's instructions (``server/discover`` is the
#: initialize-free discovery call of newer protocol revisions).
INIT_METHODS = ("initialize", "server/discover")


class Folder:
    def __init__(self, brief: Brief, *, keep: bool = True) -> None:
        self.brief = brief
        self.keep = keep
        self.server_tool = False  # the server has its own tool with the instructions tool's name
        self._pending: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ client -> server

    def to_server(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        """Note requests whose responses need rewriting; answer instructions-tool calls."""
        if "id" not in msg or not isinstance(msg.get("method"), str):
            return msg, []
        method = msg["method"]
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method == "tools/call" and not self.server_tool and self.brief.handles(params.get("name")):
            result = self.brief.call_result()
            if _stateless_era(params):
                result["resultType"] = "complete"  # required on every result since 2026-07-28
            return None, [{"jsonrpc": "2.0", "id": msg["id"], "result": result}]
        kind = None
        if method in INIT_METHODS:
            kind = "init"
        elif method == "tools/list" and not params.get("cursor"):
            kind = "list"  # only the first page gets the instructions tool
        if kind:
            with self._lock:
                self._pending[_key(msg["id"])] = kind
        return msg, []

    def wants(self, msg: dict) -> bool:
        """True if :meth:`to_client` may change this response."""
        if "method" in msg or "id" not in msg:
            return False
        with self._lock:
            return _key(msg["id"]) in self._pending

    # ------------------------------------------------------------ server -> client

    def to_client(self, msg: dict) -> Tuple[Optional[dict], List[dict]]:
        if "method" in msg or "id" not in msg:
            return msg, []
        with self._lock:
            kind = self._pending.pop(_key(msg["id"]), None)
        result = msg.get("result")
        if kind is None or not isinstance(result, dict):
            return msg, []
        if kind == "init":
            new = self.brief.initialize_result(result, keep=self.keep)
            return (msg if new is result else dict(msg, result=new)), []
        tools = result.get("tools")
        if not isinstance(tools, list):
            return msg, []
        name = self.brief.tool_name
        self.server_tool = any(isinstance(t, dict) and t.get("name") == name for t in tools)
        new_tools = self.brief.tools(tools)
        if new_tools is tools:
            return msg, []
        return dict(msg, result=dict(result, tools=new_tools)), []


#: The first protocol revision without initialize, where every request names its version in
#: ``_meta`` and every result needs a ``resultType``.
STATELESS_REVISION = "2026-07-28"


def _stateless_era(params: dict) -> bool:
    meta = params.get("_meta")
    version = meta.get("io.modelcontextprotocol/protocolVersion") if isinstance(meta, dict) else None
    return isinstance(version, str) and version >= STATELESS_REVISION


def _key(value: Any) -> str:
    return repr(value)  # ids may be numbers or strings; 1 and "1" are different requests
