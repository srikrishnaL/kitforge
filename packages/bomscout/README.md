# bomscout

**An MCP server for [OWASP Dependency-Track](https://dependencytrack.org/).** Ask Claude, Cursor,
VS Code or any MCP client about your software portfolio in plain words: "which of our projects
are affected by CVE-2021-44228, and has anyone triaged it?", "what should we fix first in
`shop`?", "do we ship any GPL code?".

Dependency-Track already knows the answers, but getting them means clicking through the UI or
writing scripts against several REST endpoints. bomscout wraps those endpoints as a handful of
read-only tools that return small, ranked results a model can reason over.

```bash
pip install bomscout        # or run it without installing: uvx bomscout
```

## Set up

You need the URL of the Dependency-Track **API server** and an API key of a team
(Administration > Access Management > Teams). Give the team `VIEW_PORTFOLIO`,
`VIEW_VULNERABILITY` and `VIEW_POLICY_VIOLATION`. Check the connection and permissions:

```bash
DT_URL=https://dtrack.example.com DT_API_KEY=odt_... bomscout check
```

**Claude Code**

```bash
claude mcp add bomscout -e DT_URL=https://dtrack.example.com -e DT_API_KEY=odt_... -- uvx bomscout
```

**Claude Desktop, Cursor, VS Code and others** (the usual `mcpServers` JSON):

```json
{
  "mcpServers": {
    "bomscout": {
      "command": "uvx",
      "args": ["bomscout"],
      "env": { "DT_URL": "https://dtrack.example.com", "DT_API_KEY": "odt_..." }
    }
  }
}
```

| Setting | Meaning |
| --- | --- |
| `DT_URL` | API server URL (a trailing `/api` is fine). `DEPENDENCY_TRACK_URL` also works. |
| `DT_API_KEY` | Team API key. `DEPENDENCY_TRACK_API_KEY` also works. |
| `DT_CA_BUNDLE` | Path to a CA bundle for a server with a private certificate. |
| `DT_INSECURE=1` | Skip TLS certificate checks (test servers only). |
| `DT_TIMEOUT` | Seconds per request (default 30). |
| `BOMSCOUT_ALLOW_WRITE=1` | Add the `record_analysis` tool (or run `bomscout --allow-write`). |

## Tools

| Tool | Answers |
| --- | --- |
| `portfolio_overview` | Vulnerabilities by severity, audited vs unaudited findings, policy violations, riskiest projects |
| `list_projects` | Projects by name or tag, with UUID, version, risk score and vulnerability counts |
| `project_findings` | A project's vulnerabilities, most severe first (CVSS, EPSS, component, triage state); filter by severity or analysis state, e.g. untriaged only |
| `vulnerability_impact` | One CVE/GHSA: details, fixed versions, every affected project and its remediation status there |
| `project_components` | A project's dependencies with license and newer versions; only outdated, only direct |
| `project_licenses` | Components per license, grouped as strong copyleft, weak copyleft, permissive, unknown |
| `find_component` | Every project that uses a package, by package URL or name and version |
| `policy_violations` | Security, license and operational policy violations for a project or the portfolio, FAIL first |
| `record_analysis` | *Opt-in.* Record a triage decision (state, justification, response, comment, suppression) |

Projects can be named by UUID, by name (its latest version) or as `name@version`. All tools
except `record_analysis` are marked read-only, so clients can run them without asking.

## Example

A tool result is compact JSON. `project_findings` with `project="shop", min_severity="HIGH"`:

```json
{
  "project": "shop@2.0",
  "by_severity": {"CRITICAL": 1, "HIGH": 1},
  "by_analysis": {"EXPLOITABLE": 1, "NOT_SET": 1},
  "total": 2, "shown": 2,
  "findings": [
    {"vuln": "CVE-2021-44228", "source": "NVD", "severity": "CRITICAL", "cvss": 10.0, "epss": 0.944,
     "aliases": ["GHSA-jfh8-c2jp-5v3q"], "component": "org.apache.logging.log4j/log4j-core@2.14.1",
     "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1", "analysis": "EXPLOITABLE",
     "component_uuid": "...", "vulnerability_uuid": "..."}
  ]
}
```

The same questions work from Python, without MCP:

```python
from bomscout import Scout

scout = Scout.from_env()  # reads DT_URL and DT_API_KEY
impact = scout.vulnerability_impact("CVE-2021-44228")
for project in impact["projects"]:
    print(project["project"], [c["analysis"] for c in project["components"]])
```

## Limits

- Dependency-Track **4.x** REST API (field names checked against the 4.14.6 API server).
  The Hyades / v5 API is not supported yet.
- An API key sees what its team sees. With portfolio access control on, other teams' projects
  are invisible to bomscout too.
- `vulnerability_impact` reads the findings of each affected project to report its analysis
  state: one request per project, 20 projects by default (`max_projects`).
- License families are a quick triage aid, not legal advice. Components without a resolved
  license count as "unknown".
- stdio transport only. Run one instance per user, with that user's (or a read-only team's)
  API key.
- Writing is limited to `record_analysis`; bomscout never uploads BOMs, deletes projects or
  changes policies.

## Why this exists

Dependency-Track users asked for an MCP server so they could query their portfolio's security
posture in natural language instead of hand-writing API scripts:
[DependencyTrack/dependency-track#5791](https://github.com/DependencyTrack/dependency-track/issues/5791)
(open, 18 upvotes). Its examples ("show me all critical vulnerabilities", "summary of component
license risks", "what's the current remediation status for CVE-...") map to
`portfolio_overview` / `project_findings`, `project_licenses` and `vulnerability_impact`.

Part of [kitforge](https://github.com/srikrishnaL/kitforge). MIT licensed.
