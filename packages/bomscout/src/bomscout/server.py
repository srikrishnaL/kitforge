"""The bomscout MCP server: Dependency-Track tools for AI assistants.

Runs on the official MCP Python SDK, 1.x (``FastMCP``) or 2.x (``MCPServer``). Every tool is
read-only unless ``BOMSCOUT_ALLOW_WRITE=1`` is set, which adds ``record_analysis`` for
triaging findings.
"""

# No `from __future__ import annotations` here: MCP SDK 1.10 reads tool parameter
# annotations without resolving strings.
import json
import os
from typing import Annotated, Any, Callable, Mapping, Optional

import anyio

from . import __version__

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server

    _SERVER_INFO = {"version": __version__}
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

    _SERVER_INFO = {}

from mcp.types import ToolAnnotations
from pydantic import Field

from .errors import DTError
from .scout import Scout

INSTRUCTIONS = (
    "Tools for OWASP Dependency-Track: projects, vulnerabilities (findings), components, "
    "licenses and policy violations across the organization's software portfolio. Projects can "
    "be named by UUID, by name (latest version) or as name@version."
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                       openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                        openWorldHint=False)

Project = Annotated[str, Field(description="Project UUID, project name (its latest version), or name@version")]
Limit = Annotated[int, Field(description="Maximum rows to return (1-500)")]


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def build_server(scout: Optional[Scout] = None, *, allow_write: Optional[bool] = None,
                 env: Optional[Mapping[str, str]] = None) -> Any:
    """Create the MCP server.

    Args:
        scout: The :class:`Scout` to answer with. By default one is built from the
            environment on the first tool call, so the server starts (and lists its tools)
            even before ``DT_URL`` and ``DT_API_KEY`` are set.
        allow_write: Register ``record_analysis``. Defaults to ``BOMSCOUT_ALLOW_WRITE``.
        env: Environment to read settings from (default ``os.environ``).
    """
    env = os.environ if env is None else env
    if allow_write is None:
        allow_write = _truthy(env.get("BOMSCOUT_ALLOW_WRITE"))
    state = {"scout": scout}

    def get_scout() -> Scout:
        if state["scout"] is None:
            state["scout"] = Scout.from_env(env)
        return state["scout"]

    async def run(fn: Callable[..., Any], *args: Any) -> str:
        def call() -> Any:
            return fn(get_scout(), *args)

        return _dumps(await anyio.to_thread.run_sync(call))

    srv = _Server(name="bomscout", instructions=INSTRUCTIONS, **_SERVER_INFO)

    @srv.tool(annotations=READ, structured_output=False)
    async def portfolio_overview(
        top: Annotated[int, Field(description="How many of the riskiest projects to list")] = 10,
    ):
        """Security posture of the whole Dependency-Track portfolio: vulnerability counts by
        severity, audited vs unaudited findings, policy violations, and the projects with the
        highest risk score."""
        return await run(Scout.portfolio_overview, top)

    @srv.tool(annotations=READ, structured_output=False)
    async def list_projects(
        name: Annotated[str, Field(description="Part of the project name to search for (empty for all)")] = "",
        tag: Annotated[str, Field(description="Only projects with this tag")] = "",
        include_inactive: Annotated[bool, Field(description="Also list inactive projects")] = False,
        limit: Limit = 50,
    ):
        """List Dependency-Track projects with their UUID, version, tags, risk score and
        vulnerability counts. Use it to find the project a question is about."""
        return await run(Scout.list_projects, name, tag, include_inactive, limit)

    @srv.tool(annotations=READ, structured_output=False)
    async def project_findings(
        project: Project,
        min_severity: Annotated[str, Field(description="Only findings at or above this severity: CRITICAL, HIGH, MEDIUM, LOW, INFO")] = "",
        analysis_state: Annotated[str, Field(description="Only findings with this analysis state, e.g. NOT_SET for untriaged ones, EXPLOITABLE, IN_TRIAGE")] = "",
        include_suppressed: Annotated[bool, Field(description="Also include suppressed findings")] = False,
        limit: Limit = 50,
    ):
        """Vulnerabilities found in one project, most severe first: vulnerability id, severity,
        CVSS, EPSS, affected component, and analysis (triage) state, plus totals by severity
        and by analysis state."""
        return await run(Scout.project_findings, project, min_severity, analysis_state,
                         include_suppressed, limit)

    @srv.tool(annotations=READ, structured_output=False)
    async def vulnerability_impact(
        vuln_id: Annotated[str, Field(description="Vulnerability id, e.g. CVE-2021-44228 or GHSA-jfh8-c2jp-5v3q")],
        source: Annotated[str, Field(description="Vulnerability source (NVD, GITHUB, OSV, SNYK...); guessed from the id when empty")] = "",
        max_projects: Annotated[int, Field(description="Check the analysis state in at most this many affected projects")] = 20,
    ):
        """Details of one vulnerability (severity, CVSS, EPSS, description, fixed versions) and
        every project it affects, with the affected component and its remediation (analysis)
        status in each project."""
        return await run(Scout.vulnerability_impact, vuln_id, source, max_projects)

    @srv.tool(annotations=READ, structured_output=False)
    async def project_components(
        project: Project,
        search: Annotated[str, Field(description="Part of a component name to search for")] = "",
        only_outdated: Annotated[bool, Field(description="Only components with a newer version available")] = False,
        only_direct: Annotated[bool, Field(description="Only direct dependencies")] = False,
        limit: Limit = 100,
    ):
        """Components (dependencies) of one project from its SBOM: name, version, package URL,
        license, newer version if known, and vulnerability counts."""
        return await run(Scout.project_components, project, search, only_outdated, only_direct, limit)

    @srv.tool(annotations=READ, structured_output=False)
    async def project_licenses(project: Project):
        """License summary of one project: how many components use each license, grouped as
        strong copyleft, weak copyleft, permissive or unknown, with example components."""
        return await run(Scout.project_licenses, project)

    @srv.tool(annotations=READ, structured_output=False)
    async def find_component(
        purl: Annotated[str, Field(description="Package URL, e.g. pkg:npm/lodash@4.17.20 (without @version for any version)")] = "",
        name: Annotated[str, Field(description="Component name, when no purl is given")] = "",
        version: Annotated[str, Field(description="Component version, with name")] = "",
        group: Annotated[str, Field(description="Component group or namespace, with name (e.g. org.apache.logging.log4j)")] = "",
        limit: Limit = 50,
    ):
        """Find which projects across the portfolio use a component (by package URL or name and
        version), e.g. every project that still ships log4j-core 2.14."""
        return await run(Scout.find_component, purl, name, version, group, limit)

    @srv.tool(annotations=READ, structured_output=False)
    async def policy_violations(
        project: Annotated[str, Field(description="Project UUID, name or name@version; empty for the whole portfolio")] = "",
        violation_type: Annotated[str, Field(description="Only SECURITY, LICENSE or OPERATIONAL violations")] = "",
        include_suppressed: Annotated[bool, Field(description="Also include suppressed violations")] = False,
        limit: Limit = 50,
    ):
        """Policy violations (security, license, operational) for one project or the whole
        portfolio, FAIL first: policy, condition, component and analysis state."""
        return await run(Scout.policy_violations, project, violation_type, include_suppressed, limit)

    if allow_write:
        @srv.tool(annotations=WRITE, structured_output=False)
        async def record_analysis(
            project: Project,
            component_uuid: Annotated[str, Field(description="component_uuid from project_findings")],
            vulnerability_uuid: Annotated[str, Field(description="vulnerability_uuid from project_findings")],
            state: Annotated[str, Field(description="Analysis state: EXPLOITABLE, IN_TRIAGE, FALSE_POSITIVE, NOT_AFFECTED, RESOLVED or NOT_SET")],
            justification: Annotated[str, Field(description="Why it is not affected, e.g. CODE_NOT_REACHABLE, REQUIRES_CONFIGURATION, PROTECTED_AT_RUNTIME")] = "",
            response: Annotated[str, Field(description="Planned response: CAN_NOT_FIX, WILL_NOT_FIX, UPDATE, ROLLBACK, WORKAROUND_AVAILABLE")] = "",
            details: Annotated[str, Field(description="Analysis details shown in Dependency-Track and in VEX exports")] = "",
            comment: Annotated[str, Field(description="Audit comment to add to the finding's history")] = "",
            suppress: Annotated[Optional[bool], Field(description="Suppress (true) or unsuppress (false) the finding; unchanged when empty")] = None,
        ):
            """Record the triage decision for one finding in Dependency-Track (analysis state,
            justification, response, details, comment, suppression). Confirm the decision with
            the user first: it is visible to the whole team and exported in VEX documents."""
            return await run(Scout.record_analysis, project, component_uuid, vulnerability_uuid,
                             state, justification, response, details, comment, suppress)

    return srv


