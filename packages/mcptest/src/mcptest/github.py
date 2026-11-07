"""GitHub Actions output: annotations and a job summary.

``mcptest lint --format github`` and ``mcptest diff --format github`` print
workflow commands (``::error title=...::message``) so each finding shows up
as an annotation on the run and the pull request, and they append a Markdown
table to the job summary when ``GITHUB_STEP_SUMMARY`` is set.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence

from .lint import Finding, count_by_severity
from .snapshot import Change

#: mcptest severity -> GitHub annotation level.
LEVELS: Dict[str, str] = {"error": "error", "warning": "warning", "info": "notice"}


def escape_data(text: str) -> str:
    """Escape the message part of a workflow command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    """Escape a property value (``title=...``) of a workflow command."""
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(level: str, title: str, message: str) -> str:
    if level not in ("error", "warning", "notice"):
        raise ValueError(f"unknown annotation level {level!r}")
    return f"::{level} title={escape_property(title)}::{escape_data(message)}"


def lint_annotations(findings: Iterable[Finding], server: str = "server") -> List[str]:
    out = []
    for f in findings:
        where = f" {f.tool}" if f.tool else ""
        out.append(annotation(LEVELS[f.severity], f"{f.code}{where} ({server})", f.message))
    return out


def diff_annotations(changes: Iterable[Change], breaking_level: str = "error") -> List[str]:
    """Breaking changes at ``breaking_level`` (``warning`` when they are allowed)."""
    out = []
    for c in changes:
        if c.breaking:
            out.append(annotation(breaking_level, f"Breaking MCP change: {c.tool}", c.message))
        else:
            out.append(annotation("notice", f"MCP change: {c.tool}", c.message))
    return out


def _cell(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(str(text).split()).replace("|", "\\|")


def lint_summary(server: str, tool_count: int, findings: Sequence[Finding]) -> str:
    counts = count_by_severity(findings)
    lines = [
        f"### mcptest lint: {_cell(server)}",
        "",
        f"{tool_count} tools; {counts['error']} errors, {counts['warning']} warnings, "
        f"{counts['info']} info",
        "",
    ]
    if findings:
        lines += ["| Level | Rule | Tool | Finding |", "| --- | --- | --- | --- |"]
        for f in findings:
            lines.append(f"| {f.severity} | {f.code} | {_cell(f.tool)} | {_cell(f.message)} |")
        lines.append("")
    else:
        lines += ["No findings.", ""]
    return "\n".join(lines) + "\n"


def diff_summary(changes: Sequence[Change]) -> str:
    n = sum(1 for c in changes if c.breaking)
    lines = ["### mcptest diff", ""]
    if not changes:
        return "\n".join(lines + ["No changes to the tool list.", ""]) + "\n"
    lines += [f"{n} breaking, {len(changes) - n} compatible", "",
              "| Change | Tool | Detail |", "| --- | --- | --- |"]
    for c in changes:
        lines.append(f"| {'**breaking**' if c.breaking else 'compatible'} | {_cell(c.tool)} "
                     f"| {_cell(c.message)} |")
    return "\n".join(lines + [""]) + "\n"


def write_step_summary(markdown: str) -> bool:
    """Append to the job summary file. Returns False when not running in Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(markdown)
    return True
