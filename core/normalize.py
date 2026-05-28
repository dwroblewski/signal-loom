"""Entity normalization: alias mapping + case-insensitive deduplication.

This module is the canonical place for entity name canonicalization used by
both the enrichment writeback path and the validation pipeline.
"""

from __future__ import annotations


def entities(items: list[str], aliases: dict[str, str]) -> list[str]:
    """Normalize a flat list of entity name strings.

    Algorithm:
    1. Build a lowercase-keyed alias table from *aliases*.
    2. For each item, look up ``item.casefold()`` in the alias table;
       if found, replace with the canonical form; otherwise keep the
       original string as-is.
    3. Deduplicate case-insensitively (first/canonical casing wins).
    4. Return a sorted list of unique canonical strings.

    Args:
        items:   Raw entity strings (may contain duplicates / mixed case).
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
        entities_dict: Dict with at least ``organizations`` and ``people`` keys,
                       each containing a list of entity name strings.
        aliases:       Alias table passed through to :func:`entities`.

    Returns:
        A new dict with the same subkeys, each value replaced by its
        normalized entity list.  Unknown subkeys are preserved unchanged.
    """
    result: dict[str, list[str]] = {}
    known_subkeys = {"organizations", "people"}
    for subkey, value in entities_dict.items():
        if subkey in known_subkeys and isinstance(value, list):
            result[subkey] = entities(value, aliases=aliases)
        else:
            result[subkey] = value
    return result
