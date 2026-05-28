"""Entity normalization: alias mapping + case-insensitive deduplication.

This module is the canonical place for entity name canonicalization used by
both the enrichment writeback path and the validation pipeline.
"""

from __future__ import annotations


def entities(items: list[str], aliases: dict[str, str]) -> list[str]:
    """Normalize a flat list of entity name strings.

    Algorithm:
    1. Build a lowercase-keyed alias table from *aliases*.
    2. For each item, skip non-string elements (log-safe, no crash).
    3. Look up ``item.casefold()`` in the alias table; if found, replace with
       the canonical form; otherwise keep the original string as-is.
    4. Deduplicate case-insensitively (first/canonical casing wins).
    5. Return a sorted list of unique canonical strings.

    Args:
        items:   Raw entity strings (may contain duplicates / mixed case /
                 non-string elements which are silently skipped).
        aliases: Mapping of ``{lowercase_raw: canonical_name}``.  Keys
                 are matched case-insensitively; values are used verbatim
                 as the canonical form.

    Returns:
        Sorted list of unique canonical entity names.

    Example::

        >>> entities(["Anthropic PBC", "anthropic", "OpenAI"],
        ...          aliases={"anthropic pbc": "Anthropic"})
        ['Anthropic', 'OpenAI']
    """
    # Normalize alias keys to lowercase so lookup is always case-insensitive.
    normalized_aliases: dict[str, str] = {k.casefold(): v for k, v in aliases.items()}

    seen_casefold: dict[str, str] = {}  # casefold_key -> canonical_string

    for item in items:
        # Issue 6: guard against non-string elements — skip silently.
        if not isinstance(item, str):
            continue
        key = item.casefold()
        canonical = normalized_aliases.get(key, item)
        canon_key = canonical.casefold()
        if canon_key not in seen_casefold:
            seen_casefold[canon_key] = canonical

    return sorted(seen_casefold.values())


def normalize_entities_dict(entities_dict: dict[str, list[str]], aliases: dict[str, str]) -> dict[str, list[str]]:
    """Normalize the ``organizations`` and ``people`` sublists of an enrichment
    ``entities`` dict.

    Convenience wrapper used by the writeback path so callers don't have to
    loop over subkeys themselves.

    Args:
        entities_dict: Dict potentially containing ``organizations`` and/or
                       ``people`` keys, each holding a list of entity name
                       strings.  Unknown subkeys are dropped.
        aliases:       Alias table passed through to :func:`entities`.

    Returns:
        A new dict containing only the known subkeys (``organizations``,
        ``people``), each value replaced by its normalized entity list.
        Unknown subkeys are silently dropped to prevent injection.
        Known subkeys whose values are not lists are also silently dropped.
    """
    result: dict[str, list[str]] = {}
    known_subkeys = {"organizations", "people"}
    for subkey, value in entities_dict.items():
        # Issue 7: only emit known subkeys — drop unknown ones.
        if subkey not in known_subkeys:
            continue
        if isinstance(value, list):
            result[subkey] = entities(value, aliases=aliases)
        # Non-list values for known subkeys are silently dropped.
    return result