def check(env: Optional[Mapping[str, str]] = None) -> int:
    """Check the connection and API key permissions; print a report. Returns an exit code."""
    scout = Scout.from_env(env)
    ok = True
    try:
        info = scout.dt.version()
        print(f"Dependency-Track {info.get('version', '?')} at {scout.dt.base}")
    except DTError as err:
        print(f"FAIL  {err}")
        return 1
    probes = [
        ("VIEW_PORTFOLIO", "/v1/project", {"pageSize": 1}),
        ("VIEW_VULNERABILITY", "/v1/finding", {"pageSize": 1}),
        ("VIEW_POLICY_VIOLATION", "/v1/violation", {"pageSize": 1}),
    ]
    for perm, path, params in probes:
        try:
            scout.dt.get(path, params)
            print(f"ok    {perm}")
        except DTError as err:
            ok = False
            print(f"FAIL  {perm}: {err}")
    return 0 if ok else 1


def main(argv: Optional[list] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="bomscout", description="MCP server for OWASP Dependency-Track (stdio).")
    ap.add_argument("command", nargs="?", choices=["serve", "check"], default="serve",
                    help="serve (default): run the MCP server on stdio; check: test DT_URL and DT_API_KEY")
    ap.add_argument("--allow-write", action="store_true",
                    help="add the record_analysis tool (same as BOMSCOUT_ALLOW_WRITE=1)")
    ap.add_argument("--version", action="version", version=f"bomscout {__version__}")
    args = ap.parse_args(argv)
    if args.command == "check":
        return check()
    build_server(allow_write=True if args.allow_write else None).run()
    return 0
