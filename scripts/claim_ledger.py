#!/usr/bin/env python3
"""Z2 claim ledger: writes (add, supersede, retract, revise) and the bitemporal
views (current, as-of revision, as-of date, valid-at, history). Spec §7.1-7.3.

Visibility. A revision row is part of knowledge only once the knowledge
operation that wrote it committed (or was reconciled): `kr.belief_revisions()`
maps those mutations to the revision at which they became knowledge. Rows of
ACTIVE, ABORTED or ORPHANED mutations are invisible to every read, except that
an operation sees its own rows while it runs. The claims transaction commits
before the knowledge operation does, so "highest rev" alone would show an
aborted write as current.

Every write runs inside a knowledge operation, holds the claims lock until that
operation ends, and plans/completes `data/claims.sqlite` through the ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from scripts import claim_evidence as ce
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

CURRENT_STATUSES = ("asserted", "supported", "contested", "unknown")


@dataclass
class ClaimState:
    claim_id: str
    rev: int
    status: str
    text: str | None
    confidence: str | None
    basis: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    valid_from: str | None = None
    valid_until: str | None = None
    evidence: list = field(default_factory=list)
    legacy_id: str | None = None
    scope_project: str | None = None
    claim_type: str | None = None
    mutation_id: str | None = None


@dataclass
class BeliefState:
    revision: int | None
    pre_ledger: bool
    claims: dict


# ----------------------------------------------------------------- validation

def _check_vocab(confidence=None, basis=None, method=None):
    if confidence is not None and confidence not in cs.CONFIDENCE:
        raise ValueError(f"confidence must be one of {cs.CONFIDENCE}, got {confidence!r}")
    for b in basis or ():
        if b not in cs.BASIS:
            raise ValueError(f"confidence basis {b!r} is not in {cs.BASIS}")
    if method is not None and method not in cs.METHODS:
        raise ValueError(f"method must be one of {cs.METHODS}, got {method!r}")


def _require_reason(reason):
    if reason is None or not str(reason).strip():
        raise cs.ClaimRefused("REASON_REQUIRED", "supersede, retract and revise need a reason")


# ----------------------------------------------------------------- write session

class _Session:
    """One claims transaction inside the active knowledge operation."""

    def __init__(self, root, migrating=False):
        root = kr.ks.store_root(root)        # None -> ZURVAN_ROOT / repo root, once
        self.root = root
        kr.require_active_operation("a claim write", root)
        if not migrating:
            cs.require_migrated(root)
        cs.acquire_for_operation(root)
        self.path = cs.db_path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ticket = None
        self.mutation_id = None
        # Never CREATE the store before the write is planned: a file that appears
        # outside a planned write is an unmanaged edit, and the next operation
        # would mint an EXTERNAL_OBSERVED revision for it (found by
        # tests/test_mcp_bridge.py on a fresh store).
        self.conn = cs.connect(root) if self.path.exists() else None

    def plan(self) -> str:
        """Record the intent to write - only after every refusal check has passed,
        so a refused write leaves no planned path and mints no revision."""
        if self.ticket is None:
            self.ticket = kr.plan_write(self.path, self.root)
            self.mutation_id = kr.active_operation().mutation_id
            if self.conn is None:
                self.conn = cs.connect(self.root)
                self.conn.execute("BEGIN IMMEDIATE")
        return self.mutation_id

    def latest(self, claim_id):
        """The claim's latest visible revision row, or None (also on a fresh store)."""
        if self.conn is None:
            return None
        self.conn.row_factory = cs.sqlite3.Row
        try:
            return _latest_row(self.conn, claim_id, _visible_map(self.root))
        finally:
            self.conn.row_factory = None

    def __enter__(self):
        if self.conn is not None:
            self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.conn is None:
            return False
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()
        if exc_type is None and self.ticket is not None:
            kr.complete_write(self.ticket, self.path, self.root)
        return False


def _visible_map(root, include_own=True) -> dict:
    vis = kr.belief_revisions(root)
    op = kr.active_operation()
    if include_own and op is not None and op.mutation_id and op.root == kr.ks.store_root(root):
        vis = dict(vis)
        vis[op.mutation_id] = float("inf")          # own rows: newer than any revision
    return vis


def _latest_row(conn, claim_id, vis):
    rows = conn.execute("SELECT * FROM revisions WHERE claim_id = ? ORDER BY rev",
                        (claim_id,)).fetchall()
    rows = [r for r in rows if r["mutation_id"] in vis]
    return rows[-1] if rows else None


