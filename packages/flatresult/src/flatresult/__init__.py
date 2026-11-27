"""Make MCP tool results readable by clients that only show text and image blocks."""

from .core import Change, Flattened, flatten, links, needs_flattening

__version__ = "0.1.0"
__all__ = ["Change", "Flattened", "flatten", "links", "needs_flattening", "__version__"]
