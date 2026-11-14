#!/usr/bin/env python3
"""Which claim pages derived views must skip (Z2 spec §7.5a, Raouf 2026-11-14).

A generated claim page (`authority: structured`) whose status is superseded,
retracted, orphaned or erased stays in `wiki/claims/` for Obsidian and
`claim show/history`, but is not chunked for search (hence not in `context`),
not a graph node, not in the recall digest and not in federation discovery.
Reusing an outdated value is the field's worst-measured memory failure (§2.3).
Legacy pages carry no `authority` and are never hidden by this rule.
"""

from __future__ import annotations

import re
from pathlib import Path

NONCURRENT = ("superseded", "retracted", "orphaned", "erased")
CLAIMS_PREFIX = "wiki/claims/"

_LINE = re.compile(r'^([A-Za-z_]+):\s*"?([^"\n]*?)"?\s*$')


def is_hidden_claim_page(rel_path: str, frontmatter: dict) -> bool:
    rel = str(rel_path).replace("\\", "/")
    return (rel.startswith(CLAIMS_PREFIX)
            and frontmatter.get("authority") == "structured"
            and frontmatter.get("status") in NONCURRENT)


def _front(path: Path) -> dict:
    """The flat `key: value` lines of a page's frontmatter (first 4 KiB only)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return {}
    if not head.startswith("---"):
        return {}
    end = head.find("\n---", 3)
    out = {}
    for line in (head[3:end] if end != -1 else head).splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def page_is_hidden(base, rel_path) -> bool:
    """True if the page at `base/rel_path` must not reach any derived view."""
    rel = str(rel_path).replace("\\", "/")
    if not rel.startswith(CLAIMS_PREFIX):
        return False
    return is_hidden_claim_page(rel, _front(Path(base) / rel))
