# schemashim outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. All three issues were open on
2026-11-10; check again before posting. Most affected clients are TypeScript, so these replies
offer the rule table and the `check` CLI rather than claiming a drop-in fix.

## Kilo-Org/kilocode#15512 (Gemini 400: invalid MCP parameter schemas, empty enum string)

https://github.com/Kilo-Org/kilocode/issues/15512

> This class of error (empty `enum` values, `$ref`, `anyOf` with null, arrays without `items`,
> integer enums) keeps coming back one schema at a time, so I wrote a small sanitizer for it:
> [schemashim](https://github.com/srikrishnaL/kitforge/tree/main/packages/schemashim)
> (`pip install schemashim`, no dependencies). It's Python, so it won't drop into kilocode,
> but the README's rule table lists every rewrite Gemini needs, and
> `mcptest tools -- <server command> | schemashim check - --target gemini` shows exactly which
> tool and field breaks the request.

## chatboxai/chatbox#2606 (Gemini tool integration fails with INVALID_ARGUMENT)

https://github.com/chatboxai/chatbox/issues/2606

> If it helps narrow down which MCP tool triggers the 400: save the server's `tools/list`
> result and run `schemashim check tools.json --target gemini`
> ([schemashim](https://github.com/srikrishnaL/kitforge/tree/main/packages/schemashim),
> `pip install schemashim`). It lists each field Gemini rejects (`$ref`, `additionalProperties`,
> `anyOf` null branches, arrays without `items`, unsupported `format`s...) and
> `schemashim fix` prints the rewritten declarations, which is a quick way to confirm the fix
> before changing the client's own converter.

## cloudflare/mcp-server-cloudflare#378 (`{"not":{}}` in tool schemas breaks strict function calling)

https://github.com/cloudflare/mcp-server-cloudflare/issues/378

> A quick way to catch this class of problem in CI on the server side:
> `schemashim check tools.json` ([schemashim](https://github.com/srikrishnaL/kitforge/tree/main/packages/schemashim),
> `pip install schemashim`) exits 1 if Gemini or OpenAI strict mode would reject any tool
> schema, and names the field (here, the `not` keyword). It works on a saved `tools/list`
> response, so it doesn't care what language the server is written in.
