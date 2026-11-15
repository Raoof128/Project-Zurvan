#!/usr/bin/env python3
"""Z2 migration: legacy `wiki/claims/*.md` pages into the claim ledger (spec §7.8).

Order, all inside the caller's ONE knowledge operation:
1. freeze the legacy pages (0444 files, 0555 dir, MANIFEST.json of sha256s)
   BEFORE anything is written, and verify the copy;
2. import every legacy page: parsed pages keep text, source and quote; their
   evidence is fingerprinted NOW (stated bound: proves what the source said at
   migration, not at the original assertion); `gone`/`changed` evidence is kept
   unanchored and the claim is orphaned with the outcome as reason; a quote
   that matches more than once is `ambiguous`, kept unanchored and asserted
   (ambiguous never orphans, as in `verify`); an empty
   quote or an unparseable page is imported as `unknown` - nothing is dropped;
3. pages are regenerated under their legacy filenames by the renderer;
4. a JSON report names every orphan, unparseable page and provenance mismatch.

The search index and graph are NOT rebuilt here: `cli.main` wraps a command in
one operation and a rebuild waits for live operations, so it would wait on
itself. The report says `derived_rebuild_required`; run the rebuilds after.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scripts import claim_evidence as ce
from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_scope as ks


@dataclass
class LegacyClaim:
    legacy_id: str
    text: str
    source: str | None
    quote: str
    confidence: str | None
    tags: list = field(default_factory=list)
    claim_type: str | None = None
    location: str | None = None
    extra: list = field(default_factory=list)     # [(quote, location)] for extraction pages
    raw: str = ""


def _front(text: str) -> tuple:
    """(flat dict, tags list, body). Line-based, like graph_build.parse_frontmatter."""
    if not text.startswith("---"):
        return {}, [], text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, [], text
    meta, tags, in_tags = {}, [], False
    for line in text[3:end].splitlines():
        if re.match(r"^tags:\s*$", line):
            in_tags = True
            continue
        m = re.match(r"^\s+-\s*(.*)$", line)
        if in_tags and m:
            tags.append(m.group(1).strip().strip('"').strip("'"))
            continue
        in_tags = False
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, tags, text[end + 4:].lstrip("\n")


def _confidence(value) -> str | None:
    v = (value or "").strip().strip('"').lower()
    return v if v in cs.CONFIDENCE else None


def parse_legacy_page(name: str, text: str) -> LegacyClaim | None:
    """Both legacy shapes: `memory.add_claim` pages and `extract.py` pages. None
    for an unparseable page or one that is already a generated (structured) page."""
    meta, tags, body = _front(text)
    if meta.get("authority") == "structured":
        return None
    legacy_id = name[:-3] if name.endswith(".md") else name
    m = re.search(r"^# Claim\n(.*?)\n\n## Evidence\n> ?(.*?)(?:\n\n\*\*Source\*\*|\Z)",
                  body, re.S | re.M)
    if m and m.group(1).strip():
        return LegacyClaim(legacy_id=legacy_id, text=m.group(1).strip(),
                           source=meta.get("source") or None, quote=m.group(2).strip(),
                           confidence=_confidence(meta.get("confidence")), tags=tags,
                           raw=text)
    m = re.search(r"^## Claim\n\n(.*?)\n\n## Evidence\n\n(.*)$", body, re.S | re.M)
    if m and m.group(1).strip():
        evs = re.findall(r'^> "?(.*?)"?\n\n(?:Location: (.*?)\n)?', m.group(2), re.M)
        if not evs:
            return None
        source = None
        if meta.get("source_file"):
            from scripts.ingest import source_page_stem
            source = f"wiki/sources/{source_page_stem(meta['source_file'])}.md"
        return LegacyClaim(legacy_id=legacy_id, text=m.group(1).strip(), source=source,
                           quote=evs[0][0], confidence=_confidence(meta.get("confidence")),
                           tags=tags, claim_type=meta.get("claim_type") or None,
                           location=evs[0][1] or None,
                           extra=[(q, loc or None) for q, loc in evs[1:]], raw=text)
    return None


def _log_times(root) -> dict:
    """legacy hex id -> local ISO time of its `Claim Added` line in wiki/log.md."""
    log = Path(root) / "wiki" / "log.md"
    out = {}
    if log.is_file():
        for m in re.finditer(r"^- \*\*([0-9T:.\-]+)\*\*: Claim Added - Claim ([0-9a-f]+) ",
                             log.read_text(encoding="utf-8", errors="replace"), re.M):
            out.setdefault(m.group(2), m.group(1))
    return out


def _outcome(root, lc: LegacyClaim) -> str:
    if not lc.quote:
        return "no_evidence"
    if not lc.source:
        return "gone"
    try:
        ce.anchor(lc.source, lc.quote, "utf8", root, cs._now())
        return "intact"
    except cs.ClaimRefused:
        pass
    try:
        ce.anchor_normalised(lc.source, lc.quote, "utf8", root, cs._now())
        return "moved_normalised"
    except cs.ClaimRefused as exc:
        if exc.code == "EVIDENCE_AMBIGUOUS":
            return "ambiguous"        # the quote is there, more than once: never orphan
        p = Path(lc.source)
        p = p if p.is_absolute() else Path(root) / p
        return "changed" if p.exists() else "gone"


def _freeze(pages, legacy_dir: Path) -> dict:
    if legacy_dir.exists():
        raise cs.ClaimRefused("MIGRATION_REFUSED", f"{legacy_dir} already exists; never overwritten")
    legacy_dir.mkdir(parents=True)
    manifest = {"files": {}}
    for p in pages:
        dst = legacy_dir / p.name
        shutil.copy2(p, dst)
        manifest["files"][p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    (legacy_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                              encoding="utf-8")
    for name, digest in manifest["files"].items():                       # verify the copy
        if hashlib.sha256((legacy_dir / name).read_bytes()).hexdigest() != digest:
            raise cs.ClaimRefused("MIGRATION_REFUSED", f"frozen copy of {name} does not verify")
    for f in legacy_dir.iterdir():
        os.chmod(f, 0o444)
    os.chmod(legacy_dir, 0o555)
    return manifest


def migrate(root=None, *, dry_run: bool = False, legacy_dir=None, report_path=None,
            now=None) -> dict:
    base = ks.store_root(root)
    stamp = (now or datetime.now(timezone.utc).isoformat())[:10].replace("-", "")
    z2 = Path.home() / ".zurvan" / "z2"
    legacy_dir = Path(legacy_dir) if legacy_dir else z2 / f"claims-legacy-{stamp}"
    report_path = Path(report_path) if report_path else z2 / f"migration-report-{stamp}.json"

    pages = sorted(p for p in (base / "wiki" / "claims").glob("*.md")
                   if not cs._is_structured_page(p))
    mtimes = {p.name: datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
              for p in pages}
    parsed, unparseable = [], []
    for p in pages:
        lc = parse_legacy_page(p.name, p.read_text(encoding="utf-8", errors="replace"))
        if lc is None:
            unparseable.append((p, p.read_text(encoding="utf-8", errors="replace")))
        else:
            parsed.append(lc)
    outcomes = {lc.legacy_id: _outcome(base, lc) for lc in parsed}
    counts = {"parsed": len(parsed), "unparseable": len(unparseable)}
    for o in ("intact", "moved_normalised", "ambiguous", "changed", "gone", "no_evidence"):
        counts[o] = sum(1 for v in outcomes.values() if v == o)
    report = {"counts": counts, "dry_run": dry_run,
              "orphaned": [{"legacy_id": k, "outcome": v,
                            "source": next(lc.source for lc in parsed if lc.legacy_id == k)}
                           for k, v in outcomes.items() if v in ("gone", "changed")],
              "unparseable": [p.name[:-3] for p, _ in unparseable],
              "no_evidence": [k for k, v in outcomes.items() if v == "no_evidence"],
              "ambiguous": [k for k, v in outcomes.items() if v == "ambiguous"],
              "derived_rebuild_required": not dry_run and bool(pages)}
    if dry_run or not pages:
        report.update(provenance_mismatches=[], legacy_manifest={}, observed_at_sources={})
        return report

    manifest = _freeze(pages, legacy_dir)
    log_times = _log_times(base)
    observed = {"log.md": 0, "mtime": 0}
    imported = {}
    common = dict(actor="migration", method="migration", basis=["legacy_unstated"], root=base,
                  _migrating=True)

    def when(legacy_id, name):
        hexid = legacy_id[len("claim-"):] if legacy_id.startswith("claim-") else None
        if hexid and hexid in log_times:
            observed["log.md"] += 1
            return log_times[hexid], "log.md"
        observed["mtime"] += 1
        return mtimes[name], "mtime"

    for lc in parsed:
        at, src = when(lc.legacy_id, lc.legacy_id + ".md")
        outcome = outcomes[lc.legacy_id]
        kw = dict(confidence=lc.confidence, tags=lc.tags, claim_type=lc.claim_type,
                  legacy_id=lc.legacy_id, observed_at_legacy=at, observed_at_legacy_source=src,
                  location_hint=lc.location, **common)
        if outcome == "intact":
            cid = cl.add(lc.text, lc.source, lc.quote, extra_evidence=lc.extra, **kw)
        elif outcome == "moved_normalised":
            cid = cl.add(lc.text, lc.source, lc.quote, extra_evidence=lc.extra,
                         _normalised=True, **kw)
        elif outcome == "ambiguous":
            # Kept asserted and unanchored: which occurrence the claim meant is
            # unknown, so no position or fingerprint is guessed. `verify` keeps
            # reporting it `ambiguous` until a longer quote is attached.
            cid = cl.add(lc.text, lc.source, lc.quote, extra_evidence=lc.extra,
                         _unanchored=True, **kw)
        elif outcome == "no_evidence":
            cid = cl.add(lc.text, lc.source, None, status="unknown", **kw)
        else:
            # A page may carry a quote and no source at all: the evidence is kept
            # with an empty source reference, never an invented one.
            cid = cl.add(lc.text, lc.source or "", lc.quote, extra_evidence=lc.extra,
                         _unanchored=True, **kw)
            cl._orphan(cid, reason=f"evidence {outcome} at migration", actor="migration",
                       method="migration", root=base)
        imported[lc.legacy_id] = cid
    for page, raw in unparseable:
        legacy_id = page.name[:-3]
        at, src = when(legacy_id, page.name)
        imported[legacy_id] = cl.add(raw, None, None, confidence=None, tags=[],
                                     legacy_id=legacy_id, observed_at_legacy=at,
                                     observed_at_legacy_source=src, status="unknown", **common)

    now_state = cl.current(base)
    mismatches = []
    for lc in parsed:
        s = now_state.get(imported[lc.legacy_id])
        quote = ((s.evidence[0].get("selector") or {}).get("TextQuoteSelector", {}).get("exact")
                 if s and s.evidence else "")
        source = s.evidence[0].get("source") if s and s.evidence else lc.source
        if s is None or s.text != lc.text or (lc.quote and (quote != lc.quote or (source or None) != lc.source)):
            mismatches.append(lc.legacy_id)
    report.update(provenance_mismatches=mismatches, legacy_manifest=manifest["files"],
                  legacy_dir=str(legacy_dir), observed_at_sources=observed,
                  imported=len(imported))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(report_path, 0o600)
    return report
