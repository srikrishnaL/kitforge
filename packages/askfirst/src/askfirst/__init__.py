"""askfirst: confirm destructive MCP tool calls with the user, and read-only mode.

    from askfirst import Confirmer

    ask = Confirmer()

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def delete_branch(repo: str, branch: str, ctx: Context, confirm_token: str = "") -> str:
        await ask(ctx, f"Delete branch {branch} in {repo}?", action="delete_branch",
                  args={"repo": repo, "branch": branch}, token=confirm_token)
        ...
"""

from .confirm import Confirmer, client_supports_elicitation, confirm
from .errors import AskFirstError, ConfirmationRequired, ConfirmationUnavailable, Declined
from .restrict import is_read_only, restrict, restrict_from_env
from .tokens import MemoryNonceStore, TokenSigner

__version__ = "0.1.0"

__all__ = [
    "Confirmer",
    "confirm",
    "client_supports_elicitation",
    "restrict",
    "restrict_from_env",
    "is_read_only",
    "AskFirstError",
    "ConfirmationRequired",
    "Declined",
    "ConfirmationUnavailable",
    "TokenSigner",
    "MemoryNonceStore",
]
