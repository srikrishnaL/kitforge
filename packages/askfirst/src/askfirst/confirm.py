"""Ask the user before a tool does something destructive.

``Confirmer.__call__`` is awaited at the top of a tool body. It returns when the
user has agreed and raises otherwise:

1. If the client supports elicitation, it shows a yes/no prompt to the user.
   Yes returns; no, cancel or no answer in time raises ``Declined``.
2. If it does not (or the prompt cannot be sent), it raises
   ``ConfirmationRequired``. Its message asks the model to check with the user
   and call the tool again with the same arguments and a signed, single-use
   token. That second call returns.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Mapping, Optional

from .errors import ConfirmationRequired, ConfirmationUnavailable, Declined
from .tokens import TokenSigner

__all__ = ["Confirmer", "confirm", "client_supports_elicitation"]

log = logging.getLogger("askfirst")

_MODES = ("auto", "elicit", "token")
_schema_cache: Dict[str, Any] = {}


def client_supports_elicitation(ctx: Any) -> bool:
    """True if the connected client declared the elicitation capability.

    Works with the MCP Python SDK 1.x ``FastMCP`` context, the 2.x ``MCPServer``
    context, and the standalone ``fastmcp`` package's context.
    """
    if ctx is None:
        return False
    caps = None
    try:
        caps = ctx.client_capabilities  # SDK 2.x
    except Exception:
        caps = None
    if caps is None:
        try:
            params = ctx.session.client_params  # SDK 1.x, fastmcp
            caps = params.capabilities if params is not None else None
        except Exception:
            caps = None
    return getattr(caps, "elicitation", None) is not None


def _confirm_schema() -> Any:
    """A one-field pydantic model: ``confirm: bool`` (pydantic ships with the SDK)."""
    model = _schema_cache.get("model")
    if model is None:
        from pydantic import BaseModel, Field

        class Confirmation(BaseModel):
            confirm: bool = Field(title="Confirm", description="Check to go ahead.")

        model = _schema_cache["model"] = Confirmation
    return model


class Confirmer:
    """Confirmation settings shared by your tools. Create one per server.

    Args:
        secret: HMAC key for confirmation tokens. Default: random at start-up.
        ttl: Seconds a confirmation token stays valid (default 300).
        mode: ``"auto"`` (prompt if the client can, else token), ``"elicit"``
            (prompt only; refuse on clients that cannot prompt) or ``"token"``
            (always use the two-call token flow).
        timeout: Seconds to wait for the user to answer a prompt. ``None`` waits
            as long as the client does. No answer in time counts as no.
        token_param: Name of the tool parameter that receives the token. Only
            used in the message to the model.
        store: Shared used-token store for multi-process servers (see
            ``MemoryNonceStore``).
    """

    def __init__(
        self,
        secret: Optional[bytes] = None,
        *,
        ttl: int = 300,
        mode: str = "auto",
        timeout: Optional[float] = None,
        token_param: str = "confirm_token",
        store: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        if mode not in _MODES:
            raise ValueError(f"mode must be one of {_MODES}, not {mode!r}")
        kwargs: Dict[str, Any] = {"ttl": ttl, "store": store}
        if clock is not None:
            kwargs["clock"] = clock
        self.signer = TokenSigner(secret, **kwargs)
        self.mode = mode
        self.timeout = timeout
        self.token_param = token_param

    async def __call__(
        self,
        ctx: Any,
        message: str,
        *,
        action: str,
        args: Optional[Mapping[str, Any]] = None,
        token: Optional[str] = None,
        subject: str = "",
    ) -> str:
        """Return once the user has confirmed ``message``; raise otherwise.

        Args:
            ctx: The tool's MCP ``Context`` (``None`` outside a request).
            message: What will happen, in words the user understands, e.g.
                ``"Delete branch release/2.1 in acme/api? This cannot be undone."``
            action: The tool name. Tokens only work for this action.
            args: The arguments that decide what happens. Tokens only work for
                the same arguments, so a model cannot get a token for a harmless
                call and use it on another.
            token: The value of the tool's token parameter (``None`` or ``""``
                on a first call).
            subject: Who is asking, e.g. the authenticated user id, so one
                user's token does not work for another.

        Returns:
            ``"elicitation"`` or ``"token"``: how the user confirmed.
        """
        if not action:
            raise ValueError("action is required")
        if self.mode != "token" and client_supports_elicitation(ctx):
            outcome = await self._elicit(ctx, message)
            if outcome is not None:
                return outcome
            # The prompt could not be sent; fall through to the token flow.
        elif self.mode == "elicit":
            raise ConfirmationUnavailable(
                f"This action needs the user's confirmation, and this client cannot ask for it: "
                f"{message} Tell the user to run it from a client that supports MCP elicitation."
            )
        if self.mode == "elicit":
            raise ConfirmationUnavailable(
                f"This action needs the user's confirmation, and the prompt could not be shown: "
                f"{message}"
            )
        return self._token_flow(message, action, args, token, subject)

    async def _elicit(self, ctx: Any, message: str) -> Optional[str]:
        schema = _confirm_schema()
        try:
            if self.timeout is not None:
                import anyio

                with anyio.fail_after(self.timeout):
                    result = await ctx.elicit(message, schema)
            else:
                result = await ctx.elicit(message, schema)
        except TimeoutError:
            raise Declined(
                f"The user did not answer the confirmation prompt in time: {message} "
                "Nothing was done.",
                action="timeout",
            ) from None
        except Exception as exc:
            # No back-channel (stateless or 2026-07-28 connections), the client
            # rejected the method, a transport error: the user saw nothing.
            log.info("askfirst: elicitation failed (%s: %s); using a confirmation token",
                     type(exc).__name__, exc)
            return None
        action = getattr(result, "action", None)
        if action == "accept":
            data = getattr(result, "data", None)
            if getattr(data, "confirm", False) is True:
                return "elicitation"
            raise Declined(f"The user did not confirm: {message} Nothing was done.")
        if action == "cancel":
            raise Declined(
                f"The user dismissed the confirmation prompt: {message} Nothing was done.",
                action="cancel",
            )
        raise Declined(
            f"The user declined: {message} Nothing was done. Do not retry unless the user asks again."
        )

    def _token_flow(
        self,
        message: str,
        action: str,
        args: Optional[Mapping[str, Any]],
        token: Optional[str],
        subject: str,
    ) -> str:
        reason = None
        if token:
            reason = self.signer.check(token, action, args, subject)
            if reason is None:
                return "token"
        fresh = self.signer.issue(action, args, subject)
        why = {
            None: "",
            "expired": "The confirmation token you sent has expired. ",
            "used": "The confirmation token you sent was already used. ",
            "mismatch": "The confirmation token you sent was issued for a different call. ",
            "malformed": "The confirmation token you sent is not valid. ",
        }[reason]
        text = (
            f"{why}Confirmation required. This call was not run.\n"
            f"Action: {message}\n"
            f"Ask the user whether to go ahead, quoting the action above. Only if they clearly say yes, "
            f"call {action} again with exactly the same arguments plus "
            f'{self.token_param}="{fresh}". '
            f"The token works once, for this exact call, for {self.signer.ttl} seconds."
        )
        raise ConfirmationRequired(text, token=fresh, expires_in=self.signer.ttl, reason=reason)


_default: Optional[Confirmer] = None


async def confirm(
    ctx: Any,
    message: str,
    *,
    action: str,
    args: Optional[Mapping[str, Any]] = None,
    token: Optional[str] = None,
    subject: str = "",
) -> str:
    """``Confirmer()(...)`` with default settings, shared by the whole process."""
    global _default
    if _default is None:
        _default = Confirmer()
    return await _default(ctx, message, action=action, args=args, token=token, subject=subject)
