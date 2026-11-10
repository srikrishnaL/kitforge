"""Rewrite a JSON Schema so a model provider accepts it, and undo the rewrite on the arguments."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

GEMINI = "gemini"
OPENAI_STRICT = "openai-strict"
TARGETS = (GEMINI, OPENAI_STRICT)

# Keywords each target accepts on a schema node. Anything else is removed, and constraints the
# model should still know about are written into the description instead.
_GEMINI_KEYS = {
    "type", "format", "title", "description", "nullable", "enum", "maxItems", "minItems",
    "properties", "required", "minProperties", "maxProperties", "minLength", "maxLength",
    "pattern", "example", "anyOf", "propertyOrdering", "default", "items", "minimum", "maximum",
}
_OPENAI_KEYS = {
    "type", "title", "description", "enum", "const", "properties", "required",
    "additionalProperties", "items", "anyOf", "pattern", "format", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems",
}
_GEMINI_STRING_FORMATS = {"enum", "date-time"}
_GEMINI_NUMBER_FORMATS = {"int32", "int64", "float", "double"}
_OPENAI_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "ipv4",
                   "ipv6", "uuid"}
# Keys dropped without a note: they carry no meaning for the model.
_SILENT = {"$schema", "$id", "$comment", "$anchor", "readOnly", "writeOnly", "deprecated",
           "contentMediaType", "contentEncoding"}
_STRUCTURAL = {"$ref", "$defs", "definitions", "allOf", "anyOf", "oneOf", "type", "properties",
               "required", "items", "prefixItems", "additionalProperties", "nullable", "const",
               "enum"}
_LOOKAROUND = re.compile(r"\(\?<?[=!]|\\[1-9]|\\k<")

_PHRASES = {
    "minLength": "at least {} character(s)",
    "maxLength": "at most {} character(s)",
    "pattern": "must match the regex {}",
    "format": "format: {}",
    "minimum": "at least {}",
    "maximum": "at most {}",
    "exclusiveMinimum": "greater than {}",
    "exclusiveMaximum": "less than {}",
    "multipleOf": "a multiple of {}",
    "minItems": "at least {} item(s)",
    "maxItems": "at most {} item(s)",
    "uniqueItems": "items must be unique",
    "minProperties": "at least {} key(s)",
    "maxProperties": "at most {} key(s)",
    "default": "default: {}",
    "examples": "examples: {}",
    "example": "example: {}",
}


@dataclass(frozen=True)
class Change:
    """One thing the original schema had that the target would reject or ignore."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path or '(root)'}: {self.message}"


@dataclass
class _Restore:
    json: bool = False
    drop_null: bool = False
    integer: bool = False
    props: Dict[str, "_Restore"] = field(default_factory=dict)
    items: Optional["_Restore"] = None

    def empty(self) -> bool:
        return not (self.json or self.drop_null or self.integer or self.props or self.items)


@dataclass
class Shim:
    """The adapted schema, what changed, and how to turn model arguments back."""

    target: str
    schema: Dict[str, Any]
    changes: List[Change]
    _restore: _Restore = field(repr=False, default_factory=_Restore)

    @property
    def ok(self) -> bool:
        """True when the original schema was already accepted as it was."""
        return not self.changes

    def restore(self, args: Any) -> Any:
        """Turn arguments the model sent for the adapted schema into arguments for the original.

        Decodes values that were sent as JSON strings, drops the nulls strict mode forces the
        model to send for optional fields, and turns 5.0 back into 5 for integer fields.
        """
        if args is None:
            return {}
        return _apply(args, self._restore)


def _apply(value: Any, node: Optional[_Restore]) -> Any:
    if node is None or node.empty():
        return value
    if node.json:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value
    if node.integer and isinstance(value, float) and value.is_integer():
        return int(value)
    if node.props and isinstance(value, dict):
        out = {}
        for key, val in value.items():
            child = node.props.get(key)
            if child is not None and child.drop_null and val is None:
                continue
            out[key] = _apply(val, child)
        return out
    if node.items is not None and isinstance(value, list):
        return [_apply(v, node.items) for v in value]
    return value


