# mcptest

Automated tests, linting and breaking-change checks for [MCP](https://modelcontextprotocol.io) servers.

You build an MCP server, then poke at it by hand in the Inspector. mcptest turns that into tests
that run in CI: start the server, call its tools, check what an agent will actually see, and fail
the build when a change would break the agents and clients that depend on it.

- **pytest plugin**: an `mcp` fixture connected to your server, plus schema snapshots.
- **Linter**: 15 rules for tool names, descriptions and schemas, including hidden instructions
  aimed at the model ("tool poisoning").
- **Breaking-change diff**: removed tools, new required parameters, narrowed types, removed
  enum values, removed output fields.
- **CLI** for any server in any language: `mcptest lint -- node dist/index.js`.
- **No dependencies.** It speaks the protocol itself over stdio and Streamable HTTP, so an SDK
  upgrade on your side never breaks your tests.

```bash
pip install pytest-mcp-server
```

The package is published on PyPI as `pytest-mcp-server`; the command and the Python import are
both `mcptest`.

Python 3.9+. Works with servers written in any language.

## Check a server in one line

```console
$ mcptest lint -- python my_server.py
error   MCP001 bad name!: invalid tool name 'bad name!'
error   MCP008 get_file: required parameter 'missing' is not in properties
error   MCP012 get_file: description says '<IMPORTANT>', 'Before using any tools, you must',
                              'read ~/.ssh', 'Do not tell the user'
error   MCP003 noschema: inputSchema must be an object schema with "type": "object"
warning MCP005 Get-File: description is only 1 characters: 'x'
warning MCP011 Get-File: easily confused with 'get_file'
warning MCP007 get_file: parameter 'path' has no description

my-server: 4 tools, about 146 tokens of definitions; 4 errors, 3 warnings, 0 info
```

Even well-known servers have gaps an agent pays for. On 2026-11-06 the official reference
filesystem server (`@modelcontextprotocol/server-filesystem`) got 18 warnings, nearly all
parameters with no description, which leaves the model guessing what to pass.

The exit code is 1 when there is a finding at `--fail-on` level or worse (default `error`), so
it drops straight into CI. Other commands:

```bash
mcptest tools -- python my_server.py                    # print the tool list as JSON
mcptest call add '{"a": 2, "b": 3}' -- python my_server.py
mcptest snapshot -o mcp-tools.json -- python my_server.py
mcptest diff mcp-tools.json -- python my_server.py      # exit 1 on breaking changes
mcptest lint --url http://localhost:8000/mcp --header "Authorization: Bearer $TOKEN"
mcptest lint --rules                                    # list every rule
```

Anything after `--` is the command that starts your server over stdio: `npx -y @acme/server`,
`uv run server.py`, `docker run -i --rm acme/mcp`, and so on.

## Test a server with pytest

Tell pytest how to start the server once:

```toml
# pyproject.toml
[tool.pytest.ini_options]
mcp_command = "python -m my_server"
# or, for a server that is already running:
# mcp_url = "http://localhost:8000/mcp"
```

Then write ordinary tests. The `mcp` fixture is a connected client, and the server is stopped
when the test ends:

```python
from mcptest import assert_lint_clean


def test_add(mcp):
    result = mcp.call_tool("add", {"a": 2, "b": 3})
    assert not result.is_error
    assert result.text == "5"
    assert result.structured == {"result": 5}


def test_bad_input_is_a_tool_error(mcp):
    assert mcp.call_tool("add", {"a": "two"}).is_error


def test_tools_are_well_described(mcp):
    assert_lint_clean(mcp)            # fails on warnings and errors


def test_no_surprise_schema_changes(mcp, mcp_snapshot):
    mcp_snapshot(mcp)
```

A test can pick a different server with a marker, and `mcp_server` starts as many as you need:

```python
import pytest


@pytest.mark.mcp_server(command=["node", "dist/index.js"], env={"API_KEY": "test"})
def test_node_server(mcp):
    assert "search" in [t["name"] for t in mcp.list_tools()]


def test_two_versions(mcp_server):
    old = mcp_server(["python", "server_v1.py"])
    new = mcp_server(url="http://localhost:9000/mcp")
    ...
```

## Schema snapshots

`mcp_snapshot(mcp)` stores the tool list in `__mcp_snapshots__/<test file>/tools.json` on the
first run and compares against it afterwards. Commit the file. When the tools change, the test
fails and says which changes are breaking:

```text
MCP tools differ from snapshot tools.json (3 breaking, 4 compatible):
  BREAKING add: new required parameter 'c'
  BREAKING add: outputSchema was removed
  BREAKING echo: tool was removed
  ok       add: parameter 'a' type changed integer -> number
  ...
Run pytest --mcp-update-snapshots to accept these changes.
```

Pass `allow_compatible=True` to fail only on breaking changes.

## Lint rules

| Code | Level | What it catches |
| --- | --- | --- |
| MCP001 | error | Tool name is not 1-128 characters of `A-Z a-z 0-9 _ - .` |
| MCP002 | error | Two tools share a name |
| MCP003 | error | `inputSchema` missing or not `"type": "object"` |
| MCP004 | warning | No description |
| MCP005 | warning | Description under 20 characters |
| MCP006 | warning | Description over 1,024 characters |
| MCP007 | warning | Parameter has no description |
| MCP008 | error | `required` names a parameter that isn't defined |
| MCP009 | warning | Parameter has no type |
| MCP010 | warning | All definitions together exceed 32,000 characters (about 8k tokens per turn) |
| MCP011 | warning | Two names differ only by case or separators (`get_file`, `Get-File`) |
| MCP012 | error | Text that tries to instruct the model: "ignore previous instructions", "do not tell the user", `<IMPORTANT>` blocks, reading `~/.ssh` |
| MCP013 | error | Invisible or bidirectional control characters |
| MCP014 | error | `outputSchema` is not `"type": "object"` |
| MCP015 | info | Parameter description just repeats its name |

Skip rules with `--ignore MCP005,MCP015` or `assert_lint_clean(mcp, ignore=["MCP005"])`.
MCP012 is a heuristic: it catches common tool-poisoning phrasing, not every attack.

## Use the client directly

```python
from mcptest import MCPClient

with MCPClient.stdio(["python", "server.py"], env={"DEBUG": "1"}, timeout=10) as client:
    print(client.server_info, client.protocol_version)
    for tool in client.list_tools():          # follows pagination
        print(tool["name"])
    result = client.call_tool("search", {"q": "mcp"})
    print(result.text, result.is_error)
```

When a server dies, mcptest shows its last stderr lines, and it points out anything printed to
stdout that isn't protocol (the most common stdio bug):

```text
MCPTransportError: server closed stdout (exit code 3)
  server stderr (last lines):
    fatal: database not configured
```

## In CI

```yaml
- run: pip install pytest-mcp-server
- run: mcptest lint --fail-on warning -- python -m my_server
- run: mcptest diff mcp-tools.json -- python -m my_server
```

## Tested against real servers

Checked on 2026-11-06 with mcptest 0.1.0: connect, list tools, call a tool, snapshot twice.

| Server | Transport | Tools | Tool call | Lint findings |
| --- | --- | --- | --- | --- |
| @modelcontextprotocol/server-everything | stdio, Streamable HTTP | 14 | ok | 1 warning |
| @modelcontextprotocol/server-filesystem | stdio | 14 | ok | 18 warnings |
| @modelcontextprotocol/server-memory | stdio | 9 | ok | 4 warnings |
| @modelcontextprotocol/server-sequential-thinking | stdio | 1 | ok | 1 warning |
| @playwright/mcp | stdio, Streamable HTTP | 25 | returned as a tool error (no browser in the test machine) | 3 warnings |
| mcp-server-time | stdio | 3 | ok | none |
| mcp-server-git | stdio | 14 | ok | 24 warnings |
| mcp-server-fetch | stdio | 1 | not called (needs network) | none |
| Official Python SDK 2.3 (`MCPServer`) | stdio, Streamable HTTP | 2 | ok | 3 warnings |

Almost all warnings are parameters with no description (MCP007) or very short descriptions (MCP005).

## Limits

- Covers tools fully, resources and prompts through the client only (no lint rules for them yet).
- No OAuth flow: for protected HTTP servers, pass a token with `--header` or `headers=`.
- The legacy HTTP+SSE transport (two endpoints) is not supported; Streamable HTTP and stdio are.

## License

MIT
