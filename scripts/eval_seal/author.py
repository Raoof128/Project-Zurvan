"""Sealed authoring pipeline (spec s6; contract s3.12). Runs as `zurvaneval` via bin/zurvan-author.

author -> deterministic filters -> reviewer (separate session) -> independent re-solve -> select -> seal.

Nothing sealed ever leaves this process except through three doors: encrypted files under the eval
home, the signed public manifest, and 0600 audit files (prompts and raw model output) under
runs/authoring/. `author_main` prints ONLY the public manifest (or a bare refusal code); stderr is never
written; an unexpected exception goes to runs/errors.log. Counts by reject CATEGORY are aggregate and
public (the manifest's `rejected` map); no query, target, per-item reason or sample is ever printed.

Sealed-side code: stdlib only, Python 3.9 compatible. Model access goes through the injected
`ModelRunner`, so every test uses a scripted fake and no model is called by the test suite.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import secrets
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Protocol

from . import grade as author_grade
from . import manifest as author_manifest
from . import spec as author_spec
from . import sysenv
from .codes import SealRefusal
from . import sealed_store
from .corpus import corpus_pages as author_corpus_pages
from .corpus import corpus_sha256 as author_corpus_sha256
from .paths import SealPaths

AUTHOR_ROLES = ("author", "reviewer", "resolver")
AUTHOR_PER_STRATUM = 20
AUTHOR_REVIEW_BATCH = 20
AUTHOR_CANARIES = 24
AUTHOR_NEARDUP_THRESHOLD = 0.7      # estimated shingle Jaccard; real Jaccard >= 0.8 clears it with margin
AUTHOR_LEXICAL_THRESHOLD = 0.9      # token-set Jaccard
AUTHOR_REJECT_REASONS = (
    "bad_schema", "target_missing", "answer_leak", "dev_contamination", "exact_dup", "lexical_overlap",
    "near_dup", "unparseable", "reviewer_rejected", "resolve_miss",
)
AUTHOR_DEFAULT_MODELS = {"author": "opus", "reviewer": "sonnet", "resolver": "haiku"}
AUTHOR_ALLOWED_TOOLS = "Read,Grep,Glob"
AUTHOR_DENIED_TOOLS = "Bash,WebFetch,WebSearch,Write,Edit,NotebookEdit,Task"
AUTHOR_CLAUDE_ENV = {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}")
_SEED_RE = re.compile(r"[0-9]{1,10}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MAX_TEXT = 4 * 1024 * 1024
_MAX_PAGE_READ = 256 * 1024
_MAX_PROMPT_FILE = 256 * 1024
_MP = (1 << 61) - 1
_STOP = frozenset((
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "were", "use", "used", "how",
    "what", "why", "when", "where", "which", "does", "did", "not", "all", "can", "its", "our", "their",
))
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class ModelRunner(Protocol):
    def __call__(self, role: str, model: str, prompt: str, *, cwd: Path) -> str: ...


# ----------------------------------------------------------------------------------------------- runner

def _author_check_model(model) -> str:
    if type(model) is not str or not _MODEL_RE.fullmatch(model):
        raise ValueError("model")
    return model


def author_claude_argv(claude_bin, model, mcp_config) -> list:
    """The exact argv. The prompt travels on stdin, never in argv. Tools: Read/Grep/Glob only, no MCP."""
    claude_bin, mcp_config = Path(claude_bin), Path(mcp_config)
    if not claude_bin.is_absolute() or not mcp_config.is_absolute():
        raise ValueError("paths must be absolute")
    return [
        str(claude_bin), "-p", "--model", _author_check_model(model), "--output-format", "json",
        "--allowedTools", AUTHOR_ALLOWED_TOOLS, "--disallowedTools", AUTHOR_DENIED_TOOLS,
        "--strict-mcp-config", "--mcp-config", str(mcp_config),
    ]


def author_default_claude(paths) -> Path:
    """The pinned root-owned copy installed by install.sh; never anything under a user's ~/.local."""
    return Path(paths.broker_bin) / "claude"