def _anchor(root, source, quote, reader):
    return ce.anchor(source, quote, reader, root, cs._now())


def _store_evidence(conn, source, a, reader, location_hint=None):
    return {"source_ref_cid": cs.put_content(conn, "source_ref", source),
            "selector_cid": cs.put_content(conn, "selector",
                                           json.dumps(a["selector"], sort_keys=True)),
            "capsule_cid": cs.put_content(conn, "capsule", a["capsule"]),
            "location_hint_cid": (cs.put_content(conn, "location_hint", location_hint)
                                  if location_hint else None),
            "reader": reader}


def _carry(prev) -> dict:
    """The fields a new revision inherits from the previous one."""
    keys = ("text_cid", "confidence", "confidence_basis", "valid_from", "valid_until",
            "valid_time_basis", "tags_cid", "claim_type", "extraction_key_cid",
            "subject_id", "predicate_id", "object_json")
    return {k: prev[k] for k in keys}


def _next(s, claim_id, *, event, status, actor, method, reason, expected_rev, **changes):
    prev = s.latest(claim_id)
    if prev is None:
        raise cs.ClaimRefused("UNKNOWN_CLAIM", claim_id)
    if expected_rev is not None and int(expected_rev) != prev["rev"]:
        raise cs.ClaimRefused("CLAIM_CONFLICT",
                              f"{claim_id} is at rev {prev['rev']}, expected {expected_rev}")
    s.plan()
    row = _carry(prev)
    row.update({k: v for k, v in changes.items() if v is not None})
    if "text" in row:
        row["text_cid"] = cs.put_content(s.conn, "text", row.pop("text"))
    if "tags" in row:
        row["tags_cid"] = cs.put_content(s.conn, "tags", json.dumps(list(row.pop("tags"))))
    if "anchored" in row:
        src, a, rdr = row.pop("anchored")
        row["evidence"] = [_store_evidence(s.conn, src, a, rdr)]
    row.update(claim_id=claim_id, event=event, status=status, actor=actor, method=method,
               mutation_id=s.mutation_id,
               reason_cid=cs.put_content(s.conn, "reason", reason) if reason else None)
    if row.get("valid_from") and changes.get("valid_from"):
        row["valid_time_basis"] = "stated"
    return cs.append_revision(s.conn, row)


# ----------------------------------------------------------------- pages

def _refuse_if_edited(claim_id, root):
    from scripts import claim_pages
    if claim_pages.has_unadopted_edit(claim_id, root):
        raise cs.ClaimRefused("CLAIM_CONFLICT",
                              f"{claim_id}: its page has unadopted edits "
                              "(`zurvan claim adopt` or `claim regenerate --force`)")


def _write_pages(root, *claim_ids):
    from scripts import claim_pages
    for cid in claim_ids:
        claim_pages.write_page(cid, root, force=True)


# ----------------------------------------------------------------- writes

def add(text, source, evidence, *, confidence, basis=(), tags=(), valid_from=None,
        valid_until=None, project=None, actor, method, reader="utf8", claim_type=None,
        extraction_key=None, legacy_id=None, observed_at_legacy=None,
        observed_at_legacy_source=None, location_hint=None, status="asserted",
        extra_evidence=(), root=None, _migrating=False) -> str:
    _check_vocab(confidence, basis, method)
    with _Session(root, _migrating) as s:
        claim_id = _add_in(s, text, source, evidence, confidence=confidence, basis=basis,
                       tags=tags, valid_from=valid_from, valid_until=valid_until,
                       project=project, actor=actor, method=method, reader=reader,
                       claim_type=claim_type, extraction_key=extraction_key,
                       legacy_id=legacy_id, observed_at_legacy=observed_at_legacy,
                       observed_at_legacy_source=observed_at_legacy_source,
                       location_hint=location_hint, status=status,
                       extra_evidence=extra_evidence)
    _write_pages(root, claim_id)
    return claim_id


