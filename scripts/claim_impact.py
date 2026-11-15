"""What is built FROM a claim (IOU-Z2-3; design 2026-11-15-z2-iou-1-4 §1). Read-only.

Syntheses (`search/context --save`), evidence packs and reports are point-in-time
snapshots of what was retrieved. When a claim they cite stops being current they are
FLAGGED, never rewritten: rewriting one would falsify the record of what it said.

An artifact cites a claim by id (a synthesis's `claims:` line, written since
2026-11-15) or by the claim page's path (older syntheses' `sources:`, an evidence
item's `source_path`, a report's claims and citations).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import knowledge_scope as ks


def _config_dir() -> Path:
    return Path(os.environ.get("ZURVAN_CONFIG_DIR", os.path.expanduser("~/.zurvan")))


def _front(text: str) -> dict:
    """Flat `key: value` frontmatter, as the other flat parsers read it."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    out = {}
    for line in text[4:end if end != -1 else 0].splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _csv(value: str) -> set:
    return {p.strip() for p in (value or "").split(",") if p.strip()}


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _syntheses(root: Path):
    d = root / "wiki" / "syntheses"
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        try:
            fm = _front(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        yield {"kind": "synthesis", "path": str(p), "ids": _csv(fm.get("claims")),
               "paths": {_norm(x) for x in _csv(fm.get("sources"))}}


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _foreign_projects(cfg: Path, base: Path) -> set:
    """Registered project names whose store is NOT `base`.

    Evidence packs and reports live in the shared config dir and name each item's
    project. A path match from another registered store is not a citation of this
    store's claim. An unregistered name cannot be told apart, so it is kept:
    flagging a non-citation is cheap, missing a citation of a retracted claim is not.
    """
    reg = _json(cfg / "projects.json")
    projects = reg.get("projects") if isinstance(reg, dict) else None
    out = set()
    for name, data in (projects or {}).items():
        try:
            if Path(data["path"]).resolve() != base.resolve():
                out.add(name)
        except (KeyError, TypeError, OSError):
            continue
    return out


def _paths(rows, foreign, *keys) -> set:
    return {_norm(next((r.get(k) for k in keys if r.get(k)), "")) for r in rows
            if isinstance(r, dict) and r.get("project") not in foreign} - {""}


def _packs(cfg: Path, foreign: set):
    d = cfg / "evidence-packs"
    for p in sorted(d.glob("*/items.json")) if d.is_dir() else []:
        items = _json(p)
        if isinstance(items, list):
            yield {"kind": "evidence_pack", "path": str(p.parent), "ids": set(),
                   "paths": _paths(items, foreign, "source_path")}


def _reports(cfg: Path, foreign: set):
    d = cfg / "reports"
    for p in sorted(d.glob("*/report.json")) if d.is_dir() else []:
        rep = _json(p)
        if isinstance(rep, dict):
            rows = (rep.get("claims") or []) + (rep.get("citations") or [])
            yield {"kind": "report", "path": str(p.parent), "ids": set(),
                   "paths": _paths(rows, foreign, "source_path", "path")}


def derived_artifacts(root=None):
    base = ks.store_root(root)
    cfg = _config_dir()
    foreign = _foreign_projects(cfg, base)
    yield from _syntheses(base)
    yield from _packs(cfg, foreign)
    yield from _reports(cfg, foreign)


def _page_index(root) -> dict:
    """claim_id -> (page path relative to the store, ClaimState)."""
    from scripts import claim_ledger as cl
    from scripts import claim_pages as cp
    base = ks.store_root(root)
    out = {}
    for cid, state in cl.current(root).items():
        try:
            rel = cp.page_path(state, root).relative_to(base).as_posix()
        except ValueError:
            rel = None
        out[cid] = (rel, state)
    return out


def _cites(art, cid, rel) -> bool:
    return cid in art["ids"] or (rel is not None and rel in art["paths"])


def impact(claim_id, root=None) -> list:
    """Every derived artifact that cites `claim_id`."""
    rel = _page_index(root).get(claim_id, (None, None))[0]
    return [{"kind": a["kind"], "path": a["path"]} for a in derived_artifacts(root)
            if _cites(a, claim_id, rel)]


def stale_derived(root=None) -> list:
    """Every (artifact, claim) pair where the claim is no longer current."""
    from scripts import claim_ledger as cl
    pages = _page_index(root)
    ended = {cid: v for cid, v in pages.items() if v[1].status not in cl.CURRENT_STATUSES}
    out = []
    for art in derived_artifacts(root):
        for cid, (rel, state) in sorted(ended.items()):
            if _cites(art, cid, rel):
                out.append({"claim_id": cid, "status": state.status, "kind": art["kind"],
                            "path": art["path"]})
    return out


def claim_ids_for_paths(paths, root=None) -> list:
    """The claim ids whose pages are among `paths` (absolute or store-relative)."""
    base = ks.store_root(root)
    wanted = set()
    for p in paths:
        p = Path(p)
        try:
            wanted.add((p.resolve().relative_to(base.resolve()) if p.is_absolute() else p).as_posix())
        except ValueError:
            continue
    if not any(w.startswith("wiki/claims/") for w in wanted):
        return []
    return sorted(cid for cid, (rel, _) in _page_index(root).items() if rel in wanted)
