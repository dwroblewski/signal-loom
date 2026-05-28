"""Enrichment dict validation and sanitization.

Two public functions:

- :func:`check` — validates an enrichment dict against ``schema.json`` AND
  that every item in ``topics.primary`` is present in the supplied vocabulary
  set.  Returns ``(True, [])`` on success or ``(False, [error_strings])`` on
  failure.

- :func:`sanitize` — the security boundary.  Returns a **new** dict containing
  ONLY keys defined in ``required_fields ∪ optional_fields`` from the schema,
  and only when the value matches the expected type.  Wrong-typed values and
  any key not in the allow-list are silently dropped.  This prevents prompt-
  injected model responses from smuggling arbitrary keys through to downstream
  consumers.
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

def check(d: dict[str, Any], vocabulary: set[str]) -> tuple[bool, list[str]]:
    """Validate an enrichment dict against the schema and the topic vocabulary.

    Checks performed (in order):
    1. All ``required_fields`` are present.
    2. Each present field (required + optional) matches its spec:
       - correct Python type
       - ``const`` equality (for ``enriched``)
       - ``min_len`` on strings
       - ``max_items`` on lists
       - required ``subkeys`` present and are lists (for dicts)
    3. Every item in ``topics.primary`` is a member of *vocabulary*.

    Args:
        d:          Enrichment dict to validate.
        vocabulary: Set of allowed topic strings for ``topics.primary``.

    Returns:
        ``(True, [])`` if all checks pass; ``(False, [error_message, ...])``
        otherwise.  Validation is exhaustive — all errors are collected before
        returning.
    """
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

        # min_len (strings)
        if "min_len" in spec and isinstance(value, str):
            if len(value) < spec["min_len"]:
                errors.append(
                    f"Field 'summary' is too short: minimum {spec['min_len']} "
                    f"characters, got {len(value)}"
                )

        # max_items (lists)
        if "max_items" in spec and isinstance(value, list):
            if len(value) > spec["max_items"]:
                errors.append(
                    f"Field '{field}' exceeds max_items={spec['max_items']}: "
                    f"got {len(value)} items"
                )

        # subkeys (dicts)
        if "subkeys" in spec and isinstance(value, dict):
            for subkey, subtype in spec["subkeys"].items():
                if subkey not in value:
                    errors.append(
                        f"Field '{field}' is missing required subkey '{subkey}'"
                    )
                elif not isinstance(value[subkey], _PY_TYPES.get(subtype, object)):
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

def sanitize(d: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy of *d* containing only allow-listed keys.

    This is the **security boundary** between untrusted model output and the
    rest of the pipeline.  The function:

    - Keeps only keys in ``required_fields ∪ optional_fields`` (allow-list).
    - Drops any key whose value does not match the expected type in the spec.
    - For ``dict`` fields (``topics``, ``entities``), further filters subkeys
      to only those declared in ``subkeys``, and only when they are lists.
    - Never raises — malformed values are silently dropped.

    Args:
        d: Raw enrichment dict, potentially containing injected keys.

    Returns:
        A new, sanitized dict.  Keys outside the allow-list are absent.
    """
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

        # For dict fields, restrict to declared subkeys (lists only)
        if type_name == "dict" and "subkeys" in spec:
            allowed_subkeys: dict[str, str] = spec["subkeys"]
            filtered: dict[str, list[Any]] = {}
            for subkey, subtype in allowed_subkeys.items():
                if subkey in value and isinstance(value[subkey], _PY_TYPES.get(subtype, object)):
                    filtered[subkey] = value[subkey]
            result[key] = filtered
        else:
            result[key] = value

    return result
