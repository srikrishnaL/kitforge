# Changelog

## 0.1.0 (2026-11-12)

First release.

- MCP server for OWASP Dependency-Track 4.x on the official MCP Python SDK (1.10+, 1.x, 2.x),
  stdio transport, run with `uvx bomscout`.
- Eight read-only tools: `portfolio_overview`, `list_projects`, `project_findings`,
  `vulnerability_impact`, `project_components`, `project_licenses`, `find_component`,
  `policy_violations`. Results are compact JSON, ranked (severity, CVSS, EPSS; FAIL before
  WARN), with totals and a note when rows were left out.
- Opt-in `record_analysis` (`BOMSCOUT_ALLOW_WRITE=1` or `--allow-write`) to record triage
  decisions; choices accept any case ("false positive" becomes `FALSE_POSITIVE`).
- Projects by UUID, name (latest version) or `name@version`, including scoped npm names.
- Vulnerability source guessed from the id (CVE- is NVD, GHSA- is GitHub, then the others).
- Errors written for the model: wrong URL (including a frontend URL), rejected API key, the
  exact team permission missing on a 403, unknown projects and vulnerabilities.
- `bomscout check` tests the URL, key and each permission. `Scout` answers the same
  questions from Python.
