"""Static checks on MCP tool definitions.

An agent only sees a tool through its name, description and input schema.
These rules catch definitions that make agents pick the wrong tool, send bad
arguments, waste context, or that hide instructions aimed at the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

SEVERITIES = ("info", "warning", "error")

# The MCP spec allows 1-128 characters from this set in tool names.
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")

MIN_DESCRIPTION = 20
MAX_DESCRIPTION = 1024
MAX_TOTAL_CHARS = 32_000  # roughly 8k tokens of tool definitions

# Phrases that try to steer the model from inside a tool description.
# These are how "tool poisoning" attacks hide instructions.
_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|other) (instructions|tools|rules)",
    r"do not (tell|inform|mention|reveal)[^.]{0,40}\b(user|human)\b",
    r"don'?t (tell|inform|mention|reveal)[^.]{0,40}\b(user|human)\b",
    r"without (telling|informing|asking) the (user|human)",
    r"<\s*(important|system|secret|instructions?)\s*>",
    r"\b(read|send|upload|exfiltrate)\b[^.]{0,60}(~/\.ssh|id_rsa|\.env\b|credentials|api[_ ]?key)",
    r"before using (any|this|other) tools?,? (you must|always)",
    r"instead of (calling|using) [a-z0-9_\-]+,? (always )?(call|use)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)
_HIDDEN_CHARS_RE = re.compile("[​‌‍⁠⁦-⁩‪-‮﻿]")

_TYPELESS_OK = ("enum", "const", "anyOf", "oneOf", "allOf", "$ref", "not")

# Tool annotations from the MCP spec. Clients use them to decide which calls need the
# user's confirmation; when a hint is missing they assume the worst (not read-only,
# destructive, not idempotent, open world).
_BOOL_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
# First word of a tool name that says the tool changes something.
_WRITE_VERBS = frozenset(
    "delete remove drop destroy purge truncate erase wipe clear reset kill terminate "
    "create insert update upsert write set put post patch send upload move rename "
    "edit modify replace overwrite execute exec deploy publish revoke grant".split()
)
# First word of a tool name that suggests it only reads.
_READ_VERBS = frozenset(
    "get list read search find fetch query describe show count lookup view inspect "
    "check status".split()
)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    tool: Optional[str]
    message: str

    def __str__(self) -> str:
        where = f"{self.tool}: " if self.tool else ""
        return f"{self.severity:<7} {self.code} {where}{self.message}"

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "tool": self.tool, "message": self.message}


#: Every rule, for documentation and ``--ignore``.
RULES: Dict[str, str] = {
    "MCP001": "Tool name is not 1-128 characters of A-Z a-z 0-9 _ - .",
    "MCP002": "Two tools share the same name",
    "MCP003": "inputSchema is missing or is not a JSON Schema of type object",
    "MCP004": "Tool has no description",
    "MCP005": f"Description is shorter than {MIN_DESCRIPTION} characters",
    "MCP006": f"Description is longer than {MAX_DESCRIPTION} characters",
    "MCP007": "Parameter has no description",
    "MCP008": "A required parameter is not defined in properties",
    "MCP009": "Parameter has no type",
    "MCP010": f"All tool definitions together exceed {MAX_TOTAL_CHARS:,} characters",
    "MCP011": "Two tool names differ only by case or separators",
    "MCP012": "Description contains text that tries to instruct the model (possible tool poisoning)",
    "MCP013": "Description contains invisible or bidirectional control characters",
    "MCP014": "outputSchema is not a JSON Schema of type object",
    "MCP015": "Parameter description is identical to its name",
    "MCP016": "Tool annotations do not say whether the tool is read-only or destructive",
    "MCP017": "annotations is not an object, or a hint is not true/false",
    "MCP018": "Tool is marked both read-only and destructive",
    "MCP019": "Tool is marked read-only but its name says it changes something",
}

_SEVERITY = {
    "MCP001": "error",
    "MCP002": "error",
    "MCP003": "error",
    "MCP004": "warning",
    "MCP005": "warning",
    "MCP006": "warning",
    "MCP007": "warning",
    "MCP008": "error",
    "MCP009": "warning",
    "MCP010": "warning",
    "MCP011": "warning",
    "MCP012": "error",
    "MCP013": "error",
    "MCP014": "error",
    "MCP015": "info",
    "MCP016": "info",
    "MCP017": "error",
    "MCP018": "warning",
    "MCP019": "warning",
}


def _norm_name(name: str) -> str:
    return re.sub(r"[\s_.\-]", "", name).lower()


def lint_tools(
    tools: Sequence[Dict[str, Any]],
    *,
    ignore: Iterable[str] = (),
) -> List[Finding]:
    """Check a ``tools/list`` result. Returns findings, most severe first."""
    skip = {c.upper() for c in ignore}
    out: List[Finding] = []

    def add(code: str, tool: Optional[str], message: str) -> None:
        if code not in skip:
            out.append(Finding(code, _SEVERITY[code], tool, message))

    seen: Dict[str, int] = {}
    normalised: Dict[str, str] = {}
    for tool in tools:
        name = str(tool.get("name", ""))
        if not NAME_RE.match(name):
            add("MCP001", name or None, f"invalid tool name {name!r}")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 2:
            add("MCP002", name, "name is used by more than one tool")
        key = _norm_name(name)
        other = normalised.get(key)
        if other is not None and other != name:
            add("MCP011", name, f"easily confused with {other!r}")
        normalised.setdefault(key, name)

        _check_description(tool, name, add)
        _check_input_schema(tool, name, add)
        _check_annotations(tool, name, add)

        output_schema = tool.get("outputSchema")
        if output_schema is not None and (
            not isinstance(output_schema, dict) or output_schema.get("type") != "object"
        ):
            add("MCP014", name, "outputSchema must be an object schema with \"type\": \"object\"")

    total = len(json.dumps(list(tools), ensure_ascii=False))
    if total > MAX_TOTAL_CHARS:
        add(
            "MCP010",
            None,
            f"tool definitions are {total:,} characters (about {total // 4:,} tokens), "
            "sent to the model on every turn",
        )

    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    out.sort(key=lambda f: (order[f.severity], f.tool or "", f.code))
    return out


def _check_description(tool: Dict[str, Any], name: str, add) -> None:
    desc = tool.get("description")
    if not isinstance(desc, str) or not desc.strip():
        add("MCP004", name, "add a description saying what the tool does and when to use it")
        return
    text = desc.strip()
    if len(text) < MIN_DESCRIPTION:
        add("MCP005", name, f"description is only {len(text)} characters: {text!r}")
    if len(text) > MAX_DESCRIPTION:
        add("MCP006", name, f"description is {len(text):,} characters; it is sent on every turn")
    _check_text_safety(text, name, "description", add)


def _check_text_safety(text: str, name: str, where: str, add) -> None:
    hits: List[str] = []
    for m in _INJECTION_RE.finditer(text):
        if m.group(0) not in hits:
            hits.append(m.group(0))
    if hits:
        add("MCP012", name, f"{where} says " + ", ".join(repr(h) for h in hits))
    if _HIDDEN_CHARS_RE.search(text):
        add("MCP013", name, f"{where} contains invisible or bidirectional control characters")


def _check_input_schema(tool: Dict[str, Any], name: str, add) -> None:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        add("MCP003", name, "inputSchema must be an object schema with \"type\": \"object\"")
        return
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        add("MCP003", name, "inputSchema.properties must be an object")
        return
    required = schema.get("required") or []
    if isinstance(required, list):
        for r in required:
            if r not in props:
                add("MCP008", name, f"required parameter {r!r} is not in properties")
    for pname, pschema in props.items():
        if not isinstance(pschema, dict):
            continue
        pdesc = pschema.get("description")
        if not isinstance(pdesc, str) or not pdesc.strip():
            add("MCP007", name, f"parameter {pname!r} has no description")
        else:
            if _norm_name(pdesc.strip().rstrip(".")) == _norm_name(pname):
                add("MCP015", name, f"parameter {pname!r} description just repeats its name")
            _check_text_safety(pdesc, name, f"parameter {pname!r} description", add)
        if "type" not in pschema and not any(k in pschema for k in _TYPELESS_OK):
            add("MCP009", name, f"parameter {pname!r} has no type")


def _first_word(name: str) -> str:
    """The verb of a tool name: ``deleteFile``, ``delete_file``, ``files.delete_one`` -> ``delete``."""
    name = name.rsplit(".", 1)[-1]  # drop a namespace prefix such as "github."
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    words = re.split(r"[\s_.\-]+", spaced.strip(" _.-"))
    return words[0].lower() if words else ""


def _check_annotations(tool: Dict[str, Any], name: str, add) -> None:
    ann = tool.get("annotations")
    if ann is None:
        ann = {}
    elif not isinstance(ann, dict):
        add("MCP017", name, f"annotations must be an object, got {type(ann).__name__}")
        return
    bad = [h for h in _BOOL_HINTS if h in ann and not isinstance(ann[h], bool)]
    if "title" in ann and not isinstance(ann["title"], str):
        bad.append("title")
    if bad:
        shown = ", ".join(f"{h}={ann[h]!r}" for h in bad)
        add("MCP017", name, f"annotation values have the wrong type: {shown} "
            "(hints must be true or false, title a string); clients ignore them")
        return

    read_only = ann.get("readOnlyHint")
    destructive = ann.get("destructiveHint")
    verb = _first_word(name)
    if read_only is None:
        if verb in _READ_VERBS:
            hint = ("looks read-only but does not set readOnlyHint, so clients treat it as "
                    "destructive and may ask the user to confirm every call")
        else:
            hint = ("sets neither readOnlyHint nor destructiveHint; clients assume it can "
                    "destroy data")
        if destructive is None:
            add("MCP016", name, hint)
    elif read_only is False and destructive is None:
        add("MCP016", name, "readOnlyHint is false but destructiveHint is not set, so clients "
            "assume it can destroy data; set destructiveHint: false for additive tools")
    if read_only is True and destructive is True:
        add("MCP018", name, "readOnlyHint and destructiveHint are both true; a read-only tool "
            "cannot destroy anything, so one of them is wrong")
    if read_only is True and verb in _WRITE_VERBS:
        add("MCP019", name, f"readOnlyHint is true but the name starts with {verb!r}; clients may "
            "run it without asking the user")


def count_by_severity(findings: Iterable[Finding]) -> Dict[str, int]:
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts[f.severity] += 1
    return counts


def at_or_above(findings: Iterable[Finding], level: str) -> List[Finding]:
    """Findings whose severity is ``level`` or worse."""
    if level not in SEVERITIES:
        raise ValueError(f"level must be one of {SEVERITIES}")
    rank = SEVERITIES.index(level)
    return [f for f in findings if SEVERITIES.index(f.severity) >= rank]