def _add_in(s, text, source, evidence, *, confidence, basis, tags, valid_from, valid_until,
            project, actor, method, reader, claim_type, extraction_key, legacy_id,
            observed_at_legacy, observed_at_legacy_source, location_hint, status,
            relations=None, extra_evidence=()) -> str:
    quotes = ([(evidence, location_hint)] if evidence is not None else []) + list(extra_evidence)
    anchored = [(_anchor(s.root, source, q, reader), loc) for q, loc in quotes]
    s.plan()
    ev_rows = [_store_evidence(s.conn, source, a, reader, loc) for a, loc in anchored]
    claim_id = cs.new_claim_id()
    cs.insert_claim(s.conn, claim_id, s.mutation_id, legacy_id=legacy_id, scope_project=project)
    cs.append_revision(s.conn, {
        "claim_id": claim_id, "event": "assert", "status": status,
        "text_cid": cs.put_content(s.conn, "text", text), "confidence": confidence,
        "confidence_basis": list(basis), "valid_from": valid_from, "valid_until": valid_until,
        "valid_time_basis": "stated" if valid_from else "unknown", "actor": actor,
        "method": method, "mutation_id": s.mutation_id, "tags": list(tags or ()),
        "claim_type": claim_type, "extraction_key": extraction_key,
        "observed_at_legacy": observed_at_legacy,
        "observed_at_legacy_source": observed_at_legacy_source,
        "evidence": ev_rows, "relations": relations or []})
    return claim_id


def supersede(old_id, *, text, source, evidence, reason, actor, method, valid_from=None,
              expected_rev=None, confidence="high", basis=(), tags=None, reader="utf8",
              root=None) -> str:
    _require_reason(reason)
    _check_vocab(confidence, basis, method)
    _refuse_if_edited(old_id, root)
    with _Session(root) as s:
        prev = s.latest(old_id)
        if prev is None:
            raise cs.ClaimRefused("UNKNOWN_CLAIM", old_id)
        if expected_rev is not None and int(expected_rev) != prev["rev"]:
            raise cs.ClaimRefused("CLAIM_CONFLICT",
                                  f"{old_id} is at rev {prev['rev']}, expected {expected_rev}")
        if tags is None:
            tags = json.loads(cs.get_content(s.conn, prev["tags_cid"]) or "[]")
        new_id = _add_in(s, text, source, evidence, confidence=confidence, basis=basis,
                         tags=tags, valid_from=valid_from, valid_until=None, project=None,
                         actor=actor, method=method, reader=reader, claim_type=None,
                         extraction_key=None, legacy_id=None, observed_at_legacy=None,
                         observed_at_legacy_source=None, location_hint=None,
                         status="asserted")
        s.conn.execute("UPDATE claims SET scope_project = (SELECT scope_project FROM claims"
                       " WHERE claim_id = ?) WHERE claim_id = ?", (old_id, new_id))
        _next(s, old_id, event="supersede", status="superseded", actor=actor, method=method,
              reason=reason, expected_rev=None, valid_until=valid_from,
              relations=[{"from_claim": new_id, "to_claim": old_id, "kind": "supersedes"}])
    _write_pages(root, new_id, old_id)
    return new_id


def retract(claim_id, *, reason, basis, actor, method, expected_rev=None, root=None) -> int:
    _require_reason(reason)
    _check_vocab(None, basis, method)
    _refuse_if_edited(claim_id, root)
    with _Session(root) as s:
        rev = _next(s, claim_id, event="retract", status="retracted", actor=actor,
                    method=method, reason=reason, expected_rev=expected_rev,
                    confidence_basis=json.dumps(list(basis)) if basis else None)["rev"]
    _write_pages(root, claim_id)
    return rev


def revise(claim_id, *, reason, actor, method, expected_rev=None, text=None, confidence=None,
           basis=None, tags=None, valid_from=None, valid_until=None, source=None,
           evidence=None, reader="utf8", root=None, _adopting=False) -> int:
    _require_reason(reason)
    _check_vocab(confidence, basis, method)
    if not _adopting:
        _refuse_if_edited(claim_id, root)
    with _Session(root) as s:
        changes = {"confidence": confidence, "valid_from": valid_from,
                   "valid_until": valid_until, "text": text, "tags": tags,
                   "confidence_basis": json.dumps(list(basis)) if basis is not None else None}
        if evidence is not None:
            changes["anchored"] = (source, _anchor(s.root, source, evidence, reader), reader)
        status = s.latest(claim_id)
        if status is None:
            raise cs.ClaimRefused("UNKNOWN_CLAIM", claim_id)
        new_status = status["status"]
        if new_status == "orphaned":
            # Anti-goal (spec §4): an orphaned claim returns only on evidence that
            # re-resolves, never by assertion.
            if evidence is None:
                raise cs.ClaimRefused("RESTORE_REFUSED",
                                      f"{claim_id} is orphaned; attach new evidence "
                                      "(--source/--evidence) or let `claim verify --apply` "
                                      "restore it when its evidence resolves again")
            new_status = "asserted"
        rev = _next(s, claim_id, event="revise", status=new_status, actor=actor,
                    method=method, reason=reason, expected_rev=expected_rev,
                    **changes)["rev"]
    _write_pages(root, claim_id)
    return rev


