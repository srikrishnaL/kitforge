# askfirst

Ask the user before an MCP tool deletes, sends or pays for anything, on every client.

MCP servers are meant to confirm destructive tool calls through *elicitation*, but many clients
don't support it, declare it and then auto-decline, or (on stateless and 2026-07-28 connections)
can't receive the prompt at all, so servers either skip the check or break. askfirst is one
`await` that shows a yes/no prompt when the client can, and otherwise falls back to a signed,
single-use confirmation token bound to the exact call. It also adds a read-only mode that
really removes write tools.

```bash
pip install askfirst
```

Python 3.9+, no dependencies of its own (it uses the pydantic and anyio your MCP SDK already
installs). Works with the official MCP Python SDK 1.x (`FastMCP`) and 2.x
(`MCPServer`).

## Confirm a destructive tool

```python
from mcp.server.fastmcp import FastMCP, Context      # SDK 2.x: from mcp.server.mcpserver import MCPServer, Context
from mcp.types import ToolAnnotations
from askfirst import Confirmer

mcp = FastMCP("git-host")
ask = Confirmer()

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def delete_branch(repo: str, branch: str, ctx: Context, confirm_token: str = "") -> str:
    """Delete a branch. Asks the user first."""
    await ask(ctx, f"Delete branch {branch} in {repo}? This cannot be undone.",
              action="delete_branch", args={"repo": repo, "branch": branch}, token=confirm_token)
    # Only reached once the user said yes.
    return f"Deleted {branch}"
```

What happens:

| Client | First call | Then |
| --- | --- | --- |
| Supports elicitation | The user sees the message with a **Confirm** checkbox | Yes: the tool runs. No, cancel, or no answer within `timeout`: the model gets "The user declined ... Nothing was done." |
| No elicitation, or the prompt can't be sent | The model gets an error: "Confirmation required. This call was not run. Ask the user whether to go ahead ... call delete_branch again with exactly the same arguments plus `confirm_token="..."`" | The second call with the token runs the tool |

The token is an HMAC over the action, the arguments and an optional `subject`, valid for 5
minutes and only once. A token issued for `delete_branch("old")` does not work for
`delete_branch("main")`, an expired or reused token gets a fresh one with the reason, and on a
client that can show prompts a token never replaces the prompt.

The errors are the SDK's own `ToolError`, so their text reaches the model as the tool result
(SDK 2.x hides the text of other exceptions).

### Settings

```python
Confirmer(
    secret=None,              # HMAC key; default random at start-up (tokens die on restart)
    ttl=300,                  # seconds a token is valid
    mode="auto",              # "auto" | "elicit" (prompt or refuse) | "token" (always two calls)
    timeout=None,             # seconds to wait for the user's answer; no answer = no
    token_param="confirm_token",
    store=None,               # shared used-token store for multi-process servers
)
```

Pass `subject=` (the authenticated user id or session id) so one user's token cannot be used
by another. `await askfirst.confirm(ctx, ...)` is the same call with default settings.

## Read-only mode and tool allow/deny lists

```python
from askfirst import restrict_from_env

# ... register all tools first ...
restrict_from_env(mcp)        # MCP_READ_ONLY=true, MCP_ALLOW_TOOLS=list_*,get_*, MCP_DENY_TOOLS=delete_*
mcp.run()
```

Or in code: `restrict(mcp, read_only=True, allow=["list_*"], deny=["delete_*"])`. It returns the
removed names.

- Tools are removed from the server, so they are gone from `tools/list` **and** calling one by
  name fails.
- Read-only keeps only tools annotated `readOnlyHint=True`. A tool with no annotations counts
  as a write tool, so a new tool someone forgot to annotate is hidden, not leaked.
- Deny beats allow. Patterns are case-sensitive globs, and a pattern that matches no tool is an
  error, so a typo can't silently leave `delete_repo` exposed.

## Limits

- The token fallback trusts the model to relay the question to the user. It stops the model
  from running a destructive call on its own initiative or with changed arguments, but a model
  that ignores the instruction could still resend the token. For hard guarantees use
  `mode="elicit"`, which refuses on clients that cannot prompt.
- On SDK 2.x connections using the 2026-07-28 protocol, prompts are carried by multi-round-trip
  requests (`InputRequiredResult`); askfirst does not produce those yet and uses the token
  fallback there. Native support is planned.
- A declined prompt is a no, even when the client declined it automatically (a known client
  bug). That is the safe side.
- The used-token store is in memory. With several workers, pass a shared `store` (an object
  with `use(nonce, expires_at) -> bool`) and a fixed `secret`.
- `restrict` acts on tools registered when you call it. Call it last.
- The standalone `fastmcp` package: `confirm` works with its `Context`; `restrict` does not (use
  fastmcp's own tag filters there).
- Python only for now; the TypeScript SDK is next if people ask.

## Why this exists

Server authors keep asking for this, and building it by hand is easy to get wrong:

- [MemPalace/mempalace#1981](https://github.com/MemPalace/mempalace/issues/1981): opt-in
  read-only MCP mode plus guarded writes with explicit user confirmation.
- [pulsemcp/mcp-servers#384](https://github.com/pulsemcp/mcp-servers/issues/384): an
  elicitation fallback pattern for clients without native elicitation.
- [erayendes/app-store-connect-mcp#112](https://github.com/erayendes/app-store-connect-mcp/issues/112):
  keep the write guard on clients without elicitation with a preview and a request-bound
  confirmation token.
- [TojiiSamaa/netbox-mcp-rust#4](https://github.com/TojiiSamaa/netbox-mcp-rust/issues/4):
  destructive tools run without confirmation when the client does not support elicitation.
- [PrefectHQ/fastmcp#4929](https://github.com/PrefectHQ/fastmcp/issues/4929): declined and
  cancelled elicitation results are truthy, so the obvious `if await ctx.elicit(...)` guard
  lets refused actions through.
- [anthropics/claude-code#98256](https://github.com/anthropics/claude-code/issues/98256): a
  client that declares elicitation and auto-declines it, so servers can't rely on prompts alone.
- Read-only mode requests: [strausmann/mcp-dockhand#254](https://github.com/strausmann/mcp-dockhand/issues/254),
  [signadot/community#122](https://github.com/signadot/community/issues/122),
  [microsoft/azure-skills#197](https://github.com/microsoft/azure-skills/issues/197).

## License

MIT
