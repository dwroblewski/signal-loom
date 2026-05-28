"""Enrichment dict validation and sanitization.

Two public functions:

- :func:`check` — validates an enrichment dict against ``schema.json`` AND
  that every item in ``topics.primary`` is present in the supplied vocabulary
  set.  Returns ``(True, [])`` on success or ``(False, [error_strings])`` on
  failure.

- :func:`sanitize` — the security boundary.  Returns a **new** dict containing
  ONLY keys defined in ``required_fields ∪ optional_fields`` from the schema,
  and only when the value matches the expected type.  Wrong-typed values and
  any key not in the allow-list are silently dropped.  This function never
  raises — malformed or non-dict inputs return an empty dict.

  For list-typed fields and list-typed subkeys, only ``str`` elements are
  kept; non-string elements (e.g. injected dicts) are silently dropped.

  Size bounds are enforced: lists are truncated to their ``max_items`` limit;
  ``summary`` values longer than :data:`_SUMMARY_MAX_LEN` characters are
  dropped as malformed.
"""

from __future__ import annotations

import json
import functools
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema loading (cached on first call)
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "schema.json"

# Maximum acceptable summary length.  Values beyond this are treated as
# malformed (possible injection of large payloads) and dropped.
_SUMMARY_MAX_LEN: int = 20_000

# Field names that are list-typed at the top level (not subkeys).
# Only str elements are kept when sanitizing these.
_STRING_LIST_FIELDS: frozenset[str] = frozenset({"key_takeaways"})

# Subkeys of dict-type fields whose elements must be str.
# Maps field_name -> set of subkey names.
_STRING_LIST_SUBKEYS: dict[str, frozenset[str]] = {
    "topics": frozenset({"primary", "secondary"}),
    "entities": frozenset({"organizations", "people"}),
}


@functools.lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    """Load and cache ``core/schema.json``.  Cached after first read."""
    with _SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

_PY_TYPES: dict[str, type] = {
    "bool": bool,
    "str": str,
    "dict": dict,
    "list": list,
    "int": int,
    "float": float,
}


def _matches_type(value: Any, type_name: str) -> bool:
    """Return True if *value* is an instance of the Python type for *type_name*.

    Note: ``bool`` is a subclass of ``int`` in Python, so we check ``bool``
    explicitly first to avoid false positives when spec says ``int``.

    Returns False for unknown *type_name* values.
    """
    if type_name == "bool":
        return isinstance(value, bool)
    if type_name == "int":
        # Exclude bool so that True/False don't masquerade as ints.
        return isinstance(value, int) and not isinstance(value, bool)
    expected = _PY_TYPES.get(type_name)
    if expected is None:
        return False
    return isinstance(value, expected)


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------

def check(d: Any, vocabulary: set[str]) -> tuple[bool, list[str]]:
    """Validate an enrichment dict against the schema and the topic vocabulary.

    Checks performed (in order):
    1. *d* is a ``dict``.  If not, returns ``(False, ["enrichment output is
       not a mapping"])`` immediately.
    2. All ``required_fields`` are present.
    3. Each present field (required + optional) matches its spec:
       - correct Python type
       - ``const`` equality (for ``enriched``)
       - ``min_len`` on strings
       - ``max_items`` on lists
       - required ``subkeys`` present and correct type (for dicts)
    4. Every item in ``topics.primary`` is a member of *vocabulary*.

    Args:
        d:          Enrichment dict to validate.
        vocabulary: Set of allowed topic strings for ``topics.primary``.

    Returns:
        ``(True, [])`` if all checks pass; ``(False, [error_message, ...])``
        otherwise.  Validation is exhaustive — all errors are collected before
        returning.
    """
    if not isinstance(d, dict):
        return (False, ["enrichment output is not a mapping"])

    schema = _load_schema()
    required: list[str] = schema["required_fields"]
    optional: list[str] = schema["optional_fields"]
    specs: dict[str, Any] = schema["field_specs"]

    errors: list[str] = []

    # 1. Required fields present
    for field in required:
        if field not in d:
            errors.append(f"Missing required field: '{field}'")

    # 2. Field-level spec checks (only for fields that are present)
    all_known = set(required) | set(optional)
    for field in all_known:
        if field not in d:
            continue  # already flagged above if required; optional absence is OK
        value = d[field]
        spec = specs.get(field, {})
        type_name: str = spec.get("type", "")

        # Type check
        if type_name and not _matches_type(value, type_name):
            errors.append(
                f"Field '{field}' has wrong type: expected {type_name}, "
                f"got {type(value).__name__}"
            )
            continue  # further checks on this field are meaningless

        # const equality
        if "const" in spec and value != spec["const"]:
            errors.append(
                f"Field '{field}' must equal {spec['const']!r}, got {value!r}"
            )

        # min_len (strings) — use field name dynamically, not hardcoded 'summary'
        if "min_len" in spec and isinstance(value, str):
            if len(value) < spec["min_len"]:
                errors.append(
                    f"Field '{field}' is too short: minimum {spec['min_len']} "
                    f"characters, got {len(value)}"
                )

        # max_items (lists)
        if "max_items" in spec and isinstance(value, list):
            if len(value) > spec["max_items"]:
                errors.append(
                    f"Field '{field}' exceeds max_items={spec['max_items']}: "
                    f"got {len(value)} items"
                )

        # subkeys (dicts) — use _matches_type for type check; unknown subtype → error
        if "subkeys" in spec and isinstance(value, dict):
            for subkey, subtype in spec["subkeys"].items():
                if subkey not in value:
                    errors.append(
                        f"Field '{field}' is missing required subkey '{subkey}'"
                    )
                elif subtype not in _PY_TYPES:
                    errors.append(
                        f"Field '{field}.{subkey}' has unknown subtype in schema: "
                        f"'{subtype}'"
                    )
                elif not _matches_type(value[subkey], subtype):
                    errors.append(
                        f"Field '{field}.{subkey}' has wrong type: "
                        f"expected {subtype}"
                    )

    # 3. Vocabulary check for topics.primary
    topics = d.get("topics")
    if isinstance(topics, dict):
        primary = topics.get("primary", [])
        if isinstance(primary, list):
            for term in primary:
                if term not in vocabulary:
                    errors.append(
                        f"topics.primary item '{term}' not in vocab"
                    )

    ok = len(errors) == 0
    return (True, []) if ok else (False, errors)


