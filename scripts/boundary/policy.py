"""The source-class x sink-class matrix.

Organised around sinks rather than "public" because of leak #1 of 2026-09-15: an
evaluation item quoted into a page that was never tracked and never pushed still
outranked that item's own answer once indexed. Contamination with zero
publication — a failure no confidentiality-shaped model describes.
"""
SINKS = ("PUBLIC_ARTIFACT", "RETRIEVAL_CORPUS", "EVAL_OUTPUT", "PRIVATE_LOCAL")

_BLOCKED = frozenset({
    ("PRIVATE", "PUBLIC_ARTIFACT"),
    ("INSTRUMENT", "PUBLIC_ARTIFACT"),
    ("INSTRUMENT", "RETRIEVAL_CORPUS"),
})


def blocked_pairs() -> set:
    return set(_BLOCKED)


SOURCE_CLASSES = ("PRIVATE", "INSTRUMENT", "PUBLIC")


class PolicyError(ValueError):
    """An unrecognised class or an empty sink set. Always fatal, never 'allow'."""


def is_blocked(source_class: str, sinks) -> bool:
    """Deny overrides allow (Global Constraint 2), and unknown input FAILS CLOSED.

    The previous draft returned `any(...)` over an unrecognised class, so a typo
    produced ALLOW: `is_blocked("PRVATE", ["PUBLIC_ARTIFACT"])` was False. An
    empty sink list did the same. For a gate, a misspelling must never be the
    difference between blocked and published.
    """
    if isinstance(sinks, str):
        sinks = [sinks]
    sinks = list(sinks)
    if not sinks:
        raise PolicyError("empty sink set: a scan must name at least one sink")
    if source_class not in SOURCE_CLASSES:
        raise PolicyError(
            f"unknown source class {source_class!r}; expected one of {SOURCE_CLASSES}")
    unknown = set(sinks) - set(SINKS)
    if unknown:
        raise PolicyError(f"unknown sink label(s): {sorted(unknown)}")
    return any((source_class, s) in _BLOCKED for s in sinks)
