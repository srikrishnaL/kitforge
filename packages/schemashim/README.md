# schemashim

Make MCP and agent tool schemas work on Gemini and OpenAI strict mode, then turn the model's
arguments back into what the tool expects. Use it as a library, a CI check, or a proxy that
fixes any MCP server without touching its code.

Gemini and OpenAI's `strict: true` function calling accept only a subset of JSON Schema, so one
ordinary tool (a pydantic `$ref`, `Optional[int]`, an array without `items`, an `enum` with `""`,
an untyped field) makes **every** request fail with `400 INVALID_ARGUMENT` or `Invalid schema`.
schemashim rewrites a schema into the subset each provider accepts, says what it changed, and
undoes the changes on the arguments the model sends back.

```bash
pip install schemashim
```

Python 3.9+, no dependencies.

## Fix an MCP server you didn't write

Gemini CLI, Cline, Kilo Code, opencode and other clients fail with `400 INVALID_ARGUMENT` or
`Invalid schema for function` when one server has a tool they can't send. Wrap that server in
`schemashim proxy` in your client's MCP config. Its tools then work as they are, whatever
language the server is written in:

```json
{
  "mcpServers": {
    "fetch": {
      "command": "uvx",
      "args": ["schemashim", "proxy", "--target", "gemini", "--",
               "uvx", "mcp-server-fetch"]
    }
  }
}
```