# ---------------------------------------------------------------------------
# sanitize()
# ---------------------------------------------------------------------------

def sanitize(d: Any) -> dict[str, Any]:
    """Return a sanitized copy of *d* containing only allow-listed keys.

    This is the **security boundary** between untrusted model output and the
    rest of the pipeline.  The function:

    - Returns ``{}`` immediately for any non-dict input (None, str, int, list,
      etc.) — never raises.
    - Keeps only keys in ``required_fields ∪ optional_fields`` (allow-list).
    - Drops any key whose value does not match the expected type in the spec.
    - For ``dict`` fields (``topics``, ``entities``), further filters subkeys
      to only those declared in ``subkeys``, and only when they are lists.
    - For list-typed fields and list-typed subkeys whose elements must be
      strings (``key_takeaways``, ``topics.primary``, ``topics.secondary``,
      ``entities.organizations``, ``entities.people``), non-string elements
      are silently dropped so injected dicts cannot reach downstream consumers.
    - Enforces size bounds: lists are truncated to ``max_items``; ``summary``
      values longer than :data:`_SUMMARY_MAX_LEN` characters are dropped.

    This function never raises.

    Args:
        d: Raw enrichment value, potentially containing injected keys or
           a non-dict type.

    Returns:
        A new, sanitized dict.  Returns ``{}`` for non-dict input.
        Keys outside the allow-list are absent.
    """
    if not isinstance(d, dict):
        return {}

    schema = _load_schema()
    required: list[str] = schema["required_fields"]
    optional: list[str] = schema["optional_fields"]
    specs: dict[str, Any] = schema["field_specs"]

    allowed_keys = set(required) | set(optional)
    result: dict[str, Any] = {}

    for key in allowed_keys:
        if key not in d:
            continue
        value = d[key]
        spec = specs.get(key, {})
        type_name: str = spec.get("type", "")

        # Type gate
        if type_name and not _matches_type(value, type_name):
            continue  # wrong type — drop entirely

        # Size gate for strings (e.g. summary)
        if type_name == "str" and len(value) > _SUMMARY_MAX_LEN:
            continue  # oversized — treat as malformed, drop

        # For list fields: enforce max_items (truncate) and element type filter
        if type_name == "list":
            max_items: int | None = spec.get("max_items")
            filtered_list = value
            # Keep only str elements for string-list fields
            if key in _STRING_LIST_FIELDS:
                filtered_list = [el for el in filtered_list if isinstance(el, str)]
            # Truncate to max_items
            if max_items is not None and len(filtered_list) > max_items:
                filtered_list = filtered_list[:max_items]
            result[key] = filtered_list
            continue

        # For dict fields, restrict to declared subkeys (lists only)
        if type_name == "dict" and "subkeys" in spec:
            allowed_subkeys: dict[str, str] = spec["subkeys"]
            filtered_dict: dict[str, list[Any]] = {}
            for subkey, subtype in allowed_subkeys.items():
                if subkey not in value:
                    continue
                subvalue = value[subkey]
                if not _matches_type(subvalue, subtype):
                    continue
                # For list subkeys, filter to str-only elements where required
                if subtype == "list":
                    str_list_subkeys = _STRING_LIST_SUBKEYS.get(key, frozenset())
                    if subkey in str_list_subkeys:
                        subvalue = [el for el in subvalue if isinstance(el, str)]
                filtered_dict[subkey] = subvalue
            result[key] = filtered_dict
        else:
            result[key] = value

    return result
