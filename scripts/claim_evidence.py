#!/usr/bin/env python3
"""Z2 evidence anchors: where a quote sits, what the source said, and whether it
still says it (spec §7.4).

The selector follows the W3C Web Annotation Data Model: `TextQuoteSelector`
(`exact`, `prefix`, `suffix`) is the anchor, `TextPositionSelector` (`start`,
`end`, character offsets in the reader's text) is a hint the W3C itself calls
"very brittle", and `state` pins the source by `sha256` of its FILE bytes - the
one thing the W3C model lacks for an immutable-source fingerprint.

`resolve` measures anchoring, not truth: an `intact` quote can still be wrong.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from scripts.claim_store import ClaimRefused
from scripts.evidence_admission import EvidenceRejected, admit_quote

READERS = ("utf8", "extract_text@1")
OUTCOMES = ("intact", "moved", "moved_normalised", "ambiguous", "changed", "gone")
CONTEXT = 32          # TextQuoteSelector prefix/suffix length
CAPSULE = 64          # capsule context each side

_WS = re.compile(r"\s+")


class SourceUnavailable(Exception):
    """The source cannot be read at all (missing, permission, I/O)."""


class SourceUndecodable(Exception):
    """The source's bytes are not text under the recorded reader."""


def _path(source_ref: str, root) -> Path:
    p = Path(source_ref)
    return p if p.is_absolute() else Path(root) / p


def read_source(source_ref: str, reader: str, root) -> tuple:
    """Return (raw file bytes, the reader's text). The text is exactly what the
    writer saw when it admitted the quote: `memory.add_claim` reads with
    `open(..., encoding="utf-8").read()` (universal newlines), extraction with
    `scripts.ingest.extract_text`."""
    if reader not in READERS:
        raise ValueError(f"unknown evidence reader {reader!r}; known: {READERS}")
    path = _path(source_ref, root)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceUnavailable(f"{type(exc).__name__}: {exc.strerror or exc}") from None
    try:
        if reader == "utf8":
            with open(path, "r", encoding="utf-8", errors="strict") as f:
                text = f.read()
        else:
            from scripts.ingest import extract_text
            text = extract_text(str(path))
    except UnicodeDecodeError as exc:
        raise SourceUndecodable(f"not {reader} text: {exc.reason}") from None
    except OSError as exc:
        raise SourceUnavailable(f"{type(exc).__name__}: {exc.strerror or exc}") from None
    return raw, text


def anchor(source_ref: str, quote: str, reader: str, root, now: str) -> dict:
    """Admit `quote` against the source and describe where it is.
    Returns {"selector": {...}, "capsule": str}."""
    try:
        raw, text = read_source(source_ref, reader, root)
    except (SourceUnavailable, SourceUndecodable) as exc:
        raise ClaimRefused("EVIDENCE_REJECTED", f"source unreadable: {exc}") from None
    try:
        admit_quote(quote, text)
    except EvidenceRejected as exc:
        raise ClaimRefused("EVIDENCE_REJECTED", str(exc)) from None
    start = text.index(quote)
    end = start + len(quote)
    return {
        "selector": {
            "TextQuoteSelector": {"exact": quote,
                                  "prefix": text[max(0, start - CONTEXT):start],
                                  "suffix": text[end:end + CONTEXT]},
            "TextPositionSelector": {"start": start, "end": end},
            "state": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                      "captured_at": now},
        },
        "capsule": text[max(0, start - CAPSULE):end + CAPSULE],
    }


def _occurrences(text: str, needle: str) -> list:
    out, i = [], text.find(needle)
    while i != -1 and needle:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def _norm(s: str) -> str:
    return _WS.sub(" ", s)


def resolve(selector: dict, source_ref: str, reader: str, root) -> tuple:
    """(outcome, detail). Never raises for an unreadable or undecodable source."""
    try:
        raw, text = read_source(source_ref, reader, root)
    except SourceUnavailable as exc:
        return "gone", str(exc)
    except SourceUndecodable as exc:
        return "changed", str(exc)
    except Exception as exc:  # noqa: BLE001 - a reader failure must not abort a verify run
        return "changed", f"reader failed: {type(exc).__name__}: {exc}"
    quote = selector["TextQuoteSelector"]
    exact, prefix, suffix = quote["exact"], quote["prefix"], quote["suffix"]
    if hashlib.sha256(raw).hexdigest() == selector["state"]["sha256"]:
        return "intact", None
    hits = _occurrences(text, exact)
    if len(hits) == 1:
        return "moved", f"at {hits[0]}"
    if len(hits) > 1:
        ctx = [h for h in hits
               if text[max(0, h - len(prefix)):h] == prefix
               and text[h + len(exact):h + len(exact) + len(suffix)] == suffix]
        if len(ctx) == 1:
            return "moved", f"at {ctx[0]} (by context)"
        return "ambiguous", f"{len(hits)} matches, {len(ctx)} with the recorded context"
    norm_hits = _occurrences(_norm(text), _norm(exact))
    if len(norm_hits) == 1:
        return "moved_normalised", "found after whitespace normalisation"
    if len(norm_hits) > 1:
        return "ambiguous", f"{len(norm_hits)} matches after whitespace normalisation"
    return "changed", "quote not found"


def _norm_map(text: str) -> tuple:
    """(normalised text, index map) - every whitespace run becomes one space;
    map[i] is the original index of normalised char i."""
    out, idx, i = [], [], 0
    while i < len(text):
        if text[i].isspace():
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            out.append(" ")
            idx.append(i)
            i = j
        else:
            out.append(text[i])
            idx.append(i)
            i += 1
    return "".join(out), idx


def anchor_normalised(source_ref: str, quote: str, reader: str, root, now: str) -> dict:
    """Migration only (spec §7.8): a legacy quote that matches its source only after
    whitespace normalisation. `exact` keeps the legacy quote verbatim (provenance);
    the position, context and fingerprint describe the real span in the source, so
    `resolve` reports `intact` while the file is unchanged and `moved_normalised`
    after it moves. Refuses unless the normalised quote occurs exactly once."""
    try:
        raw, text = read_source(source_ref, reader, root)
    except (SourceUnavailable, SourceUndecodable) as exc:
        raise ClaimRefused("EVIDENCE_REJECTED", f"source unreadable: {exc}") from None
    if quote is None or not quote.strip():
        raise ClaimRefused("EVIDENCE_REJECTED", "evidence quote is empty")
    norm_text, idx = _norm_map(text)
    needle = _norm(quote).strip()
    hits = _occurrences(norm_text, needle)
    if len(hits) != 1:
        # More than one match is `ambiguous` (spec §7.4), not a lost quote.
        raise ClaimRefused("EVIDENCE_AMBIGUOUS" if hits else "EVIDENCE_REJECTED",
                           f"normalised quote found {len(hits)} times (need exactly 1)")
    start = idx[hits[0]]
    last = idx[hits[0] + len(needle) - 1]
    end = last + 1
    return {
        "selector": {
            "TextQuoteSelector": {"exact": quote,
                                  "prefix": text[max(0, start - CONTEXT):start],
                                  "suffix": text[end:end + CONTEXT]},
            "TextPositionSelector": {"start": start, "end": end},
            "state": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                      "captured_at": now},
        },
        "capsule": text[max(0, start - CAPSULE):end + CAPSULE],
    }
