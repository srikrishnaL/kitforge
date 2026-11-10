# Changelog

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