# ----------------------------------------------------------------- views

def find_by_extraction_key(key: str, root=None):
    """The current state of the claim an extraction created under `key`, or None."""
    conn = _connect_ro(root)
    if conn is None:
        return None
    try:
        ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT r.claim_id FROM revisions r JOIN content c"
            " ON c.cid = r.extraction_key_cid WHERE c.body = ?", (key,))}
    finally:
        conn.close()
    now = current(root)
    found = [now[i] for i in sorted(ids) if i in now]
    return found[0] if found else None


def _connect_ro(root):
    try:
        conn = cs.connect(root, readonly=True)
    except cs.sqlite3.OperationalError:
        return None
    conn.row_factory = cs.sqlite3.Row
    return conn


def _state(conn, row, rows_by_claim) -> ClaimState:
    claim = conn.execute("SELECT legacy_id, scope_project FROM claims WHERE claim_id = ?",
                         (row["claim_id"],)).fetchone()
    evidence = []
    for r in reversed(rows_by_claim):                 # newest visible rev <= row with evidence
        if r["rev"] > row["rev"]:
            continue
        ev = conn.execute("SELECT * FROM evidence WHERE claim_id = ? AND rev = ?"
                          " ORDER BY evidence_id", (r["claim_id"], r["rev"])).fetchall()
        if ev:
            for e in ev:
                sel = cs.get_content(conn, e["selector_cid"])
                evidence.append({"source": cs.get_content(conn, e["source_ref_cid"]),
                                 "selector": json.loads(sel) if sel else None,
                                 "capsule": cs.get_content(conn, e["capsule_cid"]),
                                 "location_hint": cs.get_content(conn, e["location_hint_cid"]),
                                 "reader": e["reader"]})
            break
    tags = cs.get_content(conn, row["tags_cid"])
    return ClaimState(claim_id=row["claim_id"], rev=row["rev"], status=row["status"],
                      text=cs.get_content(conn, row["text_cid"]),
                      confidence=row["confidence"],
                      basis=json.loads(row["confidence_basis"] or "[]"),
                      tags=json.loads(tags) if tags else [],
                      valid_from=row["valid_from"], valid_until=row["valid_until"],
                      evidence=evidence, legacy_id=claim["legacy_id"],
                      scope_project=claim["scope_project"], claim_type=row["claim_type"],
                      mutation_id=row["mutation_id"])


def _belief(root, vis, upto) -> dict:
    conn = _connect_ro(root)
    if conn is None:
        return {}
    try:
        by_claim = {}
        for r in conn.execute("SELECT * FROM revisions ORDER BY claim_id, rev"):
            b = vis.get(r["mutation_id"])
            if b is not None and b <= upto:
                by_claim.setdefault(r["claim_id"], []).append(r)
        out = {}
        for claim_id, rows in by_claim.items():
            best = max(rows, key=lambda r: (vis[r["mutation_id"]], r["rev"]))
            out[claim_id] = _state(conn, best, rows)
        return out
    finally:
        conn.close()


def current(root=None) -> dict:
    return _belief(root, _visible_map(root), float("inf"))


def _first_belief(root, vis):
    conn = _connect_ro(root)
    if conn is None:
        return None
    try:
        revs = [vis[m] for (m,) in conn.execute("SELECT DISTINCT mutation_id FROM revisions")
                if m in vis]
    finally:
        conn.close()
    return min(revs) if revs else None


def as_of_revision(R: int, root=None) -> BeliefState:
    vis = kr.belief_revisions(root)
    first = _first_belief(root, vis)
    if first is None or R < first:
        return BeliefState(revision=R, pre_ledger=True, claims={})
    return BeliefState(revision=R, pre_ledger=False, claims=_belief(root, vis, R))


def as_of_date(iso: str, root=None) -> BeliefState:
    R = kr.revision_at(iso, root)
    if R is None:
        return BeliefState(revision=None, pre_ledger=True, claims={})
    return as_of_revision(R, root)


def valid_at(state: dict, iso: str) -> dict:
    """Claims true at `iso` in the world. A claim with unknown valid time is kept:
    absence of a stated interval is not evidence that it was false."""
    out = {}
    for claim_id, s in state.items():
        if s.valid_from is not None and iso < s.valid_from:
            continue
        if s.valid_until is not None and iso >= s.valid_until:
            continue
        out[claim_id] = s
    return out


