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

READERS = ("utf8", "extract_text@1", "pdf_pages@1")
PDF_READER = "pdf_pages@1"   # IOU-Z2-4: pypdf, one text per page, never merged or normalised
PAGE_BREAK = "\f"           # joins the pages; a quote can never contain it
RFC3778 = "http://tools.ietf.org/rfc/rfc3778"
OUTCOMES = ("intact", "moved", "moved_normalised", "ambiguous", "changed", "gone")
CONTEXT = 32          # TextQuoteSelector prefix/suffix length
CAPSULE = 64          # capsule context each side

_WS = re.compile(r"\s+")
# Directories whose children are mount points of removable or network disks.
MOUNT_PARENTS = ("/Volumes", "/mnt", "/media")
UNMOUNTED = "volume not mounted"


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
        elif reader == PDF_READER:
            text = PAGE_BREAK.join(_pdf_pages(path))
        else:
            from scripts.ingest import extract_text
            text = extract_text(str(path))
    except UnicodeDecodeError as exc:
        raise SourceUndecodable(f"not {reader} text: {exc.reason}") from None
    except OSError as exc:
        raise SourceUnavailable(f"{type(exc).__name__}: {exc.strerror or exc}") from None
    return raw, text


def _pdf_pages(path) -> list:
    """Per-page text of a PDF, exactly as pypdf extracts it."""
    try:
        import pypdf
        return [page.extract_text() or "" for page in pypdf.PdfReader(str(path)).pages]
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types on a bad file
        raise SourceUndecodable(f"not a readable PDF: {type(exc).__name__}: {exc}") from None


def _extractor() -> str:
    import pypdf
    return f"pypdf {pypdf.__version__}"


def page_of(text: str, pos: int) -> int:
    """1-based page number of character `pos` in a `pdf_pages@1` text."""
    return text.count(PAGE_BREAK, 0, pos) + 1


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
    if reader == PDF_READER and len(_occurrences(text, quote)) > 1:
        # Design §4.2 (final review I2): a page locator for a quote that occurs more
        # than once would name one page silently. Quote more of the passage.
        pages = sorted({page_of(text, h) for h in _occurrences(text, quote)})
        raise ClaimRefused("EVIDENCE_AMBIGUOUS",
                           f"the quote occurs {len(_occurrences(text, quote))} times "
                           f"(pages {', '.join(map(str, pages))}); quote a longer passage")
    start = text.index(quote)
    end = start + len(quote)
    out = {
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
    if reader == PDF_READER:
        # W3C FragmentSelector conforming to RFC 3778 (PDF fragment identifiers).
        out["selector"]["FragmentSelector"] = {"conformsTo": RFC3778,
                                               "value": f"page={page_of(text, start)}"}
        out["selector"]["state"]["extractor"] = _extractor()
    return out


def _occurrences(text: str, needle: str) -> list:
    out, i = [], text.find(needle)
    while i != -1 and needle:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def _norm(s: str) -> str:
    return _WS.sub(" ", s)


def unmounted_volume(source_ref: str, root) -> str | None:
    """The missing mount point (e.g. `/Volumes/Archive`) when the source lives on
    a disk that is not mounted, else None. Such a source is unavailable, not
    lost, and must never orphan a claim."""
    path = _path(source_ref, root)
    for parent in MOUNT_PARENTS:
        try:
            rel = path.relative_to(parent)
        except ValueError:
            continue
        if rel.parts:
            mount = Path(parent) / rel.parts[0]
            if not mount.exists():
                return str(mount)
    return None


def _read_cached(source_ref, reader, root, cache):
    """read_source, memoised per (source, reader) for one verify run: a PDF used
    by many claims was re-parsed once per claim. Errors are cached too."""
    if cache is None:
        return read_source(source_ref, reader, root)
    key = (source_ref, reader)
    if key not in cache:
        try:
            cache[key] = ("ok", read_source(source_ref, reader, root))
        except Exception as exc:  # noqa: BLE001 - re-raised below, for every claim
            cache[key] = ("err", exc)
    kind, value = cache[key]
    if kind == "err":
        raise value
    return value


def resolve(selector: dict, source_ref: str, reader: str, root, cache=None) -> tuple:
    """(outcome, detail). Never raises for an unreadable or undecodable source.
    `cache`: a dict shared across one run, so each source is read once."""
    try:
        raw, text = _read_cached(source_ref, reader, root, cache)
    except SourceUnavailable as exc:
        mount = unmounted_volume(source_ref, root)
        if mount:
            return "gone", f"{UNMOUNTED}: {mount}"
        return "gone", str(exc)
    except SourceUndecodable as exc:
        return "changed", str(exc)
    except Exception as exc:  # noqa: BLE001 - a reader failure must not abort a verify run
        return "changed", f"reader failed: {type(exc).__name__}: {exc}"
    quote = selector["TextQuoteSelector"]
    exact, prefix, suffix = quote["exact"], quote["prefix"], quote["suffix"]
    if hashlib.sha256(raw).hexdigest() == selector["state"]["sha256"]:
        return "intact", None
    frag = (selector.get("FragmentSelector") or {}).get("value", "")
    was = int(frag[len("page="):]) if reader == PDF_READER and frag.startswith("page=") else None

    def where(pos, how=""):
        if was is None:
            return f"at {pos}{how}"
        now = page_of(text, pos)
        return f"page {was}" + (f" -> page {now}" if now != was else "") + how

    hits = _occurrences(text, exact)
    if len(hits) == 1:
        return "moved", where(hits[0])
    if len(hits) > 1:
        ctx = [h for h in hits
               if text[max(0, h - len(prefix)):h] == prefix
               and text[h + len(exact):h + len(exact) + len(suffix)] == suffix]
        if len(ctx) == 1:
            return "moved", where(ctx[0], " (by context)")
        return "ambiguous", f"{len(hits)} matches, {len(ctx)} with the recorded context"
    if was is not None:
        # Per page: `_norm` would turn the form feed between pages into a space and
        # let a quote match across a page break (deferred minor 2026-11-15f).
        needle = _norm(exact)
        norm_hits = [n for n, page in enumerate(text.split(PAGE_BREAK), 1)
                     for _ in _occurrences(_norm(page), needle)]
        if len(norm_hits) == 1:
            now = norm_hits[0]
            return "moved_normalised", (f"page {was}" + (f" -> page {now}" if now != was else "")
                                        + ", found after whitespace normalisation")
    else:
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
