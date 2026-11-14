"""cleanpipe: keep an MCP server's stdout clean so stdio clients can talk to it.

Run any stdio MCP server as ``cleanpipe -- <command>``: JSON-RPC messages reach the client,
banners and log lines the server prints on stdout go to stderr. In Python:

>>> from cleanpipe import split
>>> split(b'Server ready{"jsonrpc":"2.0","id":1,"result":{}}\\n')
([b'{"jsonrpc":"2.0","id":1,"result":{}}'], [b'Server ready'])
"""

__version__ = "0.1.0"

from .check import Report, check  # noqa: E402
from .core import is_message, split  # noqa: E402
from .pipe import Pipe, run  # noqa: E402

__all__ = ["Pipe", "Report", "check", "is_message", "run", "split", "__version__"]
