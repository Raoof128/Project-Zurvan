#!/usr/bin/env python3
"""`zurvan claim ...` (Z2 spec §7.3). Registered by `scripts/cli.py`.

Every subcommand runs inside the CLI's single knowledge operation
(`cli.main`). Read commands plan no write, so they mint no revision.
Exit codes: 0 ok, 1 refused (`Error: <CODE>: <detail>` on stderr), 2 usage.
"""

from __future__ import annotations

import getpass
import json
import sys
from dataclasses import asdict

WRITE_METHODS = ("human", "agent")

#: Final review 2026-11-14 (I3): derived views follow a claim change only when
#: rebuilt. Since IOU-Z2-6 (2026-11-15) search and context hide a claim that
#: stopped being current at query time; a new or restored claim, and the graph,
#: still wait for the rebuild.
STALE_HINT = ("note: search and context hide a claim that stopped being current at once; "
              "a new or restored claim, and the graph, follow after "
              "`zurvan index search` and `zurvan graph rebuild`")
_WRITES = ("add", "supersede", "retract", "revise", "erase", "adopt", "regenerate", "migrate")


def _default_actor() -> str:
    try:
        return f"local:{getpass.getuser()}"
    except Exception:  # noqa: BLE001
        return "local:unknown"


def register(subparsers) -> None:
    claim = subparsers.add_parser("claim", help="Manage claims (the Z2 claim ledger)")
    sub = claim.add_subparsers(dest="action")

    def who(p):
        p.add_argument("--actor", default=None, help="default local:<os user>")
        p.add_argument("--method", choices=WRITE_METHODS, default="human")

    a = sub.add_parser("add", help="Assert a new evidence-backed claim")
    a.add_argument("--text", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--evidence", required=True)
    a.add_argument("--confidence", required=True, choices=("low", "medium", "high"))
    a.add_argument("--basis", nargs="+", default=[])
    a.add_argument("--tags", nargs="+", default=[])
    a.add_argument("--valid-from", dest="valid_from")
    a.add_argument("--valid-until", dest="valid_until")
    a.add_argument("--project")
    who(a)

    s = sub.add_parser("supersede", help="Replace a claim with a newer one")
    s.add_argument("claim_id")
    s.add_argument("--text", required=True)
    s.add_argument("--source", required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--valid-from", dest="valid_from")
    s.add_argument("--confidence", default="high", choices=("low", "medium", "high"))
    s.add_argument("--basis", nargs="+", default=[])
    s.add_argument("--tags", nargs="+", default=None)
    s.add_argument("--expected-rev", dest="expected_rev", type=int)
    who(s)

    r = sub.add_parser("retract", help="Withdraw a claim (kept in history)")
    r.add_argument("claim_id")
    r.add_argument("--reason", required=True)
    r.add_argument("--basis", nargs="+", default=[])
    r.add_argument("--expected-rev", dest="expected_rev", type=int)
    who(r)

    v = sub.add_parser("revise", help="Correct metadata, wording or evidence")
    v.add_argument("claim_id")
    v.add_argument("--reason", required=True)
    v.add_argument("--text")
    v.add_argument("--confidence", choices=("low", "medium", "high"))
    v.add_argument("--basis", nargs="+")
    v.add_argument("--tags", nargs="+")
    v.add_argument("--valid-from", dest="valid_from")
    v.add_argument("--valid-until", dest="valid_until")
    v.add_argument("--source")
    v.add_argument("--evidence")
    v.add_argument("--expected-rev", dest="expected_rev", type=int)
    who(v)

    def when(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--as-of-revision", dest="as_of_revision", type=int)
        g.add_argument("--as-of-date", dest="as_of_date")
        p.add_argument("--valid-at", dest="valid_at")
        p.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", help="One claim, now or as of a revision/date")
    sh.add_argument("claim_id")
    when(sh)
    hi = sub.add_parser("history", help="Every revision of a claim")
    hi.add_argument("claim_id")
    hi.add_argument("--json", action="store_true")
    li = sub.add_parser("list", help="Claims, optionally by status / as of")
    li.add_argument("--status")
    when(li)

    ve = sub.add_parser("verify", help="Re-check every claim's evidence (intact/moved/.../gone)")
    ve.add_argument("claim_ids", nargs="*")
    ve.add_argument("--apply", action="store_true",
                    help="write orphan/restore revisions when the outcome class changed")
    sub.add_parser("conflicts", help="Claims whose page has unadopted hand edits")
    ad = sub.add_parser("adopt", help="Turn a hand-edited page into a revision")
    ad.add_argument("claim_id")
    ad.add_argument("--reason", required=True)
    ad.add_argument("--actor", default=None)
    er = sub.add_parser("erase", help="Erase a claim's content (client/personal data); history stays")
    er.add_argument("claim_id")
    er.add_argument("--reason-class", dest="reason_class", required=True,
                    choices=("client_request", "personal_data", "legal", "owner_request"))
    er.add_argument("--actor", default=None)
    mg = sub.add_parser("migrate", help="Import legacy claim pages into the ledger (once)")
    mg.add_argument("--dry-run", dest="dry_run", action="store_true")
    mg.add_argument("--legacy-dir", dest="legacy_dir")
    mg.add_argument("--report", dest="report_path")
    sub.add_parser("doctor", help="Chain, integrity, invisible rows, dangling cids, edited pages")
    im = sub.add_parser("impact", help="Derived artifacts (syntheses, packs, reports) citing a claim")
    im.add_argument("claim_id")
    im.add_argument("--json", action="store_true")
    sd = sub.add_parser("stale-derived", help="Derived artifacts citing a claim that is no longer current")
    sd.add_argument("--json", action="store_true")
    rg = sub.add_parser("regenerate", help="Rewrite pages from the ledger")
    rg.add_argument("claim_id", nargs="?")
    rg.add_argument("--all", action="store_true")
    rg.add_argument("--force", action="store_true", help="discard unadopted hand edits")


def _belief(args):
    from scripts import claim_ledger as cl
    if getattr(args, "as_of_revision", None) is not None:
        state = cl.as_of_revision(args.as_of_revision).claims
    elif getattr(args, "as_of_date", None):
        state = cl.as_of_date(args.as_of_date).claims
    else:
        state = cl.current()
    if getattr(args, "valid_at", None):
        state = cl.valid_at(state, args.valid_at)
    return state


def _print_state(s, as_json):
    if as_json:
        print(json.dumps(asdict(s), sort_keys=True, default=str))
        return
    print(f"{s.claim_id} rev {s.rev} [{s.status}] {s.confidence or '-'}: {s.text}")


def _dependents(claim_id) -> None:
    """IOU-Z2-3: say what was built from a claim that just stopped being current.
    Flagged, never rewritten (design 2026-11-15-z2-iou-1-4 §1)."""
    try:
        from scripts.claim_impact import impact
        n = len(impact(claim_id))
    except Exception:  # noqa: BLE001 - a notice must never fail the write
        return
    if n:
        word = "artifact cites" if n == 1 else "artifacts cite"
        print(f"note: {n} derived {word} {claim_id} (syntheses, evidence packs, reports are "
              f"snapshots and are not rewritten); list them: zurvan claim impact {claim_id}",
              file=sys.stderr)


def dispatch(args) -> int:
    from scripts import claim_ledger as cl
    from scripts import claim_pages as cp
    from scripts.claim_store import ClaimRefused

    action = args.action
    actor = getattr(args, "actor", None) or _default_actor()
    try:
        if action == "add":
            cid = cl.add(args.text, args.source, args.evidence, confidence=args.confidence,
                         basis=args.basis, tags=args.tags, valid_from=args.valid_from,
                         valid_until=args.valid_until, project=args.project, actor=actor,
                         method=args.method)
            from scripts.memory import append_claim_log
            append_claim_log(cid, args.source)
            state = cl.current().get(cid)
            print(f"Created {cp.page_path(state) if state else cid}")
            print(json.dumps({"claim_id": cid}))
        elif action == "supersede":
            new = cl.supersede(args.claim_id, text=args.text, source=args.source,
                               evidence=args.evidence, reason=args.reason, actor=actor,
                               method=args.method, valid_from=args.valid_from,
                               expected_rev=args.expected_rev, confidence=args.confidence,
                               basis=args.basis, tags=args.tags)
            print(json.dumps({"claim_id": new, "supersedes": args.claim_id}))
            _dependents(args.claim_id)
        elif action == "retract":
            rev = cl.retract(args.claim_id, reason=args.reason, basis=args.basis, actor=actor,
                             method=args.method, expected_rev=args.expected_rev)
            print(json.dumps({"claim_id": args.claim_id, "rev": rev}))
            _dependents(args.claim_id)
        elif action == "revise":
            rev = cl.revise(args.claim_id, reason=args.reason, actor=actor, method=args.method,
                            expected_rev=args.expected_rev, text=args.text,
                            confidence=args.confidence, basis=args.basis, tags=args.tags,
                            valid_from=args.valid_from, valid_until=args.valid_until,
                            source=args.source, evidence=args.evidence)
            print(json.dumps({"claim_id": args.claim_id, "rev": rev}))
        elif action == "show":
            s = _belief(args).get(args.claim_id)
            if s is None:
                raise ClaimRefused("UNKNOWN_CLAIM", args.claim_id)
            _print_state(s, args.json)
        elif action == "history":
            hist = cl.history(args.claim_id)
            if args.json:
                print(json.dumps(hist, sort_keys=True, default=str))
            else:
                for h in hist:
                    print(f"rev {h['rev']} {h['event']} [{h['status']}] {h['recorded_at']}"
                          f" {h['actor']} ({h['method']}) {h['reason'] or ''}".rstrip())
        elif action == "list":
            items = [s for s in _belief(args).values()
                     if not args.status or s.status == args.status]
            items.sort(key=lambda s: s.claim_id)
            if args.json:
                print(json.dumps([asdict(s) for s in items], sort_keys=True, default=str))
            else:
                for s in items:
                    _print_state(s, False)
        elif action == "verify":
            out = cl.verify(args.claim_ids or None, apply=args.apply)
            print(json.dumps({k: out[k] for k in ("counts", "integrity_rate", "written")},
                             sort_keys=True, default=str))
            for cid in sorted(out["details"]):
                for d in out["details"][cid]:
                    if d["outcome"] != "intact":
                        print(f"{cid}\t{d['outcome']}\t{d['source']}\t{d['detail'] or ''}")
            print(out["disclaimer"])
            if out["written"]:
                print(STALE_HINT, file=sys.stderr)
        elif action == "conflicts":
            for cid in cp.conflicts():
                print(cid)
        elif action == "adopt":
            rev = cp.adopt(args.claim_id, actor=actor, reason=args.reason)
            print(json.dumps({"claim_id": args.claim_id, "rev": rev}))
        elif action == "erase":
            out = cl.erase(args.claim_id, reason_class=args.reason_class, actor=actor)
            print(json.dumps(out, sort_keys=True))
            _dependents(args.claim_id)
        elif action == "migrate":
            from scripts import claim_migrate
            out = claim_migrate.migrate(dry_run=args.dry_run, legacy_dir=args.legacy_dir,
                                        report_path=args.report_path)
            print(json.dumps(out, sort_keys=True, default=str))
            if out.get("provenance_mismatches"):
                return 1
        elif action == "doctor":
            from scripts import claim_store
            report = claim_store.doctor()
            try:   # informational (IOU-Z2-3): never a failure
                from scripts.claim_impact import stale_derived
                report["stale_derived"] = len(stale_derived())
            except Exception as exc:  # noqa: BLE001
                report["stale_derived"] = f"unavailable: {exc}"
            print(json.dumps(report, sort_keys=True, default=str))
            healthy = (report["chain_ok"] and report["integrity"] in ("ok", "absent")
                       and not report["dangling_cids"])
            return 0 if healthy else 1
        elif action == "impact":
            from scripts.claim_impact import impact
            if args.claim_id not in cl.current():
                raise ClaimRefused("UNKNOWN_CLAIM", args.claim_id)
            hits = impact(args.claim_id)
            if args.json:
                print(json.dumps(hits, sort_keys=True))
            else:
                for h in hits:
                    print(f"{h['kind']}\t{h['path']}")
        elif action == "stale-derived":
            from scripts.claim_impact import stale_derived
            rows = stale_derived()
            if args.json:
                print(json.dumps(rows, sort_keys=True))
            else:
                for r in rows:
                    print(f"{r['claim_id']}\t{r['status']}\t{r['kind']}\t{r['path']}")
        elif action == "regenerate":
            if not args.claim_id and not args.all:
                print("Error: USAGE: give a claim id or --all", file=sys.stderr)
                return 2
            for path in cp.regenerate(None if args.all else args.claim_id, force=args.force):
                print(path)
        else:
            print("Error: USAGE: zurvan claim {add,supersede,retract,revise,show,history,"
                  "list,conflicts,adopt,regenerate} ...", file=sys.stderr)
            return 2
    except ClaimRefused as exc:
        print(f"Error: {exc.code}: {exc.detail}".rstrip(": "), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: INVALID: {exc}", file=sys.stderr)
        return 1
    if action in _WRITES and not getattr(args, "dry_run", False):
        print(STALE_HINT, file=sys.stderr)
    return 0
