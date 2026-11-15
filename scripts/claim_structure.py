"""Reviewed, opt-in S-P-O backfill (IOU-Z2-1; design 2026-11-15-z2-iou-1-4 §3).

    zurvan claim structure --propose --out proposals.jsonl [--limit N]
        asks the configured model for a triple per current claim that has none and
        writes ONLY the proposals file (every entry "approved": false). Refuses the
        mock provider. Never touches the store.
    zurvan claim structure --apply proposals.jsonl
        writes one `revise` (method model_extraction) per entry a human marked
        "approved": true, all inside the caller's one operation; every entry is
        validated before anything is written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REASON = "S-P-O backfill (IOU-Z2-1), reviewed proposal"

PROMPT = """You turn one factual claim into a single subject-predicate-object triple.
Return ONLY a JSON object: {{"subject": <entity name>, "predicate": <one of the predicates
below>, "object": {{"entity": <name>}} or {{"value": <text>, "unit": <text, optional>}}
or {{"date": "YYYY-MM-DD"}}}}. Use "related_to" when no predicate fits.

Predicates:
{predicates}

Claim: {text}
"""


def _prompt(text: str) -> str:
    from scripts.claim_spo import predicates
    lines = "\n".join(f"- {k}: {v}" for k, v in predicates().items())
    return PROMPT.format(predicates=lines, text=text)


def _parse(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1]
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start:end + 1])


def provider_is_real() -> bool:
    return os.environ.get("ZURVAN_LLM_PROVIDER", "mock").lower() != "mock"


def boundary_gate(text: str):
    """None when `text` may leave the machine, else a refusal that names no span.

    Ruled 2026-11-16 (deferred minor of 2026-11-15f): a remote model provider is
    off the machine, so a claim is scanned as the PUBLIC_ARTIFACT sink, the sink
    PRIVATE and INSTRUMENT text may never reach. Opt-in like Boundary itself: with
    no built store nothing is scanned."""
    from scripts.boundary_guard import default_root
    from scripts.boundary import store as bstore
    root = default_root()
    if not bstore._is_built(root):
        return None
    from scripts import boundary
    verdict = boundary.scan(root, text, "PUBLIC_ARTIFACT")
    if verdict.blocked:
        classes = sorted({getattr(m, "source_class", "?") for m in verdict.matches})
        return f"BOUNDARY_BLOCKED: protected text ({', '.join(classes)}); not sent to the model"
    return None


def propose(out_path, *, call=None, limit=None, root=None, gate=boundary_gate) -> int:
    """Write proposals for current claims without a triple. Returns how many.
    Every claim passes `gate` before its text is sent to the model."""
    from scripts import claim_ledger as cl
    from scripts.claim_spo import normalise
    if call is None:
        from scripts.llm import run_llm as call
    todo = [s for s in sorted(cl.current(root).values(), key=lambda s: s.claim_id)
            if s.status in cl.CURRENT_STATUSES and not s.predicate and s.text]
    if limit:
        todo = todo[:limit]
    rows = []
    for s in todo:
        row = {"claim_id": s.claim_id, "rev": s.rev, "text": s.text, "approved": False}
        refused = gate(s.text) if gate else None
        if refused:
            row.update(subject=None, predicate=None, object=None, error=refused)
            rows.append(row)
            continue
        try:
            t = _parse(call(_prompt(s.text)))
            normalise(t.get("subject"), t.get("predicate"), t.get("object"))
            row.update(subject=t["subject"], predicate=t["predicate"], object=t["object"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            row.update(subject=None, predicate=None, object=None, error=str(exc))
        rows.append(row)
    Path(out_path).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


def apply(path, *, actor, root=None) -> list:
    """Revise every approved claim; all entries are checked first. Returns ids written."""
    from scripts import claim_ledger as cl
    from scripts.claim_spo import normalise
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    approved = [r for r in rows if r.get("approved") is True]
    current = cl.current(root)
    # Every check `revise` would make, for every entry, before the first write
    # (final review I1): a refusal halfway left pages rewritten and the store SUSPECT.
    from scripts import claim_pages as cp
    seen = set()
    for r in approved:
        cid = r.get("claim_id")
        if cid not in current:
            raise ValueError(f"unknown claim {cid!r}")
        if cid in seen:
            raise ValueError(f"{cid} is approved twice")
        seen.add(cid)
        st = current[cid]
        if r.get("rev") is None:
            raise ValueError(f"{cid}: the proposal has no rev; re-run --propose")
        if int(r["rev"]) != st.rev:
            raise ValueError(f"{cid} is at rev {st.rev}, the proposal was made at rev {r['rev']}; "
                             "re-run --propose")
        if st.status not in cl.CURRENT_STATUSES:
            raise ValueError(f"{cid} is {st.status}, not current")
        if cp.has_unadopted_edit(cid, root):
            raise ValueError(f"{cid}: its page has unadopted edits (`zurvan claim adopt` first)")
        normalise(r.get("subject"), r.get("predicate"), r.get("object"))
    written = []
    for r in approved:
        cl.revise(r["claim_id"], reason=REASON, actor=actor, method="model_extraction",
                  expected_rev=r.get("rev"), spo=(r["subject"], r["predicate"], r["object"]), root=root)
        written.append(r["claim_id"])
    return written