def history(claim_id, root=None) -> list:
    vis = _visible_map(root)
    conn = _connect_ro(root)
    if conn is None:
        return []
    try:
        out = []
        for r in conn.execute("SELECT * FROM revisions WHERE claim_id = ? ORDER BY rev",
                              (claim_id,)):
            if r["mutation_id"] not in vis:
                continue
            b = vis[r["mutation_id"]]
            out.append({"rev": r["rev"], "event": r["event"], "status": r["status"],
                        "actor": r["actor"], "method": r["method"],
                        "reason": cs.get_content(conn, r["reason_cid"]),
                        "recorded_at": r["recorded_at"],
                        "belief_revision": None if b == float("inf") else b,
                        "mutation_id": r["mutation_id"]})
        return out
    finally:
        conn.close()


# ----------------------------------------------------------------- erasure (spec §7.7)

ERASE_REASONS = ("client_request", "personal_data", "legal", "owner_request")
ERASE_BOUND = ("erasure removes the claim from the live store and its derived views; copies "
               "outside the live store are listed, not guaranteed removed; deleted "
               "rollback-journal and freed filesystem blocks are not overwritten.")


def _claim_cids(conn, claim_id) -> list:
    cids = []
    for row in conn.execute("SELECT text_cid, reason_cid, tags_cid, extraction_key_cid"
                            " FROM revisions WHERE claim_id = ?", (claim_id,)):
        cids += [c for c in row if c]
    for row in conn.execute("SELECT source_ref_cid, selector_cid, capsule_cid,"
                            " location_hint_cid FROM evidence WHERE claim_id = ?", (claim_id,)):
        cids += [c for c in row if c]
    return sorted(set(cids))


def _stub_legacy(legacy_dir, legacy_id, reason_class, now) -> dict | None:
    import os
    import stat as _stat
    from pathlib import Path
    d = Path(legacy_dir)
    target = d / f"{legacy_id}.md"
    if not target.exists():
        return None
    dir_mode = _stat.S_IMODE(d.stat().st_mode)
    os.chmod(d, dir_mode | 0o200)
    try:
        for p in (target, d / "MANIFEST.json"):
            if p.exists():
                os.chmod(p, 0o644)
        target.write_text(f"# Claim\n(erased {now[:10]}: {reason_class})\n", encoding="utf-8")
        os.chmod(target, 0o444)
        manifest_path = d / "MANIFEST.json"
        manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest_path.exists() else {"files": {}})
        manifest.setdefault("erased", {})[target.name] = {"at": now, "reason_class": reason_class}
        manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
        os.chmod(manifest_path, 0o444)
    finally:
        os.chmod(d, dir_mode)
    return {"kind": "legacy_copy", "where": str(target), "action": "stubbed"}


def erase(claim_id, *, reason_class, actor, root=None, legacy_dir=None,
          method="human") -> dict:
    """Blank every body the claim references, append an `erase` revision, rewrite
    its page (and pages that quote it) as stubs, VACUUM, and REPORT the copies
    this cannot reach. The hash chain stays valid: it never covered bodies."""
    import glob as _glob
    from pathlib import Path
    if reason_class not in ERASE_REASONS:
        raise ValueError(f"reason_class must be one of {ERASE_REASONS}")
    base = kr.ks.store_root(root)
    now = cs._now()
    with _Session(base) as s:
        prev = s.latest(claim_id)
        if prev is None:
            raise cs.ClaimRefused("UNKNOWN_CLAIM", claim_id)
        s.plan()
        text_before = cs.get_content(s.conn, prev["text_cid"]) or ""
        legacy_id = s.conn.execute("SELECT legacy_id FROM claims WHERE claim_id = ?",
                                   (claim_id,)).fetchone()[0]
        related = sorted({r for pair in s.conn.execute(
            "SELECT from_claim, to_claim FROM relations WHERE from_claim = ? OR to_claim = ?",
            (claim_id, claim_id)) for r in pair} - {claim_id})
        cids = _claim_cids(s.conn, claim_id)
        s.conn.execute(f"UPDATE content SET body = NULL, erased_at = ?, erase_reason_class = ?"
                       f" WHERE cid IN ({','.join('?' * len(cids))})", [now, reason_class, *cids])
        rev = _next(s, claim_id, event="erase", status="erased", actor=actor, method=method,
                    reason=f"erased: {reason_class}", expected_rev=None)["rev"]
    conn = cs.connect(base)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    _write_pages(base, claim_id, *[r for r in related if r in current(base)])

    residue = []
    search = base / "data" / "search.sqlite"
    if search.exists():
        residue.append({"kind": "search_index", "where": str(search),
                        "action": "rebuild with `zurvan index search` to drop the old page"})
    for snap in sorted((base / "data" / "snapshots").glob("*")) if (base / "data" / "snapshots").is_dir() else []:
        if (snap / "claims.sqlite").exists() or any(snap.rglob("wiki/claims/*.md")):
            residue.append({"kind": "snapshot", "where": str(snap),
                            "action": "held until its lease expires; `zurvan knowledge gc`"})
    if legacy_id:
        dirs = [legacy_dir] if legacy_dir else sorted(
            _glob.glob(str(Path.home() / ".zurvan" / "z2" / "claims-legacy-*")))
        for d in dirs:
            stub = _stub_legacy(d, legacy_id, reason_class, now)
            if stub:
                residue.append(stub)
    if text_before:
        for f in sorted((base / "data" / "extractions").glob("*.json")) if (base / "data" / "extractions").is_dir() else []:
            try:
                if text_before in f.read_text(encoding="utf-8", errors="replace"):
                    residue.append({"kind": "extraction_json", "where": str(f),
                                    "action": "raw model output; delete by hand if required"})
            except OSError:
                pass
    residue.append({"kind": "time_machine", "where": "backups",
                    "action": "out of Zurvan's reach"})
    return {"rev": rev, "residue": residue, "bound": ERASE_BOUND}


