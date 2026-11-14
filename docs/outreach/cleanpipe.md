# cleanpipe outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. All three issues were open with
no comments on 2026-11-14; check again before posting. Each offers a workaround for users until
the server is fixed, and `cleanpipe check` to keep the fix from regressing. Don't post where
the maintainers already shipped a fix.

## awslabs/mcp#5020 (dynamodb-mcp-server: generate_data_access_layer prints to stdout)

https://github.com/awslabs/mcp/issues/5020

> Until this is fixed, users can run the server behind
> [cleanpipe](https://github.com/srikrishnaL/kitforge/tree/main/packages/cleanpipe), a small
> wrapper (no dependencies, MIT) that forwards only JSON-RPC lines on stdout and moves
> everything else to stderr:
> `"command": "uvx", "args": ["cleanpipe", "--", "uvx", "awslabs.dynamodb-mcp-server@latest"]`.
> For the fix itself, `cleanpipe check -- <server command>` exits 1 if anything non-JSON-RPC
> reaches stdout during the handshake, so it can guard this in CI. (I wrote it after seeing
> this bug in many servers.)

## awslabs/mcp#4991 (well-architected-security-mcp-server: print() corrupts stdio)

https://github.com/awslabs/mcp/issues/4991

> A workaround for anyone hitting this before a release: wrap the server with
> [cleanpipe](https://github.com/srikrishnaL/kitforge/tree/main/packages/cleanpipe)
> (`uvx cleanpipe -- <server command>`). It passes JSON-RPC messages through byte for byte and
> sends the `print()` output to stderr, including output printed without a newline right
> before a message. `cleanpipe check -- <server command>` reports the offending lines.

## universal-tool-calling-protocol/typescript-utcp#63 (@utcp/mcp _logInfo() writes to stdout)

https://github.com/universal-tool-calling-protocol/typescript-utcp/issues/63

> If it helps while this is open: [cleanpipe](https://github.com/srikrishnaL/kitforge/tree/main/packages/cleanpipe)
> wraps any stdio MCP server (Node included) and keeps only JSON-RPC on stdout, so clients
> stop failing the handshake: `uvx cleanpipe -- npx -y <package>`. The log lines go to stderr
> unchanged. Disclosure: I maintain it.
