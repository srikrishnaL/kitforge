# kitforge

Useful libraries and MCP servers.

Each package here exists because people asked for it: an open GitHub issue, a forum thread, a
gap where the only existing project is unmaintained. The demand evidence for each one is kept in
[`demand/`](demand/) and linked from its README.

## Packages

| Package | What it does | Install | Status |
| --- | --- | --- | --- |
| [mcptest](packages/mcptest/) | Automated tests, linting and breaking-change checks for MCP servers (pytest plugin + CLI) | `pip install pytest-mcp-server` | 0.2.0 |
| [cimdkit](packages/cimdkit/) | OAuth Client ID Metadata Documents (CIMD) for authorization servers: safe fetch, validation, caching, redirect URI matching for MCP clients | `pip install cimdkit` | 0.1.0 |

## How packages are chosen

1. **Collect signals.** [`demand/collect.py`](demand/collect.py) searches GitHub for open
   requests and for the best existing project for every idea in
   [`demand/ideas.json`](demand/ideas.json).
2. **Score.** Each idea gets 0 to 100: 55% demand (open requests, most upvoted request), 45% gap
   (how strong and how maintained the best existing project is). Results land in
   [`demand/scores.json`](demand/scores.json).
3. **Check and build.** The top idea's evidence is read by hand before anything is built. Then
   it ships with tests, a README that opens with the problem, and a changelog.
4. **Measure.** Downloads, stars and issues on shipped packages decide what gets improved next.
   Packages people use come before new ones.

## Layout

```
packages/<name>/     one package per folder (Python: pyproject.toml, Node: package.json)
  src/  tests/  README.md  CHANGELOG.md  LICENSE
demand/              ideas.json, collect.py, scores.json
docs/                how releases and the daily run work
.github/workflows/   ci.yml (every package, Python 3.9 to 3.13), release.yml
```

## Releasing

Bump the version in the package's `pyproject.toml` or `package.json`, add a CHANGELOG entry, and
merge to `main`. The release workflow sees a version with no tag yet, runs the tests, publishes to
PyPI or npm, and creates the tag `<published name>-v<version>` (for example
`pytest-mcp-server-v0.2.0`) with a GitHub release. See [docs/RELEASING.md](docs/RELEASING.md).

## Contributing

Issues and pull requests are welcome. Requests for new packages are welcome too: open an issue
describing the problem and link where others have asked for the same thing.

## License

MIT
