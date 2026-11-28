# Changelog

## 0.3.0 (2026-11-28)

- `schemashim proxy --max-name-length N`: shortens tool names longer than N characters (12 to
  64) and maps each `tools/call` back to the server's real name. For servers whose tool names
  plus the client's own prefix or suffix go over the 64-character limit of Bedrock, OpenAI and
  other model APIs, which fails every request (awslabs/mcp#2395, awslabs/mcp#3181). A short
  name is the start of the original plus an 8-character hash of it, so it is the same every
  time and never collides with another tool's name. Works with or without `--target`, which is
  now optional when `--max-name-length` is given.
- `schemashim proxy --target ...` now also renames tools whose names the target rejects (for
  example dots for `openai-strict`), and maps calls back. Before, only schemas were changed.
- Library: `tool_name(name, target, max_length=64)`, `adapt_tools(..., max_name_length=64)`
  and `adapt_tool(..., max_name_length=64)`; `tool_name(name, None)` keeps to the characters
  every model API accepts. A shortened name no longer has a double `_` before the hash.

## 0.2.1 (2026-11-20)

- `openai-strict`: a `pattern` with a `\uXXXX` or `\u{...}` escape moves into the description,
  because OpenAI-compatible validators (OpenAI, DeepSeek) reject the whole request with
  `"..." is not a "regex"`. The common `^[^\uD800-\uDFFF]*$` pattern, which only rejects lone
  surrogates that JSON text can't contain anyway, is dropped (morluto/rea#12387,
  CherryHQ/cherry-studio#24524).
- `allOf` merging no longer loses constraints: when parts have different `pattern`s, the first
  one the target accepts is kept and the rest go into the description, and conflicting
  `minLength`/`maxLength`/`minimum`/`maximum`/`minItems`/... keep the tightest value. Before,
  only the first part's value survived, silently.

## 0.2.0 (2026-11-13)

- `schemashim proxy --target gemini|openai-strict -- <server command>`: runs any stdio MCP
  server behind schemashim, so its tools work in Gemini and OpenAI strict-mode clients without
  changing the server. Rewrites `inputSchema` in `tools/list` results, maps `tools/call`
  arguments back, passes everything else (including batches and server-to-client requests)
  through unchanged, and logs the changes to stderr (`--quiet` to stop).

## 0.1.0 (2026-11-10)

First release.

- `adapt(schema, "gemini" | "openai-strict")`: rewrites a tool's JSON Schema into the subset the
  provider accepts and lists every change. Inlines `$ref`/`$defs`, turns null branches and type
  lists into `nullable` (Gemini) or `[T, "null"]` (OpenAI), merges `allOf`, changes `oneOf` to
  `anyOf`, closes objects and makes every property required for strict mode, infers missing
  types, fixes `required` lists, drops empty enum values, and moves constraints the provider
  rejects (`format`, `exclusiveMinimum`, `minLength`, `default`, regex lookarounds...) into the
  description. Shapes the provider can't express (recursive schemas, free-form objects, arrays
  without `items`) become JSON-string fields.
- `Shim.restore(args)`: maps the model's arguments back: decodes JSON-string fields, drops the
  nulls strict mode forces for optional fields, turns `5.0` into `5` for integers.
- `adapt_tools(tools, target)` / `ToolKit`: adapts MCP `tools/list` entries or `mcp.types.Tool`
  objects into Gemini `functionDeclarations` or OpenAI `tools`, with valid, unique tool names,
  and maps calls back with `kit.restore(name, args)`.
- `tool_name(name, target)`: valid tool names for each provider.
- CLI: `schemashim check FILE` (exit 1 on problems, `--json`) and `schemashim fix FILE --target`.
