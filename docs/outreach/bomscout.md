# bomscout outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. Check the issue is still open
before posting.

## DependencyTrack/dependency-track#5791 (MCP Server for Dependency Track)

https://github.com/DependencyTrack/dependency-track/issues/5791 (open, 18 upvotes on 2026-11-12)

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