Everything after `--` is the command you had before (`npx -y @acme/server`, `docker run -i
--rm acme/mcp`, `python server.py`...). Use `--target openai-strict` for clients that call
OpenAI with `strict: true`. The proxy rewrites each tool's `inputSchema` in `tools/list`, maps
the model's arguments back on every `tools/call`, and passes everything else through unchanged.
It logs what it changed to stderr (your client's MCP log); `--quiet` turns that off. No `uv`?
`pip install schemashim` and use `"command": "schemashim"`. For a remote (HTTP) server, wrap
a stdio bridge such as `npx -y mcp-remote https://example.com/mcp`.

## Fix one schema

```python
from schemashim import adapt

schema = {
    "type": "object",
    "$defs": {"Point": {"type": "object", "required": ["lat", "lon"],
                        "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}}},
    "properties": {
        "start": {"$ref": "#/$defs/Point"},
        "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
        "tags": {"type": "array"},
    },
    "required": ["start"],
}

shim = adapt(schema, "gemini")          # or "openai-strict"
shim.schema      # send this as the function's parameters
for change in shim.changes:
    print(change)
# start: inlined $ref #/$defs/Point (references are not supported)
# limit: null branch of anyOf changed to nullable
# tags: array without an items type: passed as a JSON string

# When the model calls the tool, map its arguments back before calling the real tool:
shim.restore({"start": {"lat": 1, "lon": 2}, "tags": '["a", "b"]'})
# {'start': {'lat': 1, 'lon': 2}, 'tags': ['a', 'b']}
```

`shim.ok` is `True` when the original was already accepted as is; `check(schema, target)`
returns only the list of changes.

## Bridge MCP tools to Gemini or OpenAI

```python
from schemashim import adapt_tools

tools = (await session.list_tools()).tools        # mcp.types.Tool objects, or dicts
kit = adapt_tools(tools, "openai-strict")          # or "gemini"

response = client.chat.completions.create(model=..., messages=..., tools=kit.declarations)
for call in response.choices[0].message.tool_calls or []:
    name, args = kit.restore(call.function.name, json.loads(call.function.arguments))
    result = await session.call_tool(name, args)
```

For Gemini, `kit.declarations` are `functionDeclarations`:
`types.Tool(function_declarations=kit.declarations)` in `google-genai`, or
`{"functionDeclarations": kit.declarations}` over REST. For the OpenAI Responses API use
`[{"type": "function", **t.function} for t in kit]`.

Tool names are fixed too: `github/search_issues` becomes `github_search_issues`, names over 64
characters get a short hash, and two tools never end up with the same name. `kit.restore` maps
the name back.

## What it changes

| Problem in the schema | Gemini | OpenAI strict |
| --- | --- | --- |
| `$ref` / `$defs` / `definitions` | inlined | inlined |
| Recursive `$ref` (a tree) | the recursive field is a JSON string | same |
| `anyOf: [X, {type: null}]`, `type: [X, "null"]`, OpenAPI `nullable` | `nullable: true` | `type: [X, "null"]` |
| `type: [A, B]`, `oneOf` | `anyOf` | `anyOf` |
| `allOf` | merged | merged |
| Optional properties | kept optional | made required and nullable (strict mode needs every property in `required`); `restore` drops the nulls |
| `additionalProperties` | removed | set to `false` on every object |
| Array without `items`, object without `properties`, untyped "any" field, tuple arrays | sent as a JSON string; `restore` decodes it | same |
| `enum` with `""` or non-strings | `""` dropped; number enums move to the description | kept |
| `const` | one-value `enum` | kept |
| Missing `type` | inferred from `properties`, `items`, `enum`, bounds | same |
| `required` naming undefined properties, `"required": true` on a property | fixed | fixed |
| Root not an object, root `anyOf` of objects | made an object / merged | same |
| `format`, `exclusiveMinimum`, `multipleOf`, `minLength`, `default`, regex lookarounds... that the target rejects | written into the description so the model still sees them | same |
| `$schema`, `$id`, `not`, `if`/`then`, `patternProperties`... | removed | removed |

`restore` also turns `5.0` back into `5` for integer fields (Gemini sends numbers as floats).

## In CI

Check your server's tools before your users hit the error:

```bash
schemashim check tools.json                     # exit 1 if Gemini or OpenAI strict would reject a tool
schemashim check tools.json --target gemini --json
schemashim fix tools.json --target openai-strict > tools.openai.json
```

The file can be a schema, one tool, a list of tools, a `tools/list` response or an
[mcptest](../mcptest/) snapshot, and `-` reads stdin. To check a running MCP server:

```bash
mcptest tools -- python my_server.py | schemashim check -
```

## Limits

- A JSON-string field is a workaround: the model sees "JSON-encoded value" instead of the real
  shape, so describe what goes in it. It only happens where the provider cannot express the
  shape at all.
- Strict mode can't express "optional": the model sends `null`, and `restore` removes it. If a
  field was already nullable, a `null` is kept, because it may be meaningful.
- Merging a root `anyOf` makes fields that are not in every choice optional; the tool must still
  validate which combination is valid.
- Gemini's newer `parametersJsonSchema` field accepts more of JSON Schema. schemashim targets the
  classic `parameters` field that most clients and SDKs send, and its output is valid there too.
- Only local `#/$defs/...` and `#/definitions/...` references are followed; remote ones become
  JSON-string fields.
- Provider rules change. If a schema still gets rejected, open an issue with the error text.

## Why this exists

Every MCP client and agent framework that talks to Gemini or OpenAI strict mode hits this and
writes its own partial sanitizer. A few of the reports:

- [google-gemini/gemini-cli#13326](https://github.com/google-gemini/gemini-cli/issues/13326): Gemini API rejects MCP tool schemas with `$defs` references
- [modelcontextprotocol/servers#1624](https://github.com/modelcontextprotocol/servers/issues/1624): mcp-server-fetch's schema can't be used with Gemini 2.5 Pro
- [Portkey-AI/gateway#1203](https://github.com/Portkey-AI/gateway/issues/1203): not converting JSON Schema to Gemini compatible tool definitions
- [chatboxai/chatbox#2606](https://github.com/chatboxai/chatbox/issues/2606): Gemini tool integration fails with `INVALID_ARGUMENT`
- [langchain-ai/langchain-google#535](https://github.com/langchain-ai/langchain-google/issues/535): "should be non-empty for OBJECT type"
- [BerriAI/litellm#18442](https://github.com/BerriAI/litellm/issues/18442): "functionDeclaration parameters schema should be of type OBJECT"
- [Kilo-Org/kilocode#15512](https://github.com/Kilo-Org/kilocode/issues/15512), [cline/cline#11162](https://github.com/cline/cline/issues/11162), [CherryHQ/cherry-studio#18890](https://github.com/CherryHQ/cherry-studio/issues/18890), [anomalyco/opencode#48073](https://github.com/anomalyco/opencode/issues/48073): empty enums, undefined required properties, arrays without items, nullable arrays
- [openclaw/openclaw#166877](https://github.com/openclaw/openclaw/issues/166877), [NousResearch/hermes-agent#104796](https://github.com/NousResearch/hermes-agent/issues/104796), [cloudflare/mcp-server-cloudflare#378](https://github.com/cloudflare/mcp-server-cloudflare/issues/378): one MCP tool breaks every strict-mode request

GitHub search finds over 300 issues titled "tool schema" + Gemini alone. The demand evidence
is in [`demand/`](../../demand/) (idea `tool-schema-compat`).

## License

MIT