def adapt(schema: Any, target: str, *, max_depth: int = 10) -> Shim:
    """Rewrite ``schema`` (a tool's input schema) so ``target`` accepts it.

    ``target`` is ``"gemini"`` (Gemini API / Vertex AI function declarations) or
    ``"openai-strict"`` (OpenAI function calling with ``strict: true``). The input is not
    modified. Nested objects deeper than ``max_depth`` are passed as JSON strings.
    """
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; use one of {', '.join(TARGETS)}")
    if not isinstance(max_depth, int) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    return _Adapter(target, max_depth).run(schema)


def check(schema: Any, target: str) -> List[Change]:
    """List what ``target`` would reject in ``schema``. Empty means it is accepted as is."""
    return adapt(schema, target).changes


def _fmt(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


class _Adapter:
    def __init__(self, target: str, max_depth: int) -> None:
        self.target = target
        self.gemini = target == GEMINI
        self.max_depth = max_depth
        self.changes: List[Change] = []
        self.defs: Dict[str, Any] = {}

    def note(self, path: str, message: str) -> None:
        change = Change(path, message)
        if change not in self.changes:
            self.changes.append(change)

    # entry point -------------------------------------------------------------------------
    def run(self, schema: Any) -> Shim:
        if schema is None or schema is True or schema == {}:
            schema = {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            raise TypeError("schema must be a JSON object (dict)")
        schema = copy.deepcopy(schema)
        for key in ("$defs", "definitions"):
            defs = schema.get(key)
            if isinstance(defs, dict):
                for name, sub in defs.items():
                    self.defs[f"#/{key}/{name}"] = sub
        root, stack = self.resolve(schema, "", ())
        if not isinstance(root, dict) or root.get("__free__"):
            self.note("", "the root schema can't be resolved: the tool gets no arguments")
            root, stack = {"type": "object", "properties": {}}, ()
        root = self.root_object(root)
        out, restore = self.convert(root, "", stack, 0, root=True)
        return Shim(self.target, out, self.changes, restore)

    def root_object(self, s: Dict[str, Any]) -> Dict[str, Any]:
        branches = s.get("anyOf") or s.get("oneOf")
        if s.get("type") in (None, "object") and isinstance(branches, list) and branches:
            objects = []
            for b in branches:
                b, _ = self.resolve(b, "", ())
                if isinstance(b, dict) and (b.get("type") == "object" or "properties" in b):
                    objects.append(b)
            if len(objects) == len(branches):
                props: Dict[str, Any] = dict(s.get("properties") or {})
                required = None
                for b in objects:
                    for k, v in (b.get("properties") or {}).items():
                        props.setdefault(k, v)
                    r = set(b.get("required") or [])
                    required = r if required is None else required & r
                merged = {k: v for k, v in s.items() if k not in ("anyOf", "oneOf")}
                merged["type"] = "object"
                merged["properties"] = props
                merged["required"] = sorted((required or set()) | set(s.get("required") or []))
                self.note("", "the root is a choice of objects, which tools can't use: merged "
                              "into one object (fields not in every choice became optional)")
                return merged
        if s.get("type") not in (None, "object") or (
                "type" not in s and "properties" not in s and not branches):
            if s.get("type") is not None:
                self.note("", f"the root must be an object, not {_fmt(s.get('type'))}")
            elif s:
                self.note("", "the root has no type: made it an object")
            keep = {k: v for k, v in s.items() if k in ("description", "title")}
            return {"type": "object", "properties": {}, **keep}
        return s

    # $ref and allOf ----------------------------------------------------------------------
    def resolve(self, s: Any, path: str, stack: Tuple[str, ...]):
        """Follow $ref and merge allOf. Returns (schema, stack) or (None, stack) on a loop."""
        if not isinstance(s, dict):
            return s, stack
        seen = 0
        while isinstance(s, dict) and "$ref" in s:
            ref = s["$ref"]
            if ref in stack:
                return None, stack + (ref,)
            target = self.defs.get(ref) if isinstance(ref, str) else None
            if target is None:
                self.note(path, f"can't resolve $ref {_fmt(ref)}: passed as a JSON string")
                return {"__free__": True, **{k: v for k, v in s.items() if k == "description"}}, stack
            if self.gemini:
                self.note(path, f"inlined $ref {ref} (references are not supported)")
            siblings = {k: v for k, v in s.items() if k != "$ref"}
            s = {**copy.deepcopy(target), **siblings}
            stack = stack + (ref,)
            seen += 1
            if seen > 64:
                return None, stack
        if isinstance(s, dict) and isinstance(s.get("allOf"), list):
            parts = []
            for part in s["allOf"]:
                part, stack2 = self.resolve(part, path, stack)
                if part is None:
                    return None, stack2
                if isinstance(part, dict):
                    parts.append(part)
            merged = {k: v for k, v in s.items() if k != "allOf"}
            for part in parts:
                for k, v in part.items():
                    if k == "properties" and isinstance(v, dict):
                        merged.setdefault("properties", {})
                        for pk, pv in v.items():
                            merged["properties"].setdefault(pk, pv)
                    elif k == "required" and isinstance(v, list):
                        merged["required"] = list(dict.fromkeys((merged.get("required") or []) + v))
                    else:
                        merged.setdefault(k, v)
            self.note(path, "merged allOf into one schema")
            return self.resolve(merged, path, stack)
        return s, stack

    # one node ----------------------------------------------------------------------------
    def json_string(self, s: Any, path: str, why: str) -> Tuple[Dict[str, Any], _Restore]:
        self.note(path, f"{why}: passed as a JSON string")
        desc = s.get("description") if isinstance(s, dict) else None
        text = "JSON-encoded value"
        out: Dict[str, Any] = {"type": "string",
                               "description": f"{desc} ({text})" if desc else text}
        return out, _Restore(json=True)

    def convert(self, s: Any, path: str, stack: Tuple[str, ...], depth: int,
                root: bool = False, nullable: bool = False) -> Tuple[Dict[str, Any], _Restore]:
        s, stack = self.resolve(s, path, stack)
        if s is None:
            return self.json_string({}, path, "recursive schema")
        if s is True or s == {} or not isinstance(s, dict):
            return self.json_string({}, path, "any value is allowed")
        if s.get("__free__"):
            return self.json_string(s, path, "unresolved reference")
        if depth > self.max_depth:
            return self.json_string(s, path, f"nested deeper than {self.max_depth} levels")

        s = dict(s)
        if "nullable" in s:
            if s.pop("nullable") is True:
                nullable = True
                if not self.gemini:
                    self.note(path, "nullable (OpenAPI) rewritten as a null type")

        # "type": ["string", "null"] and friends.
        t = s.get("type")
        if isinstance(t, list):
            types = [x for x in t if isinstance(x, str)]
            if "null" in types:
                nullable = True
                types = [x for x in types if x != "null"]
            if self.gemini:
                self.note(path, f"type list {_fmt(t)} rewritten (not supported)")
            if len(types) == 1:
                s["type"] = types[0]
            elif not types:
                s.pop("type")
                return self.json_string(s, path, "the only type is null")
            else:
                s.pop("type")
                s["anyOf"] = [{"type": x} for x in types]
        elif t == "null":
            return self.json_string(s, path, "the only type is null")

        # oneOf / anyOf: drop the null branch, collapse single branches.
        if "oneOf" in s:
            branches = s.pop("oneOf")
            self.note(path, "oneOf changed to anyOf")
            s["anyOf"] = branches if "anyOf" not in s else s["anyOf"]
        if isinstance(s.get("anyOf"), list):
            branches = []
            for b in s["anyOf"]:
                rb, _ = self.resolve(b, path, stack)
                if isinstance(rb, dict) and rb.get("type") == "null" and not (
                        set(rb) & (_STRUCTURAL - {"type"})):
                    nullable = True
                    if self.gemini:
                        self.note(path, "null branch of anyOf changed to nullable")
                    continue
                branches.append(b)
            if not branches:
                s.pop("anyOf")
                return self.json_string(s, path, "the only type is null")
            if len(branches) == 1:
                rest = {k: v for k, v in s.items() if k != "anyOf"}
                inner, stack = self.resolve(branches[0], path, stack)
                if inner is None:
                    return self.json_string(rest, path, "recursive schema")
                if not isinstance(inner, dict) or inner is True:
                    return self.json_string(rest, path, "any value is allowed")
                s = {**inner, **{k: v for k, v in rest.items() if k != "type"}}
                s.pop("anyOf", None)
                return self.convert(s, path, stack, depth, root=root, nullable=nullable)
            s["anyOf"] = branches

        if isinstance(s.get("anyOf"), list):
            out, restore = self.convert_anyof(s, path, stack, depth)
        else:
            out, restore = self.convert_typed(s, path, stack, depth, root)
        return self.nullable(out, nullable), restore

    def nullable(self, out: Dict[str, Any], nullable: bool) -> Dict[str, Any]:
        if not nullable:
            return out
        if self.gemini:
            out["nullable"] = True
            return out
        return _openai_null(out)

    def convert_anyof(self, s, path, stack, depth):
        branches = []
        for b in s["anyOf"]:
            out, _ = self.convert(b, path, stack, depth + 1)
            if out not in branches:
                branches.append(out)
        out: Dict[str, Any] = {"anyOf": branches}
        extra = {k: v for k, v in s.items() if k != "anyOf"}
        self.finish(extra, out, path)
        return out, _Restore()

    def convert_typed(self, s, path, stack, depth, root):
        s = dict(s)
        if "const" in s:
            c = s["const"]
            if self.gemini:
                s.pop("const")
                if isinstance(c, str):
                    s["enum"] = [c]
                    s.setdefault("type", "string")
                    self.note(path, "const changed to a one-value enum")
                else:
                    self.add_note(s, f"must be {_fmt(c)}")
                    self.note(path, "const moved to the description")
        t = s.get("type")
        if t is None:
            t = _infer(s)
            if t is None:
                return self.json_string(s, path, "no type")
            s["type"] = t
            self.note(path, f"missing type: set to {t}")
        if t == "object":
            return self.convert_object(s, path, stack, depth, root)
        if t == "array":
            return self.convert_array(s, path, stack, depth)
        if t not in ("string", "number", "integer", "boolean"):
            return self.json_string(s, path, f"unknown type {_fmt(t)}")
        out: Dict[str, Any] = {"type": t}
        self.finish(s, out, path)
        if "enum" in s:
            self.enum(s, out, t, path)
        return out, _Restore(integer=(t == "integer"))

    def enum(self, s, out, t, path):
        values = s.get("enum")
        if not isinstance(values, list):
            s.pop("enum", None)
            return
        if self.gemini:
            if t != "string":
                s.pop("enum")
                self.add_note(out, "one of: " + ", ".join(_fmt(v) for v in values))
                self.note(path, f"{t} enum moved to the description (Gemini only "
                                "allows enums on strings)")
                return
            strings = [v for v in values if isinstance(v, str) and v != ""]
            if len(strings) != len(values):
                self.note(path, "removed empty or non-string enum values")
            if strings:
                out["enum"] = strings
            s.pop("enum")
            return
        out["enum"] = values
        s.pop("enum")

    def convert_object(self, s, path, stack, depth, root):
        props = s.get("properties")
        props = props if isinstance(props, dict) else {}
        required_in = s.get("required")
        required = [r for r in required_in if isinstance(r, str)] \
            if isinstance(required_in, list) else []
        if required_in is not None and not isinstance(required_in, list):
            self.note(path, "required must be a list of names: ignored")

        # FastAPI / OpenAPI 2 style "required": true on the property itself.
        cleaned = {}
        for name, ps in props.items():
            if isinstance(ps, dict) and isinstance(ps.get("required"), bool):
                ps = dict(ps)
                flag = ps.pop("required")
                self.note(_join(path, name), "boolean required flag moved into the parent's "
                                             "required list")
                if flag and name not in required:
                    required.append(name)
            cleaned[name] = ps
        props = cleaned

        missing = [r for r in required if r not in props]
        if missing:
            self.note(path, "required lists undefined properties: " + ", ".join(missing))
            required = [r for r in required if r in props]

        extra = s.get("additionalProperties")
        if not props:
            if root:
                out: Dict[str, Any] = {"type": "object", "properties": {}}
                if extra not in (None, False) or s.get("patternProperties"):
                    self.note(path, "free-form arguments can't be described: the tool gets "
                                    "no arguments")
                if not self.gemini:
                    out["required"] = []
                    out["additionalProperties"] = False
                self.finish(s, out, path, skip={"additionalProperties", "patternProperties",
                                                "propertyNames"})
                return out, _Restore()
            return self.json_string(s, path, "object without fixed properties")

        out = {"type": "object", "properties": {}}
        restore = _Restore()
        for name, ps in props.items():
            child_path = _join(path, name)
            child, child_restore = self.convert(ps, child_path, stack, depth + 1)
            if not self.gemini and name not in required:
                if not _is_nullable(child):
                    child = _openai_null(child)
                    child_restore.drop_null = True
            out["properties"][name] = child
            if not child_restore.empty():
                restore.props[name] = child_restore
        optional = [n for n in props if n not in required]
        if self.gemini:
            if required:
                out["required"] = required
            if "additionalProperties" in s:
                if extra not in (False, None):
                    self.note(path, "extra keys (additionalProperties) are not supported: "
                                    "only the listed properties can be sent")
                else:
                    self.note(path, "removed additionalProperties (not supported)")
        else:
            out["required"] = list(props)
            out["additionalProperties"] = False
            if optional:
                self.note(path, "strict mode needs every property required: optional "
                                + ", ".join(optional) + " made nullable")
            if extra is None:
                self.note(path, "strict mode needs additionalProperties: false")
            elif extra is not False:
                self.note(path, "extra keys (additionalProperties) are not allowed in strict "
                                "mode: only the listed properties can be sent")
        self.finish(s, out, path, skip={"additionalProperties"})
        return out, restore

    def convert_array(self, s, path, stack, depth):
        items = s.get("items")
        if "prefixItems" in s or isinstance(items, list):
            return self.json_string(s, path, "tuple arrays are not supported")
        if items is None or items is True or items == {}:
            return self.json_string(s, path, "array without an items type")
        child, child_restore = self.convert(items, path + "[]", stack, depth + 1)
        out = {"type": "array", "items": child}
        self.finish(s, out, path)
        return out, _Restore(items=None if child_restore.empty() else child_restore)

    # keywords ----------------------------------------------------------------------------
    def finish(self, s, out, path, skip=frozenset()):
        """Copy the keywords the target accepts; describe or drop the rest."""
        allowed = _GEMINI_KEYS if self.gemini else _OPENAI_KEYS
        t = out.get("type")
        moved = []
        for k, v in s.items():
            if k in out or k in skip or k == "__free__":
                continue
            if k in _STRUCTURAL and k not in ("const",):
                continue
            if k in _SILENT:
                continue
            if k == "default" and v is None:
                continue
            if k == "format":
                if self.gemini:
                    ok = (t == "string" and v in _GEMINI_STRING_FORMATS) or (
                        t in ("number", "integer") and v in _GEMINI_NUMBER_FORMATS)
                else:
                    ok = t == "string" and v in _OPENAI_FORMATS
                if ok:
                    out[k] = v
                else:
                    moved.append((k, v))
                continue
            if k == "pattern" and not self.gemini and isinstance(v, str) and _LOOKAROUND.search(v):
                moved.append((k, v))
                continue
            if k in allowed:
                out[k] = v
            elif k in _PHRASES:
                moved.append((k, v))
            elif k in ("description", "title"):
                out[k] = v
            else:
                self.note(path, f"removed unsupported keyword {k}")
        if moved:
            self.add_note(out, "; ".join(_PHRASES[k].format(_fmt(v)) for k, v in moved))
            self.note(path, "moved " + ", ".join(k for k, _ in moved) + " to the description "
                            "(not supported)")

    @staticmethod
    def add_note(out, text):
        desc = out.get("description")
        out["description"] = f"{desc} ({text})" if desc else text[0].upper() + text[1:]


def _infer(s: Dict[str, Any]) -> Optional[str]:
    if "properties" in s or "additionalProperties" in s or "required" in s:
        return "object"
    if "items" in s or "prefixItems" in s:
        return "array"
    values = s.get("enum")
    if isinstance(values, list) and values:
        if all(isinstance(v, str) for v in values):
            return "string"
        if all(isinstance(v, bool) for v in values):
            return "boolean"
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return "integer"
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            return "number"
    if any(k in s for k in ("minLength", "maxLength", "pattern")):
        return "string"
    if any(k in s for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")):
        return "number"
    return None


def _is_nullable(out: Dict[str, Any]) -> bool:
    t = out.get("type")
    if isinstance(t, list) and "null" in t:
        return True
    return any(b.get("type") == "null" for b in out.get("anyOf") or [] if isinstance(b, dict))


def _openai_null(out: Dict[str, Any]) -> Dict[str, Any]:
    if _is_nullable(out):
        return out
    if "anyOf" in out:
        out["anyOf"] = out["anyOf"] + [{"type": "null"}]
        return out
    t = out.get("type")
    if isinstance(t, str):
        out["type"] = [t, "null"]
        if isinstance(out.get("enum"), list) and None not in out["enum"]:
            out["enum"] = out["enum"] + [None]
        return out
    return {"anyOf": [out, {"type": "null"}]}
