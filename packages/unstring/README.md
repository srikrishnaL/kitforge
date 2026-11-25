# unstring

Some MCP clients and models send tool arguments as JSON strings: `"pages": "[{\"title\": \"A\"}]"`
where the tool's schema says array, `"limit": "5"` where it says integer. The server rejects
the call with `expected array, received string` (or `Input should be a valid list`), and the
agent retries, gives up, or writes nothing.

unstring repairs those arguments before the server sees them, guided by the tool's own
`inputSchema`. A value is decoded only where the schema does not allow a string, and only when
the decoded value has a type the schema does allow, so real strings are never touched. Use it
as a stdio proxy around any MCP server (no code changes, any language) or as one function.

```bash
pip install unstring
```

## Quick start: any stdio server

Put `unstring proxy --` in front of the server command in your client's config:

```json
{
  "mcpServers": {
    "notion": {
      "command": "unstring",
      "args": ["proxy", "--", "npx", "-y", "@notionhq/notion-mcp-server"]
    }
  }
}
```

Or with `uvx`, nothing to install first: `"command": "uvx", "args": ["unstring", "proxy", "--", ...]`.

Each repair is reported on stderr (most clients show it in their MCP log):

```text
unstring: create_pages: pages: string -> array; pages[0].position: string -> integer
```

### Proxy options

| Option | What it does |
|---|---|
| `--wrap` | Also turn a single value into a one-item list where the schema wants an array (`"ids": 4` becomes `[4]`). Off by default. |
| `--quiet` | Don't report repairs. |
| `--log FILE` | Append repair reports to a file instead of stderr. |

The proxy learns each tool's schema from the `tools/list` results the client asks for. If a
call names a tool it has not seen (the client cached the list), it asks the server for the
tool list itself, holds the call until the answer is in, and then sends it on, repaired. All
other messages pass through byte for byte.

## In your own server or client: one function

```python
from unstring import repair

schema = {
    "type": "object",
    "properties": {
        "pages": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "position": {"type": "integer"}}}},
        "query": {"type": "string"},
        "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
}
result = repair({"pages": '[{"title": "A", "position": "2"}]', "query": "[1, 2]",
                 "limit": "10"}, schema)

print(result.value)
# {'pages': [{'title': 'A', 'position': 2}], 'query': '[1, 2]', 'limit': 10}
print([str(f) for f in result.fixes])
# ['pages: string -> array', 'pages[0].position: string -> integer', 'limit: string -> integer']
```

`query` stays a string: the schema says it is one. `repair_arguments(args, schema)` returns
only the value. Check one call from the command line:

```console
$ unstring fix --schema tools.json --tool create_pages '{"pages": "[{\"title\": \"A\"}]"}'
repaired pages: string -> array
{
  "pages": [
    {
      "title": "A"
    }
  ]
}
```

`--schema` takes an `inputSchema`, a tool, or a saved `tools/list` result. The exit code is 2
when something was repaired, 0 when nothing needed it.

## What gets repaired

- JSON text for arrays, objects, integers, numbers, booleans and `null` where the schema wants
  those types, at any depth: inside arrays, nested objects, `$ref`/`$defs`, `anyOf`/`oneOf`/
  `allOf`, `additionalProperties`, `patternProperties`, `prefixItems`.
- Double-encoded JSON (`"\"[1, 2]\""`).
- Python literals some bridges send (`"['a', 'b']"`, `"{'k': True}"`), parsed safely with
  `ast.literal_eval`, never `eval`.
- Arrays that arrived as index-keyed objects (`{"0": x, "1": y}` where the schema wants an
  array), as some HTTP bridges send them.
- `"True"`/`"FALSE"` for booleans, `"5.0"` for an integer field.
- The whole `arguments` object sent as one JSON string.

## Limits

- Where the schema allows a string (`"type": ["string", "array"]`, an untyped field, `{}`),
  the string is left alone, because there is no safe way to tell. Give the field a type if
  you want it repaired.
- `unstring proxy` works with stdio servers. For a remote (Streamable HTTP) server, call
  `repair` in the server where the arguments arrive.
- It fixes types, not values: a wrong enum, a missing required field or a bad date still
  reaches the server and fails there, as it should.
- Strings like `"007"` or `"1,2"` are not valid JSON and are not guessed at.

## Why this exists

Clients keep sending stringified arguments, intermittently, and every server works around it
on its own (`tryParseJson` helpers, `z.preprocess`, accept-a-string-too unions):

- [anthropics/claude-code#26094](https://github.com/anthropics/claude-code/issues/26094): Claude
  Desktop serializes MCP object parameters as strings, breaking Notion MCP writes (21 upvotes).
- [makenotion/notion-mcp-server#215](https://github.com/makenotion/notion-mcp-server/issues/215):
  `pages` array parameter received as a string.
- [anthropics/claude-code#86459](https://github.com/anthropics/claude-code/issues/86459): array
  parameters silently stringified mid-call, breaking any tool expecting `List[str]`.
- [n8n-io/n8n#33864](https://github.com/n8n-io/n8n/issues/33864): typed tool parameters arrive as
  strings, `expected array, received string`.
- [czlonkowski/n8n-mcp#814](https://github.com/czlonkowski/n8n-mcp/issues/814): nested arrays and
  objects mis-serialized by HTTP MCP clients (strings, and arrays turned into `{"0": ..}`
  objects); the server's `tryParseJson` misses cases.
- [continuedev/continue#13350](https://github.com/continuedev/continue/issues/13350) and
  [exa-labs/exa-mcp-server#477](https://github.com/exa-labs/exa-mcp-server/issues/477): the same
  error from Continue and Exa.
- [anomalyco/opencode#57023](https://github.com/anomalyco/opencode/issues/57023): the whole
  `arguments` object passed as a string.

## License

MIT
