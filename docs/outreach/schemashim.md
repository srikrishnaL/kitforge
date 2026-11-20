# schemashim outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. Status on 2026-11-20 (GitHub
search): kilocode#15512 open, last activity 11-16; chatbox#2606 open but quiet since 2025-11;
cloudflare#378 open, no comments. Two newer reports (rea#12387, cherry-studio#24524) are added
at the end; they need schemashim 0.2.1. Check again before posting. Most affected clients are TypeScript, so these replies
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

## morluto/rea#12387 (`"^[^\uD800-\uDFFF]*$" is not a "regex"` on every DeepSeek request)

https://github.com/morluto/rea/issues/12387 (post only after schemashim 0.2.1 is on PyPI)

> Until a release with the fix ships, a client-side workaround that doesn't touch the catalog
> (so the sha256 check still passes): run the server behind
> [schemashim](https://github.com/srikrishnaL/kitforge/tree/main/packages/schemashim)'s proxy,
> e.g. `uvx schemashim proxy --target openai-strict -- node scripts/rea.mjs`. It rewrites
> `tools/list` on the way to the client: the surrogate-only pattern is dropped (valid JSON
> text can't carry lone surrogates, so it never rejects anything), other `\u`-escaped patterns
> move into the field description, and `tools/call` passes through unchanged.

## CherryHQ/cherry-studio#24524 (a `\u` escape in a third-party MCP tool's `pattern` kills the turn)

https://github.com/CherryHQ/cherry-studio/issues/24524 (post only after schemashim 0.2.1 is on PyPI)

> For the "detect it before the turn starts" part: `schemashim check tools.json --target
> openai-strict` ([schemashim](https://github.com/srikrishnaL/kitforge/tree/main/packages/schemashim),
> `pip install schemashim`, Python, no dependencies) names each tool and field an
> OpenAI-compatible validator would reject, including `\u`-escaped patterns, and exits 1. The
> README's rule table lists the rewrites, in case they help for a fix inside Cherry Studio.
