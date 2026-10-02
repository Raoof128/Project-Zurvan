"""Leak canaries, the leak scan, burns and the reseal (spec s5.6; contract s3.10; addendum B).

The scan runs as the sealed identity over developer artifacts it has read ACLs on. It takes NO input
from the development side: its targets come from the root-installed SCAN_TARGETS.json, its needles from
the decrypted sealed sets. A hit burns the matched items (irreversible), records an incident that
refuses the next run until the operator acknowledges it, and reseals the sets without the burned items.

Detection (and its stated floor):
- canary tokens: exact byte match.
- verbatim query text: Boundary's winnowed k-gram fingerprints (k=32, w=33) locate CANDIDATES in each
  file, and a byte-exact confirm (d1.confirm semantics) decides. Winnowing guarantees that any shared
  span of >= k+w-1 = 64 bytes is found. A shared span shorter than 64 bytes is NOT detected, except that
  a WHOLE query of at least 32 bytes is matched by exact substring. That floor is documented, not hidden.
- unreadable target (TCC, ACL, vanished mid-walk): counted; the run is refused with SCAN_INCOMPLETE.

scripts/boundary/d1.py is loaded BY FILE PATH: scripts/boundary/__init__.py pulls sqlite stores and
must never run on the sealed side. Sealed-side code: stdlib only, Python 3.9 compatible.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import traceback
from pathlib import Path

from . import manifest as manifest_mod
from .broker import (broker_dump_items, broker_file_sha256, broker_iso_now, broker_log_error,
                     broker_parse_items, broker_record_incident, broker_record_review, broker_today,
                     broker_verified_manifest)
from .codes import SealRefusal
from .spec import coverage, load_spec
from . import sealed_store

K = 32
W = 33
MIN_MATCH = K + W - 1          # 64: the winnowing guarantee
WHOLE_QUERY_MIN = 32           # a whole query at least this long is matched by exact substring
SCAN_CHUNK = 16 * 1024 * 1024
SCAN_OVERLAP = 16 * 1024       # > the longest possible query in bytes, so no span straddles a chunk edge

_D1 = None


def load_d1():
    """scripts/boundary/d1.py by file path. Never imports scripts.boundary (its __init__ opens stores)."""
    global _D1
    if _D1 is None:
        path = Path(__file__).resolve().parent.parent / "boundary" / "d1.py"
        spec = importlib.util.spec_from_file_location("zurvan_eval_seal_d1", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _D1 = module
    return _D1


def make_canaries(n: int) -> list:
    if type(n) is not int or n < 0 or n > 10000:
        raise ValueError("n")
    return ["zc-" + secrets.token_hex(12) for _ in range(n)]


def _scan_anchors(data: bytes, k: int, w: int) -> list:
    """Winnowed (hash, offset) pairs minus degenerate (small-period) k-grams, as d1 does on both sides."""
    d1 = load_d1()
    return [(h, off) for h, off in d1.winnow(d1.kgram_hashes(data, k), w)
            if not d1.has_small_period(data[off:off + k])]


def build_fingerprints(texts: list, k: int = K, w: int = W) -> dict:
    """hash -> sorted indices of the texts that contain it."""
    out = {}
    for idx, text in enumerate(texts):
        for h, _off in _scan_anchors(text.encode("utf-8"), k, w):
            out.setdefault(h, set()).add(idx)
    return {h: sorted(ix) for h, ix in out.items()}


def scan_targets(paths, dev_home: Path, repo: Path) -> list:
    """Existing scan targets: ~/.claude/projects/*Zurvan*/, <repo>/{data/traces,wiki/traces,logs},
    ~/.zurvan/reports. If ~/.claude/projects exists but cannot be listed it is itself a target, so the
    scan counts it unreadable instead of silently skipping it."""
    dev_home, repo = Path(dev_home), Path(repo)
    found = []
    projects = dev_home / ".claude" / "projects"
    if os.path.lexists(str(projects)):
        try:
            names = sorted(os.listdir(str(projects)))
        except OSError:
            found.append(projects)
        else:
            for name in names:
                if "Zurvan" in name and os.path.isdir(str(projects / name)):
                    found.append(projects / name)
    for cand in (repo / "data" / "traces", repo / "wiki" / "traces", repo / "logs",
                 dev_home / ".zurvan" / "reports"):
        if os.path.lexists(str(cand)):
            found.append(cand)
    return found


def _scan_files(target: Path, counters: dict):
    """Regular files under target. Unreadable directories are counted; symlinks are never followed."""
    def onerror(_exc):
        counters["unreadable"] += 1

    try:
        st = os.lstat(str(target))
    except OSError:
        counters["unreadable"] += 1
        return
    if stat.S_ISREG(st.st_mode):
        yield str(target)
        return
    if not stat.S_ISDIR(st.st_mode):
        return
    for top, _dirs, files in os.walk(str(target), topdown=True, followlinks=False, onerror=onerror):
        for name in sorted(files):
            full = os.path.join(top, name)
            try:
                fst = os.lstat(full)
            except OSError:
                counters["unreadable"] += 1
                continue
            if stat.S_ISREG(fst.st_mode):
                yield full


def _scan_window(data: bytes, canaries_b: dict, found: set, fp: dict, qbytes: list, ids: list, hits: set,
                 wholes: list) -> None:
    d1 = load_d1()
    for token_b in canaries_b:
        if token_b in data:
            found.add(canaries_b[token_b])
    for idx in wholes:
        if ids[idx] not in hits and qbytes[idx] in data:
            hits.add(ids[idx])
    if not fp or len(data) < MIN_MATCH:
        return
    for h, off in _scan_anchors(data, K, W):
        for idx in fp.get(h, ()):
            if ids[idx] in hits:
                continue
            q = qbytes[idx]
            gram = data[off:off + K]
            pos = q.find(gram)
            while pos >= 0:
                lo, length = d1.confirm(q, pos, data, off)
                if length >= MIN_MATCH and d1.informative_length(data[lo:lo + length]) >= MIN_MATCH:
                    hits.add(ids[idx])
                    break
                pos = q.find(gram, pos + 1)


# ---------------------------------------------------------------- #13: target-page (answer-key) leaks
#
# A holdout item's identity (its 32-hex id) and its target page (the answer) are each INNOCENT on their
# own: the canonical programme sanctions referring to items by id/index in controlled outputs, and a target
# path like "wiki/x.md" appears benignly in the corpus export, logs and ordinary work. Only an EXPLICIT
# ASSOCIATION id<->page (an answer key) lets a candidate hill-climb. So this detector burns ONLY on a
# deterministic association grammar (CONFIRMED); a mere ambiguous co-occurrence is SUSPECT and NEVER burns.
# Raw "id and path somewhere in the same file" is explicitly NOT a leak (it would eventually barbecue a
# healthy holdout). Verdicts: CONFIRMED_TARGET_LEAK -> fail+burn; SUSPECT_TARGET_ASSOCIATION -> report only.

# relation words whose presence on a line that ALSO contains the id and a target path confirms the leak
_REL_RE = re.compile(r"(?i)(?<![a-z])(answers?|targets?|expected|ground[_\- ]?truth|gold|correct|label)(?![a-z])")
# a structured record key (quoted or bare), e.g. "item": "<id>"  /  target = <path>
_ITEM_KEY = r'(?<![A-Za-z0-9_])["\']?(?:item|item_id|itemid|id|item_index|qid|query_id)["\']?\s*[:=]\s*["\']?%s(?![0-9a-f])'
_TGT_KEY = r'(?<![A-Za-z0-9_])["\']?(?:targets?|expected|answers?|ground[_\- ]?truth|gold|correct|label)["\']?\s*[:=]\s*(?:\[\s*)?["\']?%s'
_ASSOC_HINT = re.compile(r"(->|=>|<-|[:={])")          # an ambiguous-mapping punctuation hint (for SUSPECT)
_STRUCT_WINDOW = 2000                                   # a structured record may span lines; stay within this


def _arrow_link(line: str, i: str, p: str) -> bool:
    """A bare mapping `i <sep> p` (or reversed) with only quotes/space between: -> => <- : = ."""
    ei, ep = re.escape(i), re.escape(p)
    sep = r'["\'\s]*(?:->|=>|=|:)["\'\s]*'
    rsep = r'["\'\s]*<-["\'\s]*'
    return bool(re.search(ei + sep + ep, line) or re.search(ep + sep + ei, line)
                or re.search(ep + rsep + ei, line) or re.search(ei + rsep + ep, line))


def classify_target_associations(text: str, items: list) -> dict:
    """Pure detector. `items` = [{"id": hex32, "pages": [target_path, ...]}, ...] (abstain items omitted).
    -> {"confirmed": [id...], "suspect": [id...]} (suspect excludes anything confirmed). Deterministic; a
    span is confirmed only by the association grammar, so bare paths and bare ids can never burn."""
    confirmed, suspect = set(), set()
    lines = None
    for it in items:
        iid, pages = it["id"], [p for p in it.get("pages", []) if p]
        if not pages or iid in confirmed or iid not in text:
            continue
        verdict = None
        # (1) structured record, possibly across lines: an item-key == id with a target-key == a page nearby
        for m in re.finditer(_ITEM_KEY % re.escape(iid), text):
            win = text[max(0, m.start() - _STRUCT_WINDOW):m.end() + _STRUCT_WINDOW]
            if any(re.search(_TGT_KEY % re.escape(p), win) for p in pages):
                verdict = "confirmed"
                break
        # (2)/(3) single-line: an arrow mapping, or a relation word on a line carrying the id AND a page
        if verdict != "confirmed":
            if lines is None:
                lines = text.split("\n")
            for ln in lines:
                if iid not in ln:
                    continue
                here = [p for p in pages if p in ln]
                if not here:
                    continue
                if any(_arrow_link(ln, iid, p) for p in here) or _REL_RE.search(ln):
                    verdict = "confirmed"
                    break
                if _ASSOC_HINT.search(ln):
                    verdict = "suspect"
        if verdict == "confirmed":
            confirmed.add(iid)
        elif verdict == "suspect":
            suspect.add(iid)
    return {"confirmed": sorted(confirmed), "suspect": sorted(suspect - confirmed)}


def scan(targets: list, canaries: list, queries: dict, items: list = None) -> dict:
    """-> {"hits": [item_id...], "canary_hits": n, "canary_found": [token...], "unreadable": n,
    "target_confirmed": [id...], "target_suspect": [id...]}. Takes only sealed inputs (canary tokens,
    sealed queries, and for #13 the sealed items' id+target-pages) plus the filesystem it is pointed at."""
    ids = sorted(queries)
    qtexts = [queries[i] for i in ids]
    qbytes = [t.encode("utf-8") for t in qtexts]
    fp = build_fingerprints(qtexts)
    d1 = load_d1()
    wholes = [i for i, b in enumerate(qbytes) if d1.informative_length(b) >= WHOLE_QUERY_MIN]
    canaries_b = {c.encode("utf-8"): c for c in canaries if c}
    counters = {"unreadable": 0}
    hits, found = set(), set()
    assoc_items = [{"id": it["id"], "pages": it.get("pages", [])}
                   for it in (items or []) if it.get("pages")]
    assoc_id_b = [it["id"].encode("ascii") for it in assoc_items]
    t_conf, t_susp = set(), set()
    for target in targets:
        for path in _scan_files(Path(target), counters):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError:
                counters["unreadable"] += 1
                continue
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    continue
                tail = b""
                while True:
                    chunk = os.read(fd, SCAN_CHUNK)
                    if not chunk:
                        break
                    data = tail + chunk
                    _scan_window(data, canaries_b, found, fp, qbytes, ids, hits, wholes)
                    # #13: only decode + run the association grammar when an item id is present (rare),
                    # so the cost on ordinary files is one substring scan each.
                    if assoc_items and any(ib in data for ib in assoc_id_b):
                        res = classify_target_associations(data.decode("utf-8", "replace"), assoc_items)
                        t_conf.update(res["confirmed"])
                        t_susp.update(res["suspect"])
                    tail = data[-SCAN_OVERLAP:]
            except OSError:
                counters["unreadable"] += 1
            finally:
                os.close(fd)
    return {"hits": sorted(hits), "canary_hits": len(found), "canary_found": sorted(found),
            "unreadable": counters["unreadable"],
            "target_confirmed": sorted(t_conf), "target_suspect": sorted(t_susp - t_conf)}


def burn(ledger, item_ids: list, reason: str, date: str) -> list:
    """Irreversible. Returns the ids newly burned (already-burned ids are skipped)."""
    return ledger.burn_items(list(item_ids), reason, date)


def replace_burned(holdout: list, reserve: list, burned: set) -> tuple:
    """-> (holdout', reserve', retired). Each burned HOLDOUT item is replaced by an unburned RESERVE item of
    the SAME stratum (matching difficulty first, then lowest id); the promoted item leaves the reserve.
    Burned items go to `retired`. A burned id is never in either returned pool; a stratum with no reserve
    item left stays short (reported by coverage, never padded)."""
    burned = set(burned)
    keep_h = [i for i in holdout if i["id"] not in burned]
    retired = [i for i in holdout if i["id"] in burned] + [i for i in reserve if i["id"] in burned]
    pool = sorted((i for i in reserve if i["id"] not in burned), key=lambda i: i["id"])
    moved = set()
    for gone in sorted((i for i in holdout if i["id"] in burned), key=lambda i: i["id"]):
        choices = [i for i in pool if i["stratum"] == gone["stratum"] and i["id"] not in moved]
        same = [i for i in choices if i["difficulty"] == gone["difficulty"]]
        pick = (same or choices or [None])[0]
        if pick is not None:
            moved.add(pick["id"])
            keep_h.append(pick)
    new_reserve = [i for i in pool if i["id"] not in moved]
    return (sorted(keep_h, key=lambda i: i["id"]), sorted(new_reserve, key=lambda i: i["id"]),
            sorted(retired, key=lambda i: i["id"]))


# ================================================================ sealed-side composition

def scan_load_config(paths):
    """(dev_home, repo) from the root-installed SCAN_TARGETS.json. Unreadable or malformed: SCAN_INCOMPLETE."""
    try:
        doc = json.loads(Path(paths.scan_targets_file).read_text(encoding="utf-8"))
        dev_home, repo = doc["dev_home"], doc["repo"]
        if (set(doc) != {"dev_home", "repo"} or type(dev_home) is not str or type(repo) is not str
                or not os.path.isabs(dev_home) or not os.path.isabs(repo)):
            raise ValueError("shape")
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise SealRefusal("SCAN_INCOMPLETE") from None
    return Path(dev_home), Path(repo)


def scan_load_canaries(paths, ctx) -> list:
    """canaries.age plaintext: JSONL {"token": str, "item_id": hex32 | null}. A canary with no item_id (or one
    that matches no live item) burns every live item when it hits."""
    try:
        records = []
        for line in ctx.decrypt(sealed_store.canaries_age(paths)).decode("utf-8").split("\n"):
            if not line:
                continue
            rec = json.loads(line)
            token, item_id = rec["token"], rec["item_id"]
            ok = (set(rec) == {"token", "item_id"} and type(token) is str and 16 <= len(token) <= 80
                  and token.isascii() and token.isprintable() and " " not in token
                  and (item_id is None or (type(item_id) is str and len(item_id) == 32)))
            if not ok:
                raise ValueError("record")
            records.append(rec)
        return records
    except SealRefusal:
        raise
    except Exception:  # noqa: BLE001
        raise SealRefusal("MANIFEST_INVALID") from None


def scan_load_pools(paths, ctx, spec):
    return (broker_parse_items(ctx.decrypt(sealed_store.holdout_age(paths)), spec),
            broker_parse_items(ctx.decrypt(sealed_store.reserve_age(paths)), spec))


def scan_run(paths, ctx, ledger) -> dict:
    """The leak scan as `run` and the scheduled agent use it. Raises INTEGRITY_INCIDENT after burning on any
    hit, SCAN_INCOMPLETE if a target could not be read. Returns the scan summary when clean."""
    spec = load_spec(ctx.spec_path)
    with ledger.lock():
        holdout, reserve = scan_load_pools(paths, ctx, spec)
        canary_recs = scan_load_canaries(paths, ctx)
        burned = ledger.burned_ids()
        live = [i for i in holdout + reserve if i["id"] not in burned]
        dev_home, repo = scan_load_config(paths)
        result = scan(scan_targets(paths, dev_home, repo), [r["token"] for r in canary_recs],
                      {i["id"]: i["query"] for i in live},
                      [{"id": i["id"], "pages": [t["page"] for t in i["targets"]]} for i in live])
        live_ids = {i["id"] for i in live}
        by_token = {r["token"]: r["item_id"] for r in canary_recs}
        canary_ids = set()
        for token in result["canary_found"]:
            bound = by_token.get(token)
            if bound in live_ids:
                canary_ids.add(bound)
            elif bound is None or bound not in burned:
                canary_ids = set(live_ids)  # cannot tell which items are exposed: assume all
            # else: the item bound to this canary is already burned; nothing new is exposed
        leak_ids = set(result["hits"]) - canary_ids
        # #13: a CONFIRMED answer-key association burns like a query/canary leak; a SUSPECT (ambiguous)
        # association is recorded for review and NEVER burns automatically.
        target_leak_ids = (set(result.get("target_confirmed", [])) & live_ids) - canary_ids - leak_ids
        target_suspect_ids = (set(result.get("target_suspect", [])) & live_ids) - canary_ids - leak_ids - target_leak_ids
        if target_suspect_ids:
            broker_record_review(paths, "target_suspect", sorted(target_suspect_ids), ctx.now())
        if canary_ids or leak_ids or target_leak_ids:
            today = broker_today(ctx)
            for kind, ids in (("canary_hit", sorted(canary_ids)), ("item_leak", sorted(leak_ids)),
                              ("target_leak", sorted(target_leak_ids))):
                if ids:
                    burn(ledger, ids, kind, today)
                    broker_record_incident(paths, ledger, kind, ids, ctx.now())
            try:
                ctx.reseal(paths, ctx, ledger)
            except Exception:  # noqa: BLE001 - the hit outranks a failed reseal; `broker scan` heals later
                broker_log_error(paths, traceback.format_exc())
            raise SealRefusal("INTEGRITY_INCIDENT")
        if result["unreadable"]:
            raise SealRefusal("SCAN_INCOMPLETE")
        return {"hits": [], "canary_hits": 0, "unreadable": 0, "target_suspect": sorted(target_suspect_ids)}


def reseal_after_burn(paths, ctx, ledger) -> dict:
    """Replace burned items, re-encrypt both sets, re-sign the manifest with sequence+1 (same suite_id,
    new suite_manifest_sha256). A no-op when no burned id sits in the sealed sets. Refuses with
    MANIFEST_INVALID if the current sealed state does not verify (nothing is rewritten then)."""
    spec = load_spec(ctx.spec_path)
    with ledger.lock():
        m, _sha = broker_verified_manifest(paths, ledger, record=False)
        for pool in ("holdout", "reserve"):
            if broker_file_sha256(sealed_store.age_path(paths, pool)) != m[pool + "_age_sha256"]:
                raise SealRefusal("MANIFEST_INVALID")
        holdout, reserve = scan_load_pools(paths, ctx, spec)
        burned = ledger.burned_ids()
        if not any(i["id"] in burned for i in holdout + reserve):
            return {"resealed": False}
        new_h, new_r, _retired = replace_burned(holdout, reserve, burned)
        for pool, items in (("holdout", new_h), ("reserve", new_r)):
            ctx.encrypt(broker_dump_items(items), Path(str(sealed_store.age_path(paths, pool)) + ".new"))
        digests = {p: broker_file_sha256(Path(str(sealed_store.age_path(paths, p)) + ".new")) for p in ("holdout", "reserve")}
        counts = {"holdout": {}, "reserve": {}}
        for pool, items in (("holdout", new_h), ("reserve", new_r)):
            for item in items:
                counts[pool][item["stratum"]] = counts[pool].get(item["stratum"], 0) + 1
        seen, ledger_burns = set(), []
        for entry in ledger.entries():
            if entry["kind"] == "burn" and entry["id"] not in seen:
                seen.add(entry["id"])
                ledger_burns.append({"id": entry["id"], "reason": entry["reason"], "date": entry["date"]})
        new_m = manifest_mod.build_manifest(
            suite_id=m["suite_id"], sequence=m["sequence"] + 1, created=broker_iso_now(ctx),
            models=m["models"], prompt_sha256=m["prompt_sha256"], corpus_sha256=m["corpus_sha256"],
            holdout_age_sha256=digests["holdout"], reserve_age_sha256=digests["reserve"],
            dev_sha256=m["dev_sha256"], strata_counts=counts, accepted=m["accepted"],
            rejected=m["rejected"],
            short=coverage({"holdout": new_h, "reserve": new_r}, spec)["short"],
            evaluator_version=m["evaluator_version"], burn_ledger=sorted(ledger_burns, key=lambda e: e["id"]))
        staged = Path(str(sealed_store.manifest(paths)) + ".new")
        manifest_mod.write_manifest(staged, new_m)
        sig = manifest_mod.sign(staged, paths.keys_dir / "signing_ed25519")
        for pool in ("holdout", "reserve"):
            tgt = sealed_store.age_path(paths, pool)
            os.replace(str(tgt) + ".new", str(tgt))
        os.replace(str(sig), str(sealed_store.manifest_sig(paths)))
        os.replace(str(staged), str(sealed_store.manifest(paths)))
        ledger.append({"kind": "manifest_seen", "sequence": new_m["sequence"],
                       "manifest_sha256": manifest_mod.suite_manifest_sha256(sealed_store.manifest(paths))})
        return {"resealed": True, "sequence": new_m["sequence"]}
