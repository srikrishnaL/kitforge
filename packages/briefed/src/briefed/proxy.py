"""``briefed proxy``: run any stdio MCP server with its instructions folded into the tool list."""

from __future__ import annotations

from typing import BinaryIO, List, Optional

from ._relay import Relay
from .core import Brief
from .hooks import Folder


def run(command: List[str], brief: Optional[Brief] = None, *, keep: bool = True,
        stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None) -> int:
    """Start ``command`` and relay stdio between it and the client, folding instructions in."""
    folder = Folder(brief or Brief(), keep=keep)
    relay = Relay(command, to_server=folder.to_server, to_client=folder.to_client,
                  stdin=stdin, stdout=stdout, name="briefed proxy")
    return relay.run()
