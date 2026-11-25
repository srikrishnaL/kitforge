# bomscout outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. Check the issue is still open
before posting.

## DependencyTrack/dependency-track#5791 (MCP Server for Dependency Track)

https://github.com/DependencyTrack/dependency-track/issues/5791 (open, 18 upvotes on 2026-11-12; still open with 19 upvotes and 8 comments on 2026-11-22,
no official server announced)

> For anyone who wants this today without waiting for a built-in server: I published
> [bomscout](https://github.com/srikrishnaL/kitforge/tree/main/packages/bomscout), an MCP server
> that wraps the existing v1 REST API (`uvx bomscout`, MIT). It covers the three examples in
> this issue: `portfolio_overview` / `project_findings` for "show me all critical
> vulnerabilities", `project_licenses` for license risk, and `vulnerability_impact`, which lists
> every project a CVE or GHSA affects with its analysis state in each one. It is read-only by
> default (all tools carry `readOnlyHint`); recording an analysis is a separate opt-in tool. On
> the security question: it only needs a team key with VIEW_PORTFOLIO, VIEW_VULNERABILITY and
> VIEW_POLICY_VIOLATION, and `bomscout check` reports which of those the key has. Feedback on
> which other questions people want answered is welcome.

## Dependency-Track docs: community integrations list

The Dependency-Track docs have a community integrations page (not checked from here: find its
current URL and source file in the docs before opening anything). A one-line PR, if the
maintainers accept tools there:

> | bomscout | MCP server that lets AI assistants (Claude, Cursor, VS Code) query projects, findings, licenses and policy violations through the REST API | https://github.com/srikrishnaL/kitforge/tree/main/packages/bomscout |

Check the current format of that page first; the table layout may differ.

## Official MCP Registry (Sri runs this, about 5 minutes)

`packages/bomscout/server.json` describes bomscout for https://registry.modelcontextprotocol.io,
which VS Code, Claude and other clients read when people browse for servers. The registry
checks that the PyPI README contains `mcp-name: io.github.srikrishnal/bomscout`, so publish
**after** bomscout 0.1.1 is on PyPI (0.1.0's README does not have that line).

```bash
# once: install the publisher (macOS/Linux; see the registry quickstart for Windows)
brew install mcp-publisher        # or download it from github.com/modelcontextprotocol/registry/releases
cd packages/bomscout
mcp-publisher login github        # opens a browser device-code login as srikrishnaL
mcp-publisher publish             # reads ./server.json
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.srikrishnal/bomscout"
```

The name is lowercase because the registry's schema requires it. Logins with capital letters
(srikrishnaL) used to get a 403 here; that was fixed in modelcontextprotocol/registry#1787
(closed 2026-11-02). If the 403 still appears, the message lists the namespace you were
granted: tell the next run and it will adjust.

## punkpeye/awesome-mcp-servers (Security section)

PR adding one line under `### 🔒 Security` (README.md, around line 4420 on 2026-11-22; new
entries go near the top of the section). 🐍 = Python, 🏠 = runs locally; the glama badge is
added by glama later and can be left out.

> - [srikrishnaL/kitforge/bomscout](https://github.com/srikrishnaL/kitforge/tree/main/packages/bomscout) 🐍 🏠 🍎 🪟 🐧 - OWASP Dependency-Track for AI assistants: which projects a CVE affects and their triage state, what to fix first (CVSS, EPSS), license risk and policy violations across your SBOM portfolio. Read-only by default. `uvx bomscout`

PR title: `Add bomscout (OWASP Dependency-Track MCP server)`.

## GitHub repo topics

kitforge is a monorepo, so per-package topics don't apply; if not already there, adding
`dependency-track` and `sbom` to the kitforge repo topics helps people searching GitHub find it.