def claude_runner(claude_bin, home, *, mcp_config=None, timeout_s: int = 900, run=None):
    claude_bin, home = Path(claude_bin), Path(home)
    mcp = Path(mcp_config) if mcp_config is not None else claude_bin.parent.parent / "empty-mcp.json"
    runner_run = run if run is not None else subprocess.run

    def runner(role: str, model: str, prompt: str, *, cwd) -> str:
        argv = author_claude_argv(claude_bin, model, mcp)
        # R18: claude keeps session transcripts (plaintext prompts and items) under its config dir; that dir
        # must be the Time Machine-excluded private/ dir of the eval home, never ~/.claude.
        extra = dict(AUTHOR_CLAUDE_ENV, CLAUDE_CONFIG_DIR=str(home / "private" / "claude"))
        env = sysenv.clean_env(home, extra)
        try:
            proc = runner_run(argv, input=prompt.encode("utf-8"), cwd=str(cwd), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s, check=False)
            if proc.returncode != 0:
                raise SealRefusal("INTERNAL")
            doc = json.loads(proc.stdout.decode("utf-8"))
        except SealRefusal:
            raise
        except Exception:  # noqa: BLE001 - nothing from the child may travel in an exception
            raise SealRefusal("INTERNAL") from None
        if type(doc) is not dict or doc.get("is_error") or type(doc.get("result")) is not str:
            raise SealRefusal("INTERNAL")
        return doc["result"]

    return runner


# --------------------------------------------------------------------------------------------- minhash

def author_norm(text) -> str:
    return " ".join(_TOKEN.findall(str(text).casefold()))


def author_shingles(text, ngram: int = 5) -> frozenset:
    s = author_norm(text)
    if len(s) < ngram:
        return frozenset((s,))
    return frozenset(s[i:i + ngram] for i in range(len(s) - ngram + 1))


_PERM_CACHE = {}


def _author_perms(num_perm: int, seed: int):
    key = (num_perm, seed)
    if key not in _PERM_CACHE:
        rng = random.Random("minhash:%d:%d" % (num_perm, seed))
        _PERM_CACHE[key] = tuple((rng.randrange(1, _MP), rng.randrange(0, _MP)) for _ in range(num_perm))
    return _PERM_CACHE[key]


def author_minhash(text, *, num_perm: int = 128, ngram: int = 5, seed: int = 1) -> tuple:
    """Stdlib MinHash over normalised character n-grams. blake2b, never hash(): stable across processes."""
    if type(num_perm) is not int or num_perm < 1 or type(ngram) is not int or ngram < 1:
        raise ValueError("arguments")
    perms = _author_perms(num_perm, seed)
    sig = [_MP] * num_perm
    for sh in author_shingles(text, ngram):
        x = int.from_bytes(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).digest(), "big") % _MP
        for i, (a, b) in enumerate(perms):
            v = (a * x + b) % _MP
            if v < sig[i]:
                sig[i] = v
    return tuple(sig)


def author_jaccard_est(a, b) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("signature lengths")
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


# ------------------------------------------------------------------------------------------------ pages

