# Changelog

## 0.1.0 (2026-11-14)

First release.

- `cleanpipe -- <server command>`: runs a stdio MCP server and passes only JSON-RPC messages
  to the client on stdout. Banners, log lines (plain or JSON) and `print()` output go to stderr,
  to `--log FILE`, or nowhere with `--drop`. Messages that share a line with log text are cut
  out and forwarded; a byte order mark, `\r\n` and invalid UTF-8 are handled. Client input is
  copied unchanged; the server's exit code is passed on and SIGTERM is forwarded.
- `cleanpipe check -- <server command>`: runs the handshake and `tools/list` and reports any
  stdout noise; exit code 0 clean, 1 noise, 2 no answer; `--json` for CI.
- Python API: `split(line)`, `is_message(obj)`, `check(command)`, `Pipe`, `run`.
