# cleanpipe

A stdio MCP server talks JSON-RPC on stdout, so a single banner, log line or `print()` there
breaks the stream: the client times out handshaking, shows `Unexpected token` errors, or drops
the server. cleanpipe wraps any server so only JSON-RPC messages reach the client and
everything else goes to stderr, without waiting for the server's maintainers to ship a fix.

```
pip install cleanpipe        # or run it with no install: uvx cleanpipe -- <server command>
```

No dependencies. Python 3.9+. Works with servers written in any language.

## Use it

Put `cleanpipe --` in front of the server command in your client's config.

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.gitnexus]
command = "uvx"
args = ["cleanpipe", "--", "npx", "-y", "gitnexus@latest", "mcp"]
```

**Claude Code**:

```
claude mcp add gitnexus -- uvx cleanpipe -- npx -y gitnexus@latest mcp
```

**Claude Desktop, Cursor, VS Code** and other JSON configs:

```json
{
  "mcpServers": {
    "gitnexus": {
      "command": "uvx",
      "args": ["cleanpipe", "--", "npx", "-y", "gitnexus@latest", "mcp"]
    }
  }
}
```

The first time the server writes something that isn't JSON-RPC, cleanpipe says so on stderr
(your client's MCP log), and when the server exits it prints how many lines it moved.

### Options

```
cleanpipe [--log FILE | --drop] [--quiet] -- <server command> [args...]
```

| Option | What it does |
| --- | --- |
| `--log FILE` | Append the moved output to `FILE` instead of stderr. |
| `--drop` | Throw the moved output away. |
| `--quiet` | Don't print cleanpipe's own notes. |

The server's exit code is passed on, its stderr is left alone, and SIGTERM / Ctrl-C are passed
to it. `python -m cleanpipe` works too.

## What counts as noise

cleanpipe reads the server's stdout one line at a time (MCP stdio is newline-delimited JSON):

| The server writes | The client gets | stderr gets |
| --- | --- | --- |
| `{"jsonrpc":"2.0","id":1,"result":{...}}` | the line, byte for byte | |
| `Server started on stdio` | nothing | the line |
| `{"level":"info","event":"startup"}` (a JSON log line) | nothing | the line |
| `Loading...{"jsonrpc":"2.0","id":1,...}` (a log printed without a newline) | the message | `Loading...` |
| two messages on one line | both, one per line | |
| a UTF-8 byte order mark, `\r\n`, ANSI colors, invalid UTF-8 in a log line | the messages only | the rest |

A message is a JSON object with `"jsonrpc": "2.0"` and a `method`, or an `id` with a `result`
or `error`, or a batch (array) of them. A JSON-RPC message nested inside a log object is not
forwarded. What the client sends to the server is copied unchanged.

## For server maintainers: `cleanpipe check`

```
$ cleanpipe check -- python my_server.py
handshake: ok, 4 tool(s)
stdout noise: 2 line(s) that are not JSON-RPC (clients may fail the handshake; log to stderr instead):
  | Loading model weights...
  | [INFO] ready
```

It starts the server, sends `initialize`, `notifications/initialized` and `tools/list`, keeps
listening for half a second more, and exits 0 when stdout is clean, 1 when it found noise and
2 when the server didn't answer (`--timeout S`, default 10). `--json` prints the report as
JSON for CI. From Python:

```python
from cleanpipe import check, split

report = check(["python", "my_server.py"])
assert report.clean, report.noise

split(b'Loading...{"jsonrpc":"2.0","id":1,"result":{}}\n')
# ([b'{"jsonrpc":"2.0","id":1,"result":{}}'], [b'Loading...'])
```

## Limits

- Only stdio servers. Streamable HTTP servers don't share a channel with their logs.
- A message pretty-printed over several lines is not put back together; each line is noise.
  No MCP SDK writes messages that way.
- Output the server buffers and only flushes at exit (Python's `print()` to a pipe without
  `flush=True`) can't be moved until it is flushed; it still never reaches the client.
- cleanpipe needs Python on the machine. With `uvx` it is fetched on first use.
- Some clients tolerate stray lines (the official Python SDK client logs them and carries on);
  others fail the handshake. cleanpipe is for the second kind and harmless with the first.
- The real fix is in the server: log to stderr. `cleanpipe check` in CI keeps it fixed.

## Why this exists

The same bug is reported again and again, one server at a time, and users wait for each fix:

- [GitNexus#901](https://github.com/abhigyanpatwari/GitNexus/issues/901): "MCP startup failed:
  timed out handshaking with MCP server after 30s" in Codex, caused by a startup log on stdout.
- [awslabs/mcp#5020](https://github.com/awslabs/mcp/issues/5020) and
  [awslabs/mcp#4991](https://github.com/awslabs/mcp/issues/4991): `print()` calls corrupting
  the stdio stream in AWS MCP servers.
- [happyvertical/smrt#4685](https://github.com/happyvertical/smrt/issues/4685): the launcher's
  own banner (`pnpm exec`) breaks every connection, not the server's code.
- [universal-tool-calling-protocol/typescript-utcp#63](https://github.com/universal-tool-calling-protocol/typescript-utcp/issues/63),
  [CarbonoDev/tailwindcss-mcp-server#2](https://github.com/CarbonoDev/tailwindcss-mcp-server/issues/2),
  [codewithmukesh/dotnet-claude-kit#10](https://github.com/codewithmukesh/dotnet-claude-kit/issues/10):
  the same failure in TypeScript and .NET servers.

GitHub has over 70 such issues opened since September 2026 alone (titles with "stdout",
"stdio" and "MCP"). The existing tools detect the problem; cleanpipe also works around it
from the client side, so you can use the server today.

## License

MIT