# ----------------------------------------------------------------- verify (spec §7.4)

VERIFY_DISCLAIMER = "evidence integrity measures anchoring, not truth"
_ANCHORED = ("intact", "moved", "moved_normalised")


def _outcome_class(outcomes) -> str:
    if any(o in _ANCHORED for o in outcomes):
        return "anchored"
    if outcomes and all(o in ("gone", "changed") for o in outcomes):
        return "broken"
    return "ambiguous"


def _status_before_orphan(claim_id, root) -> str:
    for h in reversed(history(claim_id, root)):
        if h["status"] not in ("orphaned",):
            return h["status"]
    return "asserted"


def verify(claim_ids=None, *, apply: bool = False, root=None) -> dict:
    """Re-resolve every claim's evidence. Read-only unless `apply`: then an
    `orphan` revision when ALL evidence is gone/changed, a `restore` when an
    orphaned claim's evidence is anchored again - each only when the outcome
    class changed, never for `ambiguous`. Manual only; nothing schedules it."""
    base = kr.ks.store_root(root)
    now = current(base)
    targets = sorted(claim_ids) if claim_ids else sorted(now)
    counts = {o: 0 for o in ce.OUTCOMES}
    per_claim, written, skipped = {}, [], []
    for cid in targets:
        state = now.get(cid)
        if state is None or state.status == "erased":
            continue
        outcomes = []
        for ev in state.evidence:
            if not ev.get("selector") or not ev.get("source"):
                continue
            outcome, _ = ce.resolve(ev["selector"], ev["source"], ev.get("reader", "utf8"), base)
            outcomes.append(outcome)
            counts[outcome] += 1
        if not outcomes:
            continue
        per_claim[cid] = outcomes
        if not apply:
            continue
        cls = _outcome_class(outcomes)
        if cls in ("broken", "anchored"):
            from scripts import claim_pages
            if claim_pages.has_unadopted_edit(cid, base):
                skipped.append(cid)          # never regenerate over a hand edit
                continue
        if cls == "broken" and state.status in CURRENT_STATUSES:
            with _Session(base) as s:
                r = _next(s, cid, event="orphan", status="orphaned", actor="verifier",
                          method="verifier", reason="evidence " + ", ".join(outcomes),
                          expected_rev=None)["rev"]
            _write_pages(base, cid)
            written.append((cid, r, "orphan"))
        elif cls == "anchored" and state.status == "orphaned":
            restored = _status_before_orphan(cid, base)
            with _Session(base) as s:
                r = _next(s, cid, event="restore", status=restored, actor="verifier",
                          method="verifier", reason="evidence " + ", ".join(outcomes),
                          expected_rev=None)["rev"]
            _write_pages(base, cid)
            written.append((cid, r, "restore"))
    total = sum(counts.values())
    anchored = sum(counts[o] for o in _ANCHORED)
    return {"counts": counts, "integrity_rate": (anchored / total) if total else None,
            "claims": per_claim, "written": written, "skipped_edited": skipped,
            "disclaimer": VERIFY_DISCLAIMER}
