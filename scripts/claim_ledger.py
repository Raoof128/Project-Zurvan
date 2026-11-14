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
        rev = _next(s, claim_id, event="revise", status=status["status"], actor=actor,
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
