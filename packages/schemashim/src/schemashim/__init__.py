"""schemashim: make MCP and agent tool schemas work on Gemini and OpenAI strict mode."""

from .core import GEMINI, OPENAI_STRICT, TARGETS, Change, Shim, adapt, check
from .names import tool_name
from .tools import AdaptedTool, ToolKit, adapt_tool, adapt_tools

__version__ = "0.2.0"

__all__ = [
    "GEMINI", "OPENAI_STRICT", "TARGETS", "Change", "Shim", "adapt", "check", "tool_name",
    "AdaptedTool", "ToolKit", "adapt_tool", "adapt_tools", "__version__",
]