def _author_read_page(corpus_dir, page) -> str:
    fd = os.open(str(Path(corpus_dir) / page), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not regular")
        return os.read(fd, _MAX_PAGE_READ).decode("utf-8", "replace")
    finally:
        os.close(fd)


def _author_titles(text) -> list:
    lines = text.splitlines()[:80]
    titles = []
    if lines and lines[0].strip() == "---":
        for ln in lines[1:]:
            if ln.strip() == "---":
                break
            m = re.match(r"\s*title\s*:\s*(.+?)\s*$", ln, re.I)
            if m:
                titles.append(m.group(1).strip("'\""))
    for ln in lines:
        if ln.startswith("# "):
            titles.append(ln[2:].strip())
            break
    return [t for t in titles if t]


def _author_phrase_in(phrase: str, query_norm: str) -> bool:
    return bool(phrase) and (" " + phrase + " ") in (" " + query_norm + " ")


def _author_distinctive(title_norm: str) -> list:
    return [t for t in title_norm.split() if len(t) >= 3 and not t.isdigit() and t not in _STOP]


def _author_leaks(item, corpus_dir) -> bool:
    """True when the query contains a distinctive string of a target: its path, filename stem, title,
    section heading, or most of the distinctive tokens of its title. Unreadable target -> raises OSError."""
    query = item["query"]
    qlow, qnorm = query.casefold(), author_norm(query)
    qtokens = set(qnorm.split())
    for target in item["targets"]:
        page = target["page"]
        if page.casefold() in qlow or _author_phrase_in(author_norm(page), qnorm):
            return True
        stem = author_norm(Path(page).stem)
        if len(stem) >= 12 and _author_phrase_in(stem, qnorm):
            return True
        loc = target["locator"]
        if loc["kind"] == "section":
            heading = author_norm(loc["value"])
            if len(heading) >= 12 and _author_phrase_in(heading, qnorm):
                return True
        for title in _author_titles(_author_read_page(corpus_dir, page)):
            tn = author_norm(title)
            if len(tn) >= 12 and _author_phrase_in(tn, qnorm):
                return True
            words = _author_distinctive(tn)
            if len(words) >= 3 and sum(1 for w in words if w in qtokens) >= math.ceil(0.8 * len(words)):
                return True
    return False


# ----------------------------------------------------------------------------------------------- filters

def author_filters(cands, dev_items, corpus_dir, spec, *, pages=None):
    """Deterministic filters, in order: schema, target exists (store-relative), answer leakage, DEV
    contamination, exact duplicate, normalised lexical overlap, MinHash near-duplicate. Candidates are
    judged in input order; the earlier of two near-identical candidates wins. Returns (kept, counts)."""
    pages = author_corpus_pages(corpus_dir) if pages is None else pages
    counts = {r: 0 for r in AUTHOR_REJECT_REASONS}

    def profile(query):
        norm = author_norm(query)
        return norm, frozenset(norm.split()), author_minhash(query)

    dev_profiles = []
    for d in dev_items:
        if isinstance(d, dict) and isinstance(d.get("query"), str):
            dev_profiles.append(profile(d["query"]))

    def similar(prof, others):
        """None, or which test fired: 'exact', 'lexical' or 'near'."""
        norm, toks, sig = prof
        for onorm, otoks, osig in others:
            if norm == onorm:
                return "exact"
            union = toks | otoks
            if union and len(toks & otoks) / len(union) >= AUTHOR_LEXICAL_THRESHOLD:
                return "lexical"
            if author_jaccard_est(sig, osig) >= AUTHOR_NEARDUP_THRESHOLD:
                return "near"
        return None

    kept, kept_profiles = [], []
    for item in cands:
        try:
            author_spec.validate_item(item, spec)
        except ValueError:
            counts["bad_schema"] += 1
            continue
        if not author_grade.resolve_target_presence(item, pages):
            counts["target_missing"] += 1
            continue
        try:
            leaks = _author_leaks(item, corpus_dir)
        except OSError:
            counts["target_missing"] += 1
            continue
        if leaks:
            counts["answer_leak"] += 1
            continue
        prof = profile(item["query"])
        if similar(prof, dev_profiles) is not None:
            counts["dev_contamination"] += 1
            continue
        hit = similar(prof, kept_profiles)
        if hit is not None:
            counts[{"exact": "exact_dup", "lexical": "lexical_overlap", "near": "near_dup"}[hit]] += 1
            continue
        kept.append(item)
        kept_profiles.append(prof)
    return kept, counts


# ------------------------------------------------------------------------------------------------ select

_DIFFS = ("easy", "medium", "hard")


def _author_quotas(n: int, targets: dict) -> dict:
    raw = {d: n * targets[d] for d in _DIFFS}
    base = {d: int(math.floor(raw[d] + 1e-9)) for d in _DIFFS}
    order = sorted(_DIFFS, key=lambda d: (-(raw[d] - base[d]), _DIFFS.index(d)))
    for d in order[: n - sum(base.values())]:
        base[d] += 1
    return base


def _author_pick(pool: list, n: int, targets: dict) -> list:
    k = min(n, len(pool))
    quotas = _author_quotas(k, targets)
    picked, used = [], set()
    for d in _DIFFS:
        for i, item in enumerate(pool):
            if len([p for p in picked if p["difficulty"] == d]) >= quotas[d]:
                break
            if item["difficulty"] == d and i not in used:
                picked.append(item)
                used.add(i)
    for i, item in enumerate(pool):
        if len(picked) >= k:
            break
        if i not in used:
            picked.append(item)
            used.add(i)
    return picked


def author_select(accepted, spec, *, seed):
    """HOLDOUT gets min_holdout per stratum first, RESERVE gets the remainder up to min_reserve, both
    balanced toward the stratum's difficulty targets. A short pool is reported, never padded."""
    holdout, reserve, short = [], [], []
    for entry in spec["strata"]:
        sid = entry["id"]
        pool = sorted((i for i in accepted if i.get("stratum") == sid), key=lambda i: i["id"])
        random.Random("select:%s:%s" % (seed, sid)).shuffle(pool)
        h = _author_pick(pool, entry["min_holdout"], entry["difficulty_targets"])
        taken = {id(x) for x in h}
        rest = [x for x in pool if id(x) not in taken]
        r = _author_pick(rest, entry["min_reserve"], entry["difficulty_targets"])
        holdout.extend(sorted(h, key=lambda i: i["id"]))
        reserve.extend(sorted(r, key=lambda i: i["id"]))
        if len(h) < entry["min_holdout"]:
            short.append({"pool": "holdout", "stratum": sid, "have": len(h), "need": entry["min_holdout"]})
        if len(r) < entry["min_reserve"]:
            short.append({"pool": "reserve", "stratum": sid, "have": len(r), "need": entry["min_reserve"]})
    return holdout, reserve, short


# ------------------------------------------------------------------------------------------ prompts, io

def author_prompt_digest(rubric: str, templates: dict) -> str:
    doc = {"rubric": rubric}
    doc.update({role: templates[role] for role in AUTHOR_ROLES})
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _author_json(text):
    """The first JSON value in model output: bare, fenced, or after a line of prose. None if there is none."""
    if type(text) is not str or len(text) > _MAX_TEXT:
        return None
    s = text.strip()
    tick = "`" * 3
    fence = re.match(tick + r"[A-Za-z]*\s*\n(.*?)\n" + tick + r"\s*$", s, re.S)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", s):
        try:
            return dec.raw_decode(s[m.start():])[0]
        except ValueError:
            continue
    return None


def _author_list(doc, key):
    if type(doc) is list:
        return doc
    if type(doc) is dict and type(doc.get(key)) is list:
        return doc[key]
    return None


class _Audit:
    """0600 prompt/output files under the eval home. The only place raw model text is ever kept."""

    def __init__(self, directory):
        self.dir, self.n = (Path(directory) if directory is not None else None), 0
        if self.dir is not None:
            _author_private_dir(self.dir)

    def record(self, role, prompt, output):
        if self.dir is None:
            return
        self.n += 1
        for kind, body in (("prompt", prompt), ("out", output)):
            _author_write_new(self.dir / ("%04d-%s.%s.txt" % (self.n, role, kind)), body.encode("utf-8"), 0o600)


def _author_private_dir(path: Path) -> None:
    missing = []
    p = Path(path)
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        os.mkdir(str(d), 0o700)
        os.chmod(str(d), 0o700)


def _author_write_new(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(str(path), mode)


def _author_check_models(models) -> dict:
    if type(models) is not dict or any(role not in models for role in AUTHOR_ROLES):
        raise ValueError("models")
    return {role: _author_check_model(models[role]) for role in AUTHOR_ROLES}


def _author_check_templates(templates) -> dict:
    if type(templates) is not dict or any(type(templates.get(r)) is not str or not templates[r].strip()
                                          for r in AUTHOR_ROLES):
        raise ValueError("templates")
    return {r: templates[r] for r in AUTHOR_ROLES}


def author_load_templates(prompts_dir) -> dict:
    out = {}
    for role in AUTHOR_ROLES:
        try:
            p = Path(prompts_dir) / (role + ".md")
            if not p.is_file() or p.stat().st_size > _MAX_PROMPT_FILE:
                raise SealRefusal("BAD_ARGS")
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise SealRefusal("BAD_ARGS") from None
        if not text.strip():
            raise SealRefusal("BAD_ARGS")
        out[role] = text
    return out


def _author_author_prompt(template, rubric, entry, n) -> str:
    return (template + "\n\n## Rubric\n" + rubric + "\n\n## Assignment\n"
            "STRATUM_ID: %s\nSTRATUM_NAME: %s\nGRADING: %s\nDIFFICULTY_TARGETS: %s\nCOUNT: %d\n" % (
                entry["id"], entry["name"], entry["grading"],
                json.dumps(entry["difficulty_targets"], sort_keys=True), n))


def _author_reviewer_prompt(template, rubric, batch) -> str:
    rows = [{"n": i, "stratum": it["stratum"], "query": it["query"], "targets": [
        {"page": t["page"], "locator": t["locator"]} for t in it["targets"]], "difficulty": it["difficulty"]}
        for i, it in enumerate(batch)]
    return (template + "\n\n## Rubric\n" + rubric + "\n\n## Assignment\nCANDIDATES_JSON: "
            + json.dumps(rows, sort_keys=True, separators=(",", ":")))


def _author_resolver_prompt(template, query) -> str:
    """Template plus the query. Never targets, ids, strata or rubric."""
    return template + "\n\n## Question\nQUERY: " + query


def _author_build_item(raw, entry, models) -> object:
    if type(raw) is not dict:
        return None
    item = dict(raw)
    item.update(
        id=secrets.token_hex(16), stratum=entry["id"], grading=entry["grading"],
        provenance={"author": models["author"][:64], "reviewer": models["reviewer"][:64]})
    if type(item.get("targets")) is list:
        item["targets"] = [dict({"source_id": None}, **t) if type(t) is dict else t for t in item["targets"]]
    return item


def _author_review(items, runner, models, template, rubric, cwd, audit, counts) -> list:
    kept = []
    for start in range(0, len(items), AUTHOR_REVIEW_BATCH):
        batch = items[start:start + AUTHOR_REVIEW_BATCH]
        prompt = _author_reviewer_prompt(template, rubric, batch)
        out = runner("reviewer", models["reviewer"], prompt, cwd=cwd)
        audit.record("reviewer", prompt, out)
        verdicts = _author_list(_author_json(out), "verdicts") or []
        keep = set()
        for v in verdicts:
            if type(v) is dict and type(v.get("n")) is int and v.get("keep") is True:
                keep.add(v["n"])
        for i, item in enumerate(batch):
            if i in keep:
                kept.append(item)
            else:
                counts["reviewer_rejected"] += 1
    return kept


def _author_resolves(item, out) -> bool:
    results = _author_list(_author_json(out), "results")
    if results is None:
        return False
    rows = [r for r in results if type(r) is dict and type(r.get("page")) is str]
    if item["grading"] == "abstain":
        return not results                     # not_found: the blind resolver must also find nothing
    return any(author_grade.hit({"page": r["page"], "heading": r.get("heading")}, t)
               for r in rows for t in item["targets"])


# ------------------------------------------------------------------------------------------- suite author

def author_suite(spec, rubric, dev_items, corpus_dir, runner, models, *, seed, templates, dev_sha256=None,
                 audit_dir=None, per_stratum: int = AUTHOR_PER_STRATUM) -> dict:
    models = _author_check_models(models)
    templates = _author_check_templates(templates)
    corpus_dir = Path(corpus_dir)
    pages = author_corpus_pages(corpus_dir)
    audit = _Audit(audit_dir)
    counts = {r: 0 for r in AUTHOR_REJECT_REASONS}

    cands = []
    for entry in spec["strata"]:
        prompt = _author_author_prompt(templates["author"], rubric, entry, per_stratum)
        out = runner("author", models["author"], prompt, cwd=corpus_dir)
        audit.record("author", prompt, out)
        raws = _author_list(_author_json(out), "candidates")
        if raws is None:
            counts["unparseable"] += 1
            continue
        for raw in raws:
            item = _author_build_item(raw, entry, models)
            if item is None:
                counts["bad_schema"] += 1
            else:
                cands.append(item)

    kept, fcounts = author_filters(cands, dev_items, corpus_dir, spec, pages=pages)
    for k, v in fcounts.items():
        counts[k] += v

    reviewed = []
    for entry in spec["strata"]:
        group = [i for i in kept if i["stratum"] == entry["id"]]
        reviewed.extend(_author_review(group, runner, models, templates["reviewer"], rubric, corpus_dir,
                                       audit, counts))

    accepted = []
    for item in reviewed:
        prompt = _author_resolver_prompt(templates["resolver"], item["query"])
        out = runner("resolver", models["resolver"], prompt, cwd=corpus_dir)
        audit.record("resolver", prompt, out)
        if _author_resolves(item, out):
            accepted.append(item)
        else:
            counts["resolve_miss"] += 1

    holdout, reserve, short = author_select(accepted, spec, seed=seed)
    cov = author_spec.coverage({"holdout": holdout, "reserve": reserve}, spec)
    return {
        "holdout": holdout, "reserve": reserve, "short": short, "rejected": counts, "accepted": len(accepted),
        "coverage": cov, "models": dict(models), "prompt_sha256": author_prompt_digest(rubric, templates),
        "corpus_sha256": author_corpus_sha256(corpus_dir), "dev_sha256": dev_sha256,
        "strata_counts": {"holdout": cov["holdout"], "reserve": cov["reserve"]},
    }


# ----------------------------------------------------------------------------------------------- sealing

def _author_jsonl(items) -> bytes:
    return "".join(json.dumps(i, sort_keys=True, separators=(",", ":")) + "\n" for i in items).encode("utf-8")


def _author_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def author_canary_records(tokens) -> bytes:
    """canaries.age plaintext, in exactly the format scan.scan_load_canaries reads (R26): one JSON object
    per line, {"token": str, "item_id": null}. A set-level canary names no item, so a hit burns every live
    item. Refuses any token the reader would refuse, before anything is encrypted."""
    lines = []
    for token in tokens:
        if (type(token) is not str or not 16 <= len(token) <= 80 or not token.isascii()
                or not token.isprintable() or " " in token):
            raise SealRefusal("BAD_ARGS")
        lines.append(json.dumps({"token": token, "item_id": None}, sort_keys=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("ascii")


def seal_suite(bundle, paths, *, encrypt, sign, make_canaries, sequence: int = 1, now=None) -> dict:
    """Encrypt both pools and the canaries, write and sign the manifest, return the PUBLIC manifest.
    Refuses (BAD_ARGS) to overwrite any existing sealed file; validates everything before writing."""
    dev_sha = bundle.get("dev_sha256")
    if type(dev_sha) is not str or not _HEX64.fullmatch(dev_sha):
        raise SealRefusal("BAD_ARGS")
    # the sealed set (the four generation files) is built in a fresh generation dir and promoted atomically;
    # canaries.age is non-generational. Refuse if a store is already sealed (active) or a canaries file sits.
    if sealed_store.active_dir(paths).exists() or os.path.lexists(str(sealed_store.canaries_age(paths))):
        raise SealRefusal("BAD_ARGS")
    canaries = make_canaries(AUTHOR_CANARIES)
    if (type(canaries) is not list or len(canaries) != AUTHOR_CANARIES or len(set(canaries)) != len(canaries)
            or any(type(c) is not str or not c or "\n" in c for c in canaries)):
        raise SealRefusal("BAD_ARGS")
    Path(paths.sets_dir).mkdir(parents=True, exist_ok=True)
    gen = sealed_store.new_generation(paths, sequence)
    written = []
    try:
        digests = {}
        for name, out, data in (("holdout", gen / sealed_store.HOLDOUT, _author_jsonl(bundle["holdout"])),
                                ("reserve", gen / sealed_store.RESERVE, _author_jsonl(bundle["reserve"])),
                                ("canaries", sealed_store.canaries_age(paths), author_canary_records(canaries))):
            out = Path(out)
            encrypt(data, out)
            if not out.is_file():
                raise SealRefusal("INTERNAL")
            written.append(out)
            os.chmod(str(out), 0o600)
            digests[name] = hashlib.sha256(out.read_bytes()).hexdigest()
        manifest = author_manifest.build_manifest(
            suite_id=secrets.token_hex(8), sequence=sequence, created=(now or _author_utc_now)(),
            models=bundle["models"], prompt_sha256=bundle["prompt_sha256"], corpus_sha256=bundle["corpus_sha256"],
            holdout_age_sha256=digests["holdout"], reserve_age_sha256=digests["reserve"],
            dev_sha256=dev_sha, strata_counts=bundle["strata_counts"], accepted=bundle["accepted"],
            rejected=bundle["rejected"], short=bundle["short"],
            evaluator_version=author_grade.EVALUATOR_VERSION, burn_ledger=[])
        mf = gen / sealed_store.MANIFEST
        author_manifest.write_manifest(mf, manifest)
        written.append(mf)
        os.chmod(str(mf), 0o600)
        sig = Path(sign(mf))
        written.append(sig)
        if not sig.is_file():
            raise SealRefusal("INTERNAL")
        os.chmod(str(sig), 0o600)
        for f in (gen / sealed_store.HOLDOUT, gen / sealed_store.RESERVE, mf, sig):
            sealed_store.fsync_file(f)
        sealed_store.promote(paths, sequence)   # ONE atomic swap makes the generation live
    except BaseException:
        for f in written:
            try:
                os.unlink(str(f))
            except OSError:
                pass
        try:
            import shutil as _sh
            _sh.rmtree(str(gen))
        except OSError:
            pass
        raise
    author_manifest.assert_no_per_item_digests(manifest)
    return manifest


# ------------------------------------------------------------------------------------------------ main

_FLAGS = ("spec", "rubric", "prompts", "dev", "seed", "sequence", "workspace", "author-model", "reviewer-model",
          "resolver-model", "public-out")


def _author_parse(argv) -> dict:
    """Strict `--flag value` pairs from a closed set; required: spec, rubric, prompts."""
    if type(argv) is not list or len(argv) % 2:
        raise SealRefusal("BAD_ARGS")
    args = {}
    for i in range(0, len(argv), 2):
        flag, val = argv[i], argv[i + 1]
        if type(flag) is not str or not flag.startswith("--") or flag[2:] not in _FLAGS or type(val) is not str:
            raise SealRefusal("BAD_ARGS")
        if flag[2:] in args or "\x00" in val:
            raise SealRefusal("BAD_ARGS")
        args[flag[2:]] = val
    if not all(k in args for k in ("spec", "rubric", "prompts")):
        raise SealRefusal("BAD_ARGS")
    for k in ("spec", "rubric", "prompts", "dev", "public-out", "workspace"):
        if k in args and not Path(args[k]).is_absolute():
            raise SealRefusal("BAD_ARGS")
    for k in ("seed", "sequence"):
        if k in args and not _SEED_RE.fullmatch(args[k]):
            raise SealRefusal("BAD_ARGS")
    for role in AUTHOR_ROLES:
        k = role + "-model"
        if k in args and not _MODEL_RE.fullmatch(args[k]):
            raise SealRefusal("BAD_ARGS")
    return args


def _author_read_text(path, limit) -> bytes:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > limit:
            raise SealRefusal("BAD_ARGS")
        return p.read_bytes()
    except OSError:
        raise SealRefusal("BAD_ARGS") from None


def _author_load_dev(path) -> tuple:
    data = _author_read_text(path, 16 * 1024 * 1024)
    items = []
    try:
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if type(row) is not dict or type(row.get("query")) is not str:
                    raise SealRefusal("BAD_ARGS")
                items.append(row)
    except (ValueError, UnicodeDecodeError):
        raise SealRefusal("BAD_ARGS") from None
    return items, hashlib.sha256(data).hexdigest()


def author_age_encrypt(paths):
    """Production encrypt: the PINNED age binary, to the recipient of the eval identity's key."""
    age = Path(paths.broker_bin) / "age"
    key = Path(paths.keys_dir) / "age.key"

    def encrypt(plain: bytes, out_path) -> None:
        try:
            proc = subprocess.run(
                [str(age), "-e", "-i", str(key), "-o", str(out_path)], input=plain,
                env=sysenv.clean_env(Path(paths.eval_home)), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=300, check=False)
        except (OSError, subprocess.SubprocessError):
            raise SealRefusal("INTERNAL") from None
        if proc.returncode != 0:
            raise SealRefusal("INTERNAL")

    return encrypt


def _author_errors_log(paths, text: str) -> None:
    try:
        runs = Path(paths.runs_dir)
        if not runs.exists():
            _author_private_dir(runs)
        fd = os.open(str(runs / "errors.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " author\n" + text + "\n")
    except OSError:
        pass


def _author_emit_refusal(code: str) -> None:
    sys.stdout.write(json.dumps({"ok": False, "code": code}, sort_keys=True) + "\n")
    sys.stdout.flush()


def _author_no_dups(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def _author_no_const(_x):
    raise ValueError("NaN/Infinity")


_BUNDLE_KEYS = frozenset(("holdout", "reserve", "short", "rejected", "accepted", "coverage", "models",
                         "prompt_sha256", "corpus_sha256", "dev_sha256", "strata_counts"))


def author_write_bundle(bundle, workspace):
    """Rev 3 #2: the draft phase (as zurvanauthor) writes the selected PLAINTEXT bundle here. The workspace
    is zurvanauthor-owned and zurvaneval-readable; the seal phase reads it. 0600, never overwritten."""
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    os.chmod(str(ws), 0o700)
    dest = ws / "bundle.json"
    data = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return dest


def author_read_bundle(workspace):
    """The seal phase reads the draft bundle. A missing, oversized, symlinked or malformed bundle is
    BAD_ARGS; the holdout/reserve pools must be lists of dicts. Takes no model, touches no key."""
    path = Path(workspace) / "bundle.json"
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise SealRefusal("BAD_ARGS") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 64 * 1024 * 1024:
            raise SealRefusal("BAD_ARGS")
        data = os.read(fd, 64 * 1024 * 1024 + 1)
    finally:
        os.close(fd)
    try:
        bundle = json.loads(data.decode("utf-8"), object_pairs_hook=_author_no_dups,
                            parse_constant=_author_no_const)
    except (ValueError, UnicodeDecodeError):
        raise SealRefusal("BAD_ARGS") from None
    if type(bundle) is not dict or set(bundle) != _BUNDLE_KEYS:
        raise SealRefusal("BAD_ARGS")
    for pool in ("holdout", "reserve"):
        if type(bundle[pool]) is not list or any(type(i) is not dict for i in bundle[pool]):
            raise SealRefusal("BAD_ARGS")
    return bundle


def author_draft_main(argv, *, paths=None, runner=None, now=None):
    """Rev 3 #2: the DRAFTING phase. Runs as zurvanauthor. Reads only the corpus and the public
    rubric/prompts, runs the models, writes the selected bundle to --workspace. Touches NO key and does NOT
    encrypt or sign; a corpus prompt-injection that tells it to read keys/ hits EACCES (OS-enforced).
    stdout: aggregate counts only. Exit 0 full / 1 short / 2 refusal / 3 internal."""
    paths = paths if paths is not None else SealPaths.real()
    try:
        args = _author_parse(list(argv))
        if "workspace" not in args:
            raise SealRefusal("BAD_ARGS")
        models = {role: args.get(role + "-model", AUTHOR_DEFAULT_MODELS[role]) for role in AUTHOR_ROLES}
        if models["reviewer"] == models["author"]:
            raise SealRefusal("BAD_ARGS")
        spec = author_spec.load_spec(args["spec"])
        rubric = _author_read_text(args["rubric"], _MAX_PROMPT_FILE).decode("utf-8", "replace")
        templates = author_load_templates(args["prompts"])
        dev_path = Path(args["dev"]) if "dev" in args else sealed_store.dev_ref(paths)
        dev_items, dev_sha = _author_load_dev(dev_path)
        seed = int(args["seed"]) if "seed" in args else secrets.randbits(31)
        if runner is None:
            runner = claude_runner(author_default_claude(paths), Path(paths.author_home))
        audit = Path(args["workspace"]) / "authoring" / secrets.token_hex(8)  # plaintext, author-owned
        bundle = author_suite(spec, rubric, dev_items, Path(paths.corpus_dir), runner, models, seed=seed,
                              templates=templates, dev_sha256=dev_sha, audit_dir=audit)
        author_write_bundle(bundle, args["workspace"])
        sys.stdout.write(json.dumps({"coverage": bundle["coverage"], "rejected": bundle["rejected"],
                                     "accepted": bundle["accepted"]}, sort_keys=True) + "\n")
        sys.stdout.flush()
        return 0 if bundle["coverage"]["ok"] else 1
    except SealRefusal as exc:
        _author_emit_refusal(exc.code)
        return 2
    except Exception:  # noqa: BLE001
        _author_errors_log(paths, traceback.format_exc())
        _author_emit_refusal("INTERNAL")
        return 3


def author_seal_main(argv, *, paths=None, encrypt=None, sign=None, make_canaries=None, now=None):
    """Rev 3 #2: the SEAL phase. Runs as zurvaneval (the key owner). Reads the draft bundle from
    --workspace, encrypts the pools, signs the manifest. Runs NO model and reads no corpus.
    stdout: the public manifest. Exit 0 full / 1 short / 2 refusal / 3 internal."""
    paths = paths if paths is not None else SealPaths.real()
    try:
        args = _author_parse(list(argv))
        if "workspace" not in args:
            raise SealRefusal("BAD_ARGS")
        bundle = author_read_bundle(args["workspace"])
        if encrypt is None:
            encrypt = author_age_encrypt(paths)
        if sign is None:
            key = Path(paths.keys_dir) / "signing_ed25519"
            sign = lambda p: author_manifest.sign(p, key)  # noqa: E731
        if make_canaries is None:
            from . import scan as author_scan
            make_canaries = author_scan.make_canaries
        public = seal_suite(bundle, paths, encrypt=encrypt, sign=sign, make_canaries=make_canaries,
                            sequence=int(args.get("sequence", "1")), now=now)
        if "public-out" in args:
            out = Path(args["public-out"])
            out.mkdir(parents=True, exist_ok=True)
            for src in (sealed_store.manifest(paths), sealed_store.manifest_sig(paths)):
                dst = out / src.name
                dst.write_bytes(src.read_bytes())
                os.chmod(str(dst), 0o644)
        sys.stdout.write(author_manifest.dump_manifest(public).decode("utf-8"))
        sys.stdout.flush()
        return 0 if bundle["coverage"]["ok"] else 1
    except SealRefusal as exc:
        _author_emit_refusal(exc.code)
        return 2
    except Exception:  # noqa: BLE001
        _author_errors_log(paths, traceback.format_exc())
        _author_emit_refusal("INTERNAL")
        return 3


def author_main(argv, *, paths=None, runner=None, encrypt=None, sign=None, make_canaries=None, now=None):
    """Dispatcher for bin/zurvan-author. Rev 3 #2 splits authoring across two identities:
    `draft` runs as zurvanauthor (corpus + models, no key), `seal` runs as zurvaneval (key, no model)."""
    argv = list(argv)
    if argv[:1] == ["draft"]:
        return author_draft_main(argv[1:], paths=paths, runner=runner, now=now)
    if argv[:1] == ["seal"]:
        return author_seal_main(argv[1:], paths=paths, encrypt=encrypt, sign=sign,
                                make_canaries=make_canaries, now=now)
    _author_emit_refusal("BAD_ARGS")
    return 2
