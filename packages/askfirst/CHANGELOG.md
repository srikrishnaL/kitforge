# Changelog

## 0.1.0 (2026-11-09)

First release.

- `Confirmer` / `confirm`: one `await` at the top of a destructive tool. Shows a yes/no
  elicitation prompt when the client supports it; otherwise (or when the prompt cannot be sent,
  e.g. no back-channel) asks the model to check with the user and call again with a signed,
  single-use token bound to the action, the arguments and an optional subject.
  Modes `auto`, `elicit` (refuse without a prompt) and `token`; answer `timeout`.
- Declined, cancelled, unchecked and timed-out prompts all stop the call.
- Errors subclass the MCP SDK's `ToolError`, so the model sees their text on SDK 1.x and 2.x.
- `restrict` / `restrict_from_env`: read-only mode (keeps only `readOnlyHint=True` tools,
  unannotated tools count as writes) and glob allow/deny lists. Removed tools cannot be called
  by name; a pattern that matches nothing is an error.
- Tested against MCP Python SDK 1.10, 1.x latest and 2.x, including the 2026-07-28 protocol.
