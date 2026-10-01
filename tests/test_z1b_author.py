"""Z1b Task 15 tests: sealed authoring (spec s6; contract s3.12). No model is ever called: a scripted fake
ModelRunner stands in for `claude -p`, and the sealed layout is simulated under tmp_path."""
import copy
import hashlib
import json
import os
import random
import re
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.eval_seal import author as A
from scripts.eval_seal import manifest as M
from scripts.eval_seal import spec as S
from scripts.eval_seal import sysenv
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.paths import SealPaths

REPO = Path(__file__).resolve().parents[1]
SPEC = S.load_spec(REPO / "eval" / "z1b" / "spec.json")
STRATA = S.STRATA
BY_ID = {e["id"]: e for e in SPEC["strata"]}
SSH_KEYGEN = "/usr/bin/ssh-keygen"
MODELS = {"author": "opus", "reviewer": "sonnet", "resolver": "haiku"}
TEMPLATES = {"author": "AUTHOR TEMPLATE", "reviewer": "REVIEWER TEMPLATE", "resolver": "RESOLVER TEMPLATE"}
RUBRIC = "PUBLIC RUBRIC TEXT"
DIFFS = ("easy", "medium", "hard")
FENCE = "`" * 3


# ---------------------------------------------------------------------------------------- world builders

def _t15_word(rng):
    cons, vow = "bcdfghjklmnprstvwx", "aeiou"
    return "".join(rng.choice(cons) + rng.choice(vow) for _ in range(rng.randint(3, 5)))


def _t15_query(tag, n_words=9):
    rng = random.Random("t15-query-" + tag)
    return " ".join(_t15_word(rng) for _ in range(n_words))


def _t15_corpus(tmp_path, n_pages=60) -> Path:
    corpus = tmp_path / "corpus"
    (corpus / "wiki" / "notes").mkdir(parents=True)
    (corpus / "docs").mkdir()
    for i in range(n_pages):
        (corpus / "wiki" / "notes" / ("n-%02d.md" % i)).write_text(
            "---\ntitle: Zzt Heading %02d Quorum\n---\n# Zzt Heading %02d Quorum\n\nbody text %d\n" % (i, i, i))
    (corpus / "docs" / "guide.md").write_text("# Guide\n\nplain\n")
    (corpus / "search.sqlite").write_bytes(b"\x00not markdown")
    return corpus


def _t15_target(i, kind="page", value=""):
    return {"page": "wiki/notes/n-%02d.md" % i, "source_id": None, "locator": {"kind": kind, "value": value}}


def _t15_item(stratum="semantic", query=None, targets=None, difficulty="medium", tag="x"):
    grading = BY_ID[stratum]["grading"]
    if targets is None:
        targets = [] if grading == "abstain" else [_t15_target(1)]
    return {
        "id": hashlib.sha256(("t15-" + tag).encode()).hexdigest()[:32], "stratum": stratum,
        "query": query if query is not None else _t15_query(tag), "targets": targets, "grading": grading,
        "difficulty": difficulty, "provenance": {"author": "opus", "reviewer": "sonnet"},
    }


def _t15_world(per=20):
    """Raw author output per stratum plus the truth table (query -> targets) the fake resolver uses."""
    raw, truth = {}, {}
    for si, sid in enumerate(STRATA):
        rows = []
        for i in range(per):
            q = _t15_query("%s-%d" % (sid, i))
            grading = BY_ID[sid]["grading"]
            n_targets = 0 if grading == "abstain" else (2 if grading == "set" else 1)
            targets = [{"page": "wiki/notes/n-%02d.md" % ((si * per + i + k) % 60),
                        "locator": {"kind": "page", "value": ""}} for k in range(n_targets)]
            rows.append({"query": q, "difficulty": DIFFS[i % 3], "targets": targets})
            truth[q] = targets
        raw[sid] = rows
    return {"raw": raw, "truth": truth}


class _T15Fake:
    """Scripted ModelRunner. Hooks may be replaced per test; every call is recorded."""

    def __init__(self, world, *, author=None, reviewer=None, resolver=None):
        self.world, self.calls = world, []
        self.author = author or (lambda sid, prompt: json.dumps({"candidates": world["raw"][sid]}))
        self.reviewer = reviewer or (lambda cands, prompt: json.dumps(
            {"verdicts": [{"n": c["n"], "keep": True, "reason": "fine"} for c in cands]}))
        self.resolver = resolver or self._resolve

    def _resolve(self, query, prompt):
        targets = self.world["truth"].get(query)
        if targets is None:
            return json.dumps({"results": []})
        return json.dumps({"results": [{"page": t["page"], "heading": None} for t in targets]})

    def __call__(self, role, model, prompt, *, cwd):
        self.calls.append((role, model, prompt, Path(cwd)))
        if role == "author":
            return self.author(re.search(r"^STRATUM_ID: (\w+)$", prompt, re.M).group(1), prompt)
        if role == "reviewer":
            return self.reviewer(json.loads(prompt.rsplit("CANDIDATES_JSON: ", 1)[1]), prompt)
        if role == "resolver":
            return self.resolver(prompt.rsplit("QUERY: ", 1)[1], prompt)
        raise AssertionError("unknown role " + role)


def _t15_suite(tmp_path, fake=None, dev=None, per=20, seed=7, **kw):
    corpus = _t15_corpus(tmp_path)
    fake = fake or _T15Fake(_t15_world(per))
    bundle = A.author_suite(SPEC, RUBRIC, dev or [], corpus, fake, MODELS, seed=seed, templates=TEMPLATES,
                            dev_sha256="d" * 64, **kw)
    return corpus, fake, bundle


def _t15_keygen(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    key = d / "signing_ed25519"
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


def _t15_signers(path: Path, key: Path) -> Path:
    pub = (key.parent / (key.name + ".pub")).read_text().split()
    path.write_text('zurvaneval namespaces="zurvan-z1b-manifest" %s %s\n' % (pub[0], pub[1]))
    return path


def _t15_fake_encrypt(plain: bytes, out: Path) -> None:
    Path(out).write_bytes(b"AGE-FAKE" + bytes(b ^ 0x5A for b in plain))


def _t15_fake_decrypt(path: Path) -> bytes:
    raw = Path(path).read_bytes()
    assert raw.startswith(b"AGE-FAKE")
    return bytes(b ^ 0x5A for b in raw[8:])


def _t15_seams(tmp_path):
    key = _t15_keygen(tmp_path / "keysrc")
    signers = _t15_signers(tmp_path / "allowed_signers", key)
    return dict(
        encrypt=_t15_fake_encrypt, sign=lambda p: M.sign(p, key),
        make_canaries=lambda n: ["zc-%024x" % (0xABC0 + i) for i in range(n)]), signers


def _t15_modes(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ------------------------------------------------------------------------------------------------ runner

def test_t15_claude_argv_grants_only_read_grep_glob_and_loads_no_mcp():
    argv = A.author_claude_argv(Path("/Library/ZurvanEval/broker/bin/claude"), "opus",
                                Path("/Library/ZurvanEval/broker/empty-mcp.json"))
    assert argv[0] == "/Library/ZurvanEval/broker/bin/claude" and argv[1] == "-p"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep,Glob"
    denied = set(argv[argv.index("--disallowedTools") + 1].split(","))
    assert {"Bash", "WebFetch", "WebSearch", "Write", "Edit"} <= denied
    assert not denied & {"Read", "Grep", "Glob"}
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == "/Library/ZurvanEval/broker/empty-mcp.json"


def test_t15_claude_argv_refuses_unsafe_model_names_and_relative_binaries():
    for bad in ("opus; rm -rf /", "--dangerously-skip-permissions", "", "a b", "x" * 81):
        with pytest.raises(ValueError):
            A.author_claude_argv(Path("/b/claude"), bad, Path("/b/empty-mcp.json"))
    with pytest.raises(ValueError):
        A.author_claude_argv(Path("claude"), "opus", Path("/b/empty-mcp.json"))


def test_t15_claude_runner_uses_clean_env_pinned_binary_and_corpus_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak-me")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/dev/.claude")
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": "MODEL TEXT", "is_error": False}).encode(),
                                           stderr=b"")

    home = tmp_path / "evalhome"
    broker = tmp_path / "broker"
    runner = A.claude_runner(broker / "bin" / "claude", home, run=fake_run)
    out = runner("author", "opus", "THE PROMPT", cwd=tmp_path / "corpus")
    assert out == "MODEL TEXT"
    kw = seen["kw"]
    assert seen["argv"][0] == str(broker / "bin" / "claude")
    assert seen["argv"][seen["argv"].index("--mcp-config") + 1] == str(broker / "empty-mcp.json")
    assert kw["cwd"] == str(tmp_path / "corpus")
    assert kw["input"] == b"THE PROMPT" and "THE PROMPT" not in " ".join(seen["argv"])
    expect = sysenv.clean_env(home, {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    assert kw["env"] == expect
    assert "ANTHROPIC_API_KEY" not in kw["env"] and kw["env"]["HOME"] == str(home)
    assert "stdin" not in kw                     # the prompt is `input=`, never an inherited stdin
    assert kw["stderr"] in (subprocess.DEVNULL, subprocess.PIPE)


def test_t15_the_two_claude_env_names_are_not_reserved_by_clean_env():
    env = sysenv.clean_env(Path("/h"), {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    assert env["DISABLE_AUTOUPDATER"] == "1" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


@pytest.mark.parametrize("proc", [
    subprocess.CompletedProcess([], 1, stdout=b"{}", stderr=b"SECRET-STDERR"),
    subprocess.CompletedProcess([], 0, stdout=json.dumps({"result": "x", "is_error": True}).encode(), stderr=b""),
])
def test_t15_claude_runner_failures_are_a_bare_internal_refusal(tmp_path, proc):
    runner = A.claude_runner(tmp_path / "bin" / "claude", tmp_path / "h", run=lambda argv, **kw: proc)
    with pytest.raises(SealRefusal) as ei:
        runner("author", "opus", "p", cwd=tmp_path)
    assert ei.value.code == "INTERNAL" and "SECRET" not in str(ei.value)


def test_t15_claude_runner_timeout_and_oserror_become_internal(tmp_path):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    runner = A.claude_runner(tmp_path / "bin" / "claude", tmp_path / "h", run=boom)
    with pytest.raises(SealRefusal) as ei:
        runner("author", "opus", "p", cwd=tmp_path)
    assert ei.value.code == "INTERNAL"


# ---------------------------------------------------------------------------------------------- minhash

def _t15_pair_with_jaccard(target, ngram=5):
    """Two texts whose exact shingle Jaccard is closest to `target` (search over word swaps)."""
    rng = random.Random("pair-%s" % target)
    base_words = [_t15_word(rng) for _ in range(40)]
    a = " ".join(base_words)
    best = None
    for k in range(0, 41):
        words = list(base_words)
        for j in range(k):
            words[j] = _t15_word(rng) + "q"
        b = " ".join(words)
        sa, sb = A.author_shingles(a, ngram), A.author_shingles(b, ngram)
        exact = len(sa & sb) / len(sa | sb)
        if best is None or abs(exact - target) < abs(best[0] - target):
            best = (exact, a, b)
    return best


def test_t15_minhash_detects_jaccard_at_or_above_point_eight_and_not_at_or_below_point_three():
    hi_exact, a1, b1 = _t15_pair_with_jaccard(0.85)
    lo_exact, a2, b2 = _t15_pair_with_jaccard(0.25)
    assert hi_exact >= 0.8 and lo_exact <= 0.3
    est_hi = A.author_jaccard_est(A.author_minhash(a1), A.author_minhash(b1))
    est_lo = A.author_jaccard_est(A.author_minhash(a2), A.author_minhash(b2))
    assert est_hi >= A.AUTHOR_NEARDUP_THRESHOLD and est_lo < A.AUTHOR_NEARDUP_THRESHOLD
    assert abs(est_hi - hi_exact) < 0.15 and abs(est_lo - lo_exact) < 0.15


def test_t15_minhash_classification_is_stable_across_seeds():
    _, a1, b1 = _t15_pair_with_jaccard(0.85)
    _, a2, b2 = _t15_pair_with_jaccard(0.25)
    for seed in range(1, 21):
        hi = A.author_jaccard_est(A.author_minhash(a1, seed=seed), A.author_minhash(b1, seed=seed))
        lo = A.author_jaccard_est(A.author_minhash(a2, seed=seed), A.author_minhash(b2, seed=seed))
        assert hi >= A.AUTHOR_NEARDUP_THRESHOLD, seed
        assert lo < A.AUTHOR_NEARDUP_THRESHOLD, seed


def test_t15_minhash_is_deterministic_normalised_and_total():
    a = A.author_minhash("The  Quick, brown FOX!")
    assert a == A.author_minhash("the quick brown fox")
    assert len(a) == 128 and a != A.author_minhash("the quick brown fox", seed=2)
    assert A.author_jaccard_est(a, a) == 1.0
    assert len(A.author_minhash("")) == 128 and len(A.author_minhash("ab")) == 128
    with pytest.raises(ValueError):
        A.author_jaccard_est(a, A.author_minhash("x", num_perm=64))


# ------------------------------------------------------------------------------------------------ filters

def _t15_filter(cands, dev=None, tmp_path=None):
    corpus = _t15_corpus(tmp_path)
    return A.author_filters(cands, dev or [], corpus, SPEC)


def test_t15_filter_rejects_schema_defects(tmp_path):
    good = _t15_item(tag="g")
    bad = copy.deepcopy(_t15_item(tag="b"))
    del bad["difficulty"]
    extra = dict(_t15_item(tag="e"), notes="x")
    kept, counts = _t15_filter([good, bad, extra], tmp_path=tmp_path)
    assert kept == [good] and counts["bad_schema"] == 2


def test_t15_filter_rejects_targets_missing_from_the_corpus(tmp_path):
    ghost = _t15_item(tag="ghost", targets=[dict(_t15_target(1), page="wiki/notes/nope.md")])
    ok = _t15_item(tag="ok")
    kept, counts = _t15_filter([ghost, ok], tmp_path=tmp_path)
    assert kept == [ok] and counts["target_missing"] == 1


def test_t15_filter_targets_are_store_relative_over_every_indexed_root(tmp_path):
    doc = _t15_item(tag="doc", targets=[dict(_t15_target(1), page="docs/guide.md")])
    wiki_relative = _t15_item(tag="wr", targets=[dict(_t15_target(1), page="notes/n-01.md")])
    kept, counts = _t15_filter([doc, wiki_relative], tmp_path=tmp_path)
    assert [k["id"] for k in kept] == [doc["id"]] and counts["target_missing"] == 1


def test_t15_filter_rejects_exact_duplicates_after_normalisation(tmp_path):
    a = _t15_item(tag="a", query="Alpha  bravo   Charlie delta")
    b = _t15_item(tag="b", query="alpha bravo charlie  DELTA ")
    kept, counts = _t15_filter([a, b], tmp_path=tmp_path)
    assert kept == [a] and counts["exact_dup"] == 1


def test_t15_filter_rejects_high_lexical_overlap(tmp_path):
    words = [_t15_word(random.Random("lex%d" % i)) for i in range(20)]
    a = _t15_item(tag="la", query=" ".join(words))
    b = _t15_item(tag="lb", query=" ".join(reversed(words)) + " extra")     # 20/21 shared tokens, reordered
    kept, counts = _t15_filter([a, b], tmp_path=tmp_path)
    assert kept == [a] and counts["lexical_overlap"] == 1


def _t15_near_pair():
    """17 words, one swapped for a word of the same length: token Jaccard 16/18 stays under the lexical
    threshold while the shingle Jaccard stays well above the MinHash threshold."""
    rng = random.Random("near")
    words = [_t15_word(rng) for _ in range(17)]
    a = " ".join(words)
    words2 = list(words)
    repl = _t15_word(rng)
    while len(repl) != len(words2[5]) or repl == words2[5]:
        repl = _t15_word(rng)
    words2[5] = repl
    return a, " ".join(words2)


def test_t15_filter_rejects_minhash_near_duplicates_between_candidates(tmp_path):
    qa, qb = _t15_near_pair()
    toks_a, toks_b = set(qa.split()), set(qb.split())
    assert len(toks_a & toks_b) / len(toks_a | toks_b) < A.AUTHOR_LEXICAL_THRESHOLD     # lexical alone misses it
    assert A.author_jaccard_est(A.author_minhash(qa), A.author_minhash(qb)) >= A.AUTHOR_NEARDUP_THRESHOLD
    a, b = _t15_item(tag="na", query=qa), _t15_item(tag="nb", query=qb)
    kept, counts = _t15_filter([a, b], tmp_path=tmp_path)
    assert kept == [a] and counts["near_dup"] == 1 and counts["lexical_overlap"] == 0


def test_t15_filter_keeps_unrelated_queries(tmp_path):
    items = [_t15_item(tag="k%d" % i) for i in range(30)]
    kept, counts = _t15_filter(items, tmp_path=tmp_path)
    assert len(kept) == 30 and sum(counts.values()) == 0


def test_t15_filter_rejects_a_candidate_near_identical_to_a_dev_item(tmp_path):
    qa, qb = _t15_near_pair()
    dev = [_t15_item(tag="dev1", query=qa)]
    near = _t15_item(tag="c1", query=qb)
    exact = _t15_item(tag="c2", query=qa)
    fine = _t15_item(tag="c3")
    kept, counts = _t15_filter([near, exact, fine], dev=dev, tmp_path=tmp_path)
    assert kept == [fine] and counts["dev_contamination"] == 2


@pytest.mark.parametrize("leaky", [
    "what does Zzt Heading 07 Quorum say about retries",                 # full title
    "where is wiki/notes/n-07.md described",                              # full page path
    "explain the quorum zzt heading again",                               # >=3 distinctive title tokens
])
def test_t15_filter_rejects_query_target_answer_leakage(tmp_path, leaky):
    item = _t15_item(tag="leak", query=leaky, targets=[_t15_target(7)])
    kept, counts = _t15_filter([item], tmp_path=tmp_path)
    assert kept == [] and counts["answer_leak"] == 1


def test_t15_filter_rejects_filename_stem_and_section_heading_leakage(tmp_path):
    corpus = _t15_corpus(tmp_path)
    (corpus / "wiki" / "notes" / "retry-backoff-policy.md").write_text("# Policy\n\n## Exponential Cooldown Window\n")
    stem = _t15_item(tag="stem", query="tell me about the retry backoff policy please",
                     targets=[dict(_t15_target(1), page="wiki/notes/retry-backoff-policy.md")])
    sect = _t15_item(tag="sect", query="what is the exponential cooldown window here",
                     targets=[dict(_t15_target(1), page="wiki/notes/retry-backoff-policy.md",
                                   locator={"kind": "section", "value": "Exponential Cooldown Window"})])
    kept, counts = A.author_filters([stem, sect], [], corpus, SPEC)
    assert kept == [] and counts["answer_leak"] == 2


def test_t15_leakage_check_does_not_reject_a_query_sharing_one_title_word(tmp_path):
    item = _t15_item(tag="ok1", query="how are quorum decisions settled across projects", targets=[_t15_target(7)])
    kept, counts = _t15_filter([item], tmp_path=tmp_path)
    assert kept == [item] and counts["answer_leak"] == 0


def test_t15_filter_does_not_mutate_inputs_and_reports_every_reason_key(tmp_path):
    items = [_t15_item(tag="m%d" % i) for i in range(3)]
    before = copy.deepcopy(items)
    _, counts = _t15_filter(items, tmp_path=tmp_path)
    assert items == before
    assert set(counts) == set(A.AUTHOR_REJECT_REASONS)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k) for k in counts)


# ------------------------------------------------------------------------------------------------ select

def _t15_pool(counts):
    """counts: {stratum: {difficulty: n}} -> accepted items."""
    out = []
    for sid, per in counts.items():
        for d, n in per.items():
            for i in range(n):
                out.append(_t15_item(stratum=sid, tag="%s-%s-%d" % (sid, d, i), difficulty=d))
    return out


def test_t15_select_takes_ten_then_five_per_stratum_balanced_by_difficulty():
    pool = _t15_pool({sid: {"easy": 12, "medium": 12, "hard": 12} for sid in STRATA})
    holdout, reserve, short = A.author_select(pool, SPEC, seed=3)
    assert len(holdout) == 160 and len(reserve) == 80 and short == []
    assert not {i["id"] for i in holdout} & {i["id"] for i in reserve}
    ex_h = [i for i in holdout if i["stratum"] == "exact_identifier"]
    assert sorted(i["difficulty"] for i in ex_h) == ["easy"] * 3 + ["hard"] * 2 + ["medium"] * 5
    ex_r = [i for i in reserve if i["stratum"] == "exact_identifier"]
    assert sorted(i["difficulty"] for i in ex_r) == ["easy"] * 2 + ["hard"] * 1 + ["medium"] * 2
    con_h = [i for i in holdout if i["stratum"] == "contradiction"]
    assert sorted(i["difficulty"] for i in con_h) == ["easy"] * 1 + ["hard"] * 5 + ["medium"] * 4


def test_t15_select_is_deterministic_for_a_seed_and_independent_of_input_order():
    pool = _t15_pool({sid: {"easy": 8, "medium": 8, "hard": 8} for sid in STRATA})
    h1, r1, s1 = A.author_select(pool, SPEC, seed=11)
    h2, r2, s2 = A.author_select(list(reversed(pool)), SPEC, seed=11)
    assert [i["id"] for i in h1] == [i["id"] for i in h2] and [i["id"] for i in r1] == [i["id"] for i in r2]
    h3, _, _ = A.author_select(pool, SPEC, seed=12)
    assert [i["id"] for i in h1] != [i["id"] for i in h3]


@pytest.mark.parametrize("have,hold,res", [(7, 7, 0), (10, 10, 0), (12, 10, 2), (15, 10, 5), (40, 10, 5), (0, 0, 0)])
def test_t15_select_fills_holdout_first_and_never_pads(have, hold, res):
    pool = _t15_pool({"hash": {"easy": have}}) if have else []
    holdout, reserve, short = A.author_select(pool, SPEC, seed=1)
    assert len([i for i in holdout if i["stratum"] == "hash"]) == hold
    assert len([i for i in reserve if i["stratum"] == "hash"]) == res
    rows = [r for r in short if r["stratum"] == "hash"]
    assert ({"pool": "holdout", "stratum": "hash", "have": hold, "need": 10} in rows) == (hold < 10)
    assert ({"pool": "reserve", "stratum": "hash", "have": res, "need": 5} in rows) == (res < 5)
    assert all(set(r) == {"pool", "stratum", "have", "need"} for r in short)
    assert all(r["stratum"] in STRATA for r in short)


def test_t15_select_reports_every_empty_stratum_short_in_both_pools():
    holdout, reserve, short = A.author_select([], SPEC, seed=1)
    assert holdout == [] and reserve == [] and len(short) == 32


def test_t15_select_fills_from_other_difficulties_when_one_bucket_is_empty():
    pool = _t15_pool({"hash": {"easy": 20, "medium": 20}})
    holdout, reserve, short = A.author_select(pool, SPEC, seed=1)
    assert len(holdout) == 10 and len(reserve) == 5 and short == [{"pool": p, "stratum": s, "have": 0, "need": n}
                                                                    for s in STRATA if s != "hash"
                                                                    for p, n in (("holdout", 10), ("reserve", 5))]


def test_t15_select_does_not_mutate_its_input():
    pool = _t15_pool({"hash": {"easy": 6, "medium": 6, "hard": 6}})
    before = copy.deepcopy(pool)
    A.author_select(pool, SPEC, seed=5)
    assert pool == before


# ------------------------------------------------------------------------------------------ author_suite

def test_t15_full_pipeline_with_a_fake_runner_fills_every_stratum(tmp_path):
    corpus, fake, bundle = _t15_suite(tmp_path)
    assert bundle["short"] == [] and bundle["coverage"]["ok"] is True
    assert len(bundle["holdout"]) == 160 and len(bundle["reserve"]) == 80
    assert bundle["accepted"] == 320
    assert bundle["models"] == MODELS
    for item in bundle["holdout"] + bundle["reserve"]:
        S.validate_item(item, SPEC)
        assert re.fullmatch(r"[0-9a-f]{32}", item["id"])
        assert item["provenance"] == {"author": "opus", "reviewer": "sonnet"}
    roles = [c[0] for c in fake.calls]
    assert roles.count("author") == 16 and roles.count("resolver") == 320 and roles.count("reviewer") >= 16
    assert all(c[3] == corpus for c in fake.calls)
    assert {c[0]: c[1] for c in fake.calls} == MODELS


def test_t15_opaque_ids_are_random_not_content_hashes(tmp_path):
    _, _, b1 = _t15_suite(tmp_path / "one")
    _, _, b2 = _t15_suite(tmp_path / "two")
    ids1, ids2 = {i["id"] for i in b1["holdout"]}, {i["id"] for i in b2["holdout"]}
    assert not ids1 & ids2
    for item in b1["holdout"][:20]:
        for blob in (item["query"], json.dumps(item["targets"], sort_keys=True)):
            assert item["id"] != hashlib.sha256(blob.encode()).hexdigest()[:32]


def test_t15_author_prompts_carry_no_dev_text_and_the_reviewer_is_a_separate_call(tmp_path):
    dev = [_t15_item(tag="dev-%d" % i, query=_t15_query("devq-%d" % i)) for i in range(5)]
    _, fake, bundle = _t15_suite(tmp_path, dev=dev)
    for role, model, prompt, cwd in fake.calls:
        for d in dev:
            assert d["query"] not in prompt
        if role == "author":
            assert TEMPLATES["author"] in prompt and RUBRIC in prompt
        if role == "reviewer":
            assert TEMPLATES["reviewer"] in prompt and model == "sonnet" and model != "opus"
    reviewer_prompts = [c[2] for c in fake.calls if c[0] == "reviewer"]
    assert len(set(reviewer_prompts)) == len(reviewer_prompts)


def test_t15_reviewer_rejection_drops_items(tmp_path):
    world = _t15_world()
    dropped = {r["query"] for r in world["raw"]["hash"][:8]}

    def reviewer(cands, prompt):
        return json.dumps({"verdicts": [{"n": c["n"], "keep": c["query"] not in dropped,
                                         "reason": "SENTINEL-WHY"} for c in cands]})

    _, fake, bundle = _t15_suite(tmp_path, fake=_T15Fake(world, reviewer=reviewer))
    kept_queries = {i["query"] for i in bundle["holdout"] + bundle["reserve"]}
    assert not kept_queries & dropped
    assert bundle["rejected"]["reviewer_rejected"] == 8 and bundle["accepted"] == 312
    assert len([i for i in bundle["holdout"] if i["stratum"] == "hash"]) == 10
    assert "SENTINEL-WHY" not in json.dumps(bundle["rejected"])


def test_t15_reviewer_silence_or_garbage_fails_closed(tmp_path):
    world = _t15_world()
    silent = _T15Fake(world, reviewer=lambda cands, prompt: json.dumps(
        {"verdicts": [{"n": c["n"], "keep": True} for c in cands if c["n"] % 2 == 0]}))
    _, _, b1 = _t15_suite(tmp_path / "a", fake=silent)
    assert b1["rejected"]["reviewer_rejected"] > 0 and b1["accepted"] < 320
    garbage = _T15Fake(world, reviewer=lambda cands, prompt: "I think these are all fine!")
    _, _, b2 = _t15_suite(tmp_path / "b", fake=garbage)
    assert b2["accepted"] == 0 and b2["coverage"]["ok"] is False
    truthy_string = _T15Fake(world, reviewer=lambda cands, prompt: json.dumps(
        {"verdicts": [{"n": c["n"], "keep": "yes"} for c in cands]}))
    _, _, b3 = _t15_suite(tmp_path / "c", fake=truthy_string)
    assert b3["accepted"] == 0


def test_t15_resolver_prompt_contains_only_the_query_never_a_target(tmp_path):
    _, fake, bundle = _t15_suite(tmp_path)
    prompts = [c for c in fake.calls if c[0] == "resolver"]
    assert prompts
    all_queries = {q for q in _t15_world()["truth"]}
    for role, model, prompt, cwd in prompts:
        assert model == "haiku"
        assert prompt.startswith(TEMPLATES["resolver"]) and RUBRIC not in prompt
        query = prompt.rsplit("QUERY: ", 1)[1]
        assert query in all_queries
        assert "wiki/" not in prompt and "docs/" not in prompt and ".md" not in prompt
        assert not [q for q in all_queries if q != query and q in prompt]
        assert "locator" not in prompt and "targets" not in prompt
    for item in bundle["holdout"]:
        assert item["id"] not in "".join(c[2] for c in prompts)


def test_t15_resolve_miss_drops_the_item(tmp_path):
    world = _t15_world()
    missed = {r["query"] for r in world["raw"]["code"][:5]}

    def resolver(query, prompt):
        if query in missed:
            return json.dumps({"results": [{"page": "wiki/notes/n-59.md", "heading": None}]})
        return _T15Fake._resolve(fk, query, prompt)

    fk = _T15Fake(world)
    fk.resolver = resolver
    _, _, bundle = _t15_suite(tmp_path, fake=fk)
    assert bundle["rejected"]["resolve_miss"] == 5 and bundle["accepted"] == 315
    assert not {i["query"] for i in bundle["holdout"] + bundle["reserve"]} & missed


def test_t15_resolver_hit_on_any_target_accepts_and_section_locators_need_the_heading(tmp_path):
    world = _t15_world(per=20)
    corpus = _t15_corpus(tmp_path)
    (corpus / "wiki" / "notes" / "n-00.md").write_text("# Zzt Heading 00 Quorum\n\n## Amber Gate Section\n")
    sect = [{"page": "wiki/notes/n-00.md", "locator": {"kind": "section", "value": "Amber Gate Section"}}]
    world["raw"]["hash"][0]["targets"] = sect
    world["truth"][world["raw"]["hash"][0]["query"]] = sect
    wrong_heading = _T15Fake(world, resolver=lambda q, p: json.dumps(
        {"results": [{"page": "wiki/notes/n-00.md", "heading": "Other"}]} if q == world["raw"]["hash"][0]["query"]
        else {"results": [{"page": t["page"], "heading": None} for t in world["truth"][q]]}))
    b = A.author_suite(SPEC, RUBRIC, [], corpus, wrong_heading, MODELS, seed=1, templates=TEMPLATES, dev_sha256="d" * 64)
    assert b["rejected"]["resolve_miss"] == 1
    right = _T15Fake(world, resolver=lambda q, p: json.dumps(
        {"results": [{"page": "wiki/notes/n-00.md", "heading": "Amber Gate Section"}]} if q == world["raw"]["hash"][0]["query"]
        else {"results": [{"page": t["page"], "heading": None} for t in world["truth"][q]]}))
    b2 = A.author_suite(SPEC, RUBRIC, [], corpus, right, MODELS, seed=1, templates=TEMPLATES, dev_sha256="d" * 64)
    assert b2["rejected"]["resolve_miss"] == 0


def test_t15_not_found_items_are_accepted_only_when_the_resolver_finds_nothing(tmp_path):
    world = _t15_world()
    nf = world["raw"]["not_found"]
    bad_q = nf[0]["query"]

    def resolver(query, prompt):
        if query == bad_q:
            return json.dumps({"results": [{"page": "wiki/notes/n-03.md", "heading": None}]})
        return json.dumps({"results": []}) if query in {r["query"] for r in nf} else _T15Fake._resolve(fk, query, prompt)

    fk = _T15Fake(world)
    fk.resolver = resolver
    _, _, bundle = _t15_suite(tmp_path, fake=fk)
    assert bundle["rejected"]["resolve_miss"] == 1
    assert len([i for i in bundle["holdout"] if i["stratum"] == "not_found"]) == 10


def test_t15_a_stratum_with_zero_survivors_yields_coverage_not_ok(tmp_path):
    world = _t15_world()
    fk = _T15Fake(world, author=lambda sid, prompt: json.dumps(
        {"candidates": [] if sid == "temporal" else world["raw"][sid]}))
    _, _, bundle = _t15_suite(tmp_path, fake=fk)
    assert bundle["coverage"]["ok"] is False
    assert {"pool": "holdout", "stratum": "temporal", "have": 0, "need": 10} in bundle["short"]
    assert {"pool": "reserve", "stratum": "temporal", "have": 0, "need": 5} in bundle["short"]
    assert len(bundle["holdout"]) == 150      # every other stratum is full: short is reported, never padded


def test_t15_unparseable_author_output_is_counted_not_fatal(tmp_path):
    world = _t15_world()
    fk = _T15Fake(world, author=lambda sid, prompt: "sorry, no JSON here" if sid == "alias" else json.dumps(
        {"candidates": world["raw"][sid]}))
    _, _, bundle = _t15_suite(tmp_path, fake=fk)
    assert bundle["rejected"]["unparseable"] == 1 and bundle["coverage"]["ok"] is False


def test_t15_author_output_may_be_fenced_or_a_bare_list(tmp_path):
    world = _t15_world()

    def author(sid, prompt):
        body = json.dumps(world["raw"][sid])
        return (FENCE + "json\n" + body + "\n" + FENCE) if len(sid) % 2 else ("Here you go:\n" + body)

    _, _, bundle = _t15_suite(tmp_path, fake=_T15Fake(world, author=author))
    assert bundle["accepted"] == 320


def test_t15_forced_stratum_and_grading_cannot_be_chosen_by_the_author(tmp_path):
    world = _t15_world()
    rows = copy.deepcopy(world["raw"]["hash"])
    for r in rows:
        r.update(stratum="not_found", grading="abstain", id="f" * 32, provenance={"author": "x", "reviewer": "y"})
    fk = _T15Fake(world, author=lambda sid, prompt: json.dumps({"candidates": rows if sid == "hash" else world["raw"][sid]}))
    _, _, bundle = _t15_suite(tmp_path, fake=fk)
    hashes = [i for i in bundle["holdout"] + bundle["reserve"] if i["stratum"] == "hash"]
    assert len(hashes) == 15 and all(i["grading"] == "rank" and i["id"] != "f" * 32 for i in hashes)
    assert all(i["provenance"] == {"author": "opus", "reviewer": "sonnet"} for i in hashes)


def test_t15_prompt_digest_changes_with_any_template_or_rubric_byte(tmp_path):
    d0 = A.author_prompt_digest(RUBRIC, TEMPLATES)
    assert re.fullmatch(r"[0-9a-f]{64}", d0)
    assert A.author_prompt_digest(RUBRIC + "!", TEMPLATES) != d0
    for role in TEMPLATES:
        t = dict(TEMPLATES, **{role: TEMPLATES[role] + " "})
        assert A.author_prompt_digest(RUBRIC, t) != d0


def test_t15_audit_files_hold_prompts_and_raw_outputs_at_0600(tmp_path):
    audit = tmp_path / "authoring" / "run1"
    _, fake, _ = _t15_suite(tmp_path, per=20, audit_dir=audit)
    files = sorted(audit.iterdir())
    assert len(files) == 2 * len(fake.calls)
    assert all(_t15_modes(f) == 0o600 for f in files) and _t15_modes(audit) == 0o700
    blob = b"".join(f.read_bytes() for f in files)
    assert TEMPLATES["author"].encode() in blob and b"QUERY: " in blob


def test_t15_author_suite_validates_models_and_templates(tmp_path):
    corpus = _t15_corpus(tmp_path)
    fk = _T15Fake(_t15_world())
    with pytest.raises(ValueError):
        A.author_suite(SPEC, RUBRIC, [], corpus, fk, {"author": "opus"}, seed=1, templates=TEMPLATES, dev_sha256="d" * 64)
    with pytest.raises(ValueError):
        A.author_suite(SPEC, RUBRIC, [], corpus, fk, MODELS, seed=1, templates={"author": "x"}, dev_sha256="d" * 64)
    with pytest.raises(ValueError):
        A.author_suite(SPEC, RUBRIC, [], corpus, fk, dict(MODELS, reviewer="bad model;"), seed=1,
                       templates=TEMPLATES, dev_sha256="d" * 64)


# --------------------------------------------------------------------------------------------- seal_suite

def _t15_paths(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    for d in (p.sets_dir, p.keys_dir, p.runs_dir, p.corpus_dir):
        d.mkdir(parents=True, exist_ok=True)
    return p


def test_t15_seal_suite_writes_ciphertext_a_signed_manifest_and_counts_only(tmp_path):
    paths = _t15_paths(tmp_path)
    corpus, fake, bundle = _t15_suite(tmp_path)
    seams, signers = _t15_seams(tmp_path)
    pub = A.seal_suite(bundle, paths, now=lambda: "2026-10-02T10:00:00Z", **seams)
    for name in ("holdout.age", "reserve.age", "canaries.age"):
        f = paths.sets_dir / name
        assert f.is_file() and _t15_modes(f) == 0o600
    assert _t15_modes(paths.manifest_file) == 0o600
    cipher = (paths.sets_dir / "holdout.age").read_bytes()
    for item in bundle["holdout"][:10]:
        assert item["query"].encode() not in cipher
    dec = [json.loads(ln) for ln in _t15_fake_decrypt(paths.sets_dir / "holdout.age").decode().splitlines()]
    assert sorted(i["id"] for i in dec) == sorted(i["id"] for i in bundle["holdout"])
    sealed = M.verify(paths.manifest_file, Path(str(paths.manifest_file) + ".sig"), signers)
    assert sealed == pub
    M.assert_no_per_item_digests(pub)
    assert pub["holdout_age_sha256"] == hashlib.sha256(cipher).hexdigest()
    assert pub["reserve_age_sha256"] == hashlib.sha256((paths.sets_dir / "reserve.age").read_bytes()).hexdigest()
    assert pub["dev_sha256"] == "d" * 64
    assert pub["corpus_sha256"] == bundle["corpus_sha256"]
    assert pub["strata_counts"]["holdout"] == {s: 10 for s in STRATA}
    assert pub["strata_counts"]["reserve"] == {s: 5 for s in STRATA}
    assert pub["accepted"] == 320 and pub["short"] == [] and pub["sequence"] == 1
    assert pub["models"] == MODELS and pub["prompt_sha256"] == bundle["prompt_sha256"]
    assert pub["burn_ledger"] == [] and re.fullmatch(r"[0-9a-f]{16}", pub["suite_id"])
    dump = json.dumps(pub)
    for item in bundle["holdout"] + bundle["reserve"]:
        assert item["query"] not in dump and item["id"] not in dump


def test_t15_seal_suite_records_short_strata_in_the_manifest(tmp_path):
    world = _t15_world()
    fk = _T15Fake(world, author=lambda sid, prompt: json.dumps(
        {"candidates": [] if sid == "temporal" else world["raw"][sid]}))
    paths = _t15_paths(tmp_path)
    _, _, bundle = _t15_suite(tmp_path, fake=fk)
    seams, _ = _t15_seams(tmp_path)
    pub = A.seal_suite(bundle, paths, **seams)
    assert {"pool": "holdout", "stratum": "temporal", "have": 0, "need": 10} in pub["short"]
    assert pub["strata_counts"]["holdout"]["temporal"] == 0


def test_t15_seal_suite_calls_make_canaries_with_a_count_and_encrypts_them(tmp_path):
    paths = _t15_paths(tmp_path)
    _, _, bundle = _t15_suite(tmp_path)
    seams, _ = _t15_seams(tmp_path)
    asked = []

    def make(n):
        asked.append(n)
        return ["zc-%024x" % (0xF00 + i) for i in range(n)]

    A.seal_suite(bundle, paths, **dict(seams, make_canaries=make))
    assert asked == [A.AUTHOR_CANARIES]
    plain = _t15_fake_decrypt(paths.sets_dir / "canaries.age").decode().split()
    assert len(plain) == A.AUTHOR_CANARIES and len(set(plain)) == len(plain)


def test_t15_seal_suite_refuses_to_overwrite_an_existing_sealed_suite(tmp_path):
    paths = _t15_paths(tmp_path)
    _, _, bundle = _t15_suite(tmp_path)
    seams, _ = _t15_seams(tmp_path)
    A.seal_suite(bundle, paths, **seams)
    before = {f.name: f.read_bytes() for f in paths.sets_dir.iterdir()}
    mf = paths.manifest_file.read_bytes()
    with pytest.raises(SealRefusal) as ei:
        A.seal_suite(bundle, paths, **seams)
    assert ei.value.code == "BAD_ARGS"
    assert {f.name: f.read_bytes() for f in paths.sets_dir.iterdir()} == before
    assert paths.manifest_file.read_bytes() == mf


def test_t15_seal_suite_requires_a_dev_digest_and_valid_canaries_and_working_encrypt(tmp_path):
    paths = _t15_paths(tmp_path)
    _, _, bundle = _t15_suite(tmp_path)
    seams, _ = _t15_seams(tmp_path)
    with pytest.raises(SealRefusal):
        A.seal_suite(dict(bundle, dev_sha256=None), paths, **seams)
    with pytest.raises(SealRefusal):
        A.seal_suite(bundle, paths, **dict(seams, make_canaries=lambda n: ["same"] * n))
    with pytest.raises(SealRefusal):
        A.seal_suite(bundle, paths, **dict(seams, encrypt=lambda plain, out: None))     # wrote nothing
    assert not paths.manifest_file.exists()


# ------------------------------------------------------------------------------------------- author_main

def _t15_argv(tmp_path, paths, extra=()):
    dev = paths.sets_dir / "dev-ref.jsonl"
    dev.write_text("".join(json.dumps(_t15_item(tag="dev%d" % i, query=_t15_query("devfile%d" % i))) + "\n"
                           for i in range(4)))
    return ["--spec", str(REPO / "eval" / "z1b" / "spec.json"), "--rubric", str(REPO / "eval" / "z1b" / "rubric.md"),
            "--prompts", str(REPO / "eval" / "z1b" / "prompts"), "--seed", "5", *extra]


def _t15_run_main(tmp_path, fake, extra=(), **over):
    paths = _t15_paths(tmp_path)
    corpus = _t15_corpus(tmp_path / "c")
    for f in corpus.rglob("*"):
        if f.is_file():
            dest = paths.corpus_dir / f.relative_to(corpus)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.read_bytes())
    seams, signers = _t15_seams(tmp_path)
    kw = dict(paths=paths, runner=fake, **seams)
    kw.update(over)
    return paths, signers, A.author_main(_t15_argv(tmp_path, paths, extra), **kw)


def test_t15_author_main_prints_only_the_public_manifest_and_nothing_sealed(tmp_path, capsys):
    world = _t15_world()
    sentinel_q = world["raw"]["hash"][0]["query"]
    fk = _T15Fake(world, reviewer=lambda cands, prompt: json.dumps(
        {"verdicts": [{"n": c["n"], "keep": True, "reason": "SENTINEL-REVIEW-REASON-77"} for c in cands]}))
    paths, signers, rc = _t15_run_main(tmp_path, fk)
    cap = capsys.readouterr()
    assert rc == 0 and cap.err == ""
    out = json.loads(cap.out)
    assert out == M.verify(paths.manifest_file, Path(str(paths.manifest_file) + ".sig"), signers)
    assert out["strata_counts"]["holdout"] == {s: 10 for s in STRATA}
    haystack = cap.out + cap.err
    for q in world["truth"]:
        assert q not in haystack
    for tag in ("SENTINEL-REVIEW-REASON-77", "wiki/notes", "n-0", "Zzt Heading", sentinel_q):
        assert tag not in haystack
    assert len(cap.out.splitlines()) == 1


def test_t15_author_main_keeps_prompts_and_raw_outputs_only_under_the_eval_home_at_0600(tmp_path):
    fk = _T15Fake(_t15_world())
    paths, _, rc = _t15_run_main(tmp_path, fk)
    assert rc == 0
    runs = paths.runs_dir / "authoring"
    dirs = list(runs.iterdir())
    assert len(dirs) == 1 and _t15_modes(dirs[0]) == 0o700 and _t15_modes(runs) == 0o700
    files = list(dirs[0].iterdir())
    assert len(files) == 2 * len(fk.calls) and all(_t15_modes(f) == 0o600 for f in files)
    for f in paths.sets_dir.glob("*.age"):
        assert _t15_modes(f) == 0o600
    root = tmp_path / "root"
    allowed = (root / "Library" / "ZurvanEval" / "corpus", paths.sets_dir, paths.runs_dir)
    for f in (p for p in root.rglob("*") if p.is_file()):
        assert any(a in f.parents for a in allowed) or f.parent == paths.eval_home, f
        if f.parent == paths.eval_home:
            assert f.name in ("manifest.json", "manifest.json.sig")


def test_t15_author_main_exit_code_is_nonzero_when_a_stratum_is_short(tmp_path, capsys):
    world = _t15_world()
    fk = _T15Fake(world, author=lambda sid, prompt: json.dumps(
        {"candidates": [] if sid == "path" else world["raw"][sid]}))
    paths, _, rc = _t15_run_main(tmp_path, fk)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and {"pool": "holdout", "stratum": "path", "have": 0, "need": 10} in out["short"]


def test_t15_author_main_refusals_print_only_a_code_and_never_echo_arguments(tmp_path, capsys):
    paths = _t15_paths(tmp_path)
    rc = A.author_main(["--spec", "x", "--evil-flag-SENTINEL-ARG", "1"], paths=paths, runner=_T15Fake(_t15_world()))
    cap = capsys.readouterr()
    assert rc == 2 and cap.err == ""
    assert json.loads(cap.out) == {"ok": False, "code": "BAD_ARGS"}
    assert "SENTINEL" not in cap.out


@pytest.mark.parametrize("argv", [
    [], ["--spec"], ["--spec", "a", "--spec", "b"], ["--spec", "a", "--rubric", "b"],
    ["--seed", "notint"], ["--author-model", "bad model"], ["positional"],
])
def test_t15_author_main_rejects_malformed_argv(tmp_path, capsys, argv):
    paths = _t15_paths(tmp_path)
    rc = A.author_main(argv, paths=paths, runner=_T15Fake(_t15_world()))
    assert rc == 2 and json.loads(capsys.readouterr().out)["code"] == "BAD_ARGS"


def test_t15_author_main_requires_distinct_author_and_reviewer_models(tmp_path, capsys):
    fk = _T15Fake(_t15_world())
    paths, _, rc = _t15_run_main(tmp_path, fk, extra=("--author-model", "opus", "--reviewer-model", "opus"))
    assert rc == 2 and json.loads(capsys.readouterr().out)["code"] == "BAD_ARGS" and fk.calls == []


def test_t15_author_main_missing_dev_ref_is_refused_before_any_model_call(tmp_path, capsys):
    fk = _T15Fake(_t15_world())
    paths = _t15_paths(tmp_path)
    seams, _ = _t15_seams(tmp_path)
    argv = ["--spec", str(REPO / "eval" / "z1b" / "spec.json"), "--rubric", str(REPO / "eval" / "z1b" / "rubric.md"),
            "--prompts", str(REPO / "eval" / "z1b" / "prompts")]
    rc = A.author_main(argv, paths=paths, runner=fk, **seams)
    assert rc == 2 and json.loads(capsys.readouterr().out)["code"] == "BAD_ARGS" and fk.calls == []


def test_t15_author_main_internal_errors_go_to_the_sealed_log_not_the_streams(tmp_path, capsys):
    def boom(role, model, prompt, *, cwd):
        raise RuntimeError("SENTINEL-EXC-TEXT with query " + prompt[-30:])

    paths, _, rc = _t15_run_main(tmp_path, boom)
    cap = capsys.readouterr()
    assert rc == 3 and cap.err == "" and json.loads(cap.out) == {"ok": False, "code": "INTERNAL"}
    assert "SENTINEL" not in cap.out
    log = paths.runs_dir / "errors.log"
    assert log.is_file() and _t15_modes(log) == 0o600 and b"SENTINEL-EXC-TEXT" in log.read_bytes()
    assert not (paths.sets_dir / "holdout.age").exists() and not paths.manifest_file.exists()


def test_t15_author_main_public_out_copy_verifies_byte_for_byte(tmp_path, capsys):
    fk = _T15Fake(_t15_world())
    pub = tmp_path / "public"
    paths, signers, rc = _t15_run_main(tmp_path, fk, extra=("--public-out", str(pub)))
    assert rc == 0
    assert (pub / "manifest.json").read_bytes() == paths.manifest_file.read_bytes()
    M.verify(pub / "manifest.json", pub / "manifest.json.sig", signers)
    assert _t15_modes(pub / "manifest.json") == 0o644


def test_t15_author_main_dev_digest_is_the_whole_file_digest(tmp_path, capsys):
    fk = _T15Fake(_t15_world())
    paths, _, rc = _t15_run_main(tmp_path, fk)
    out = json.loads(capsys.readouterr().out)
    assert out["dev_sha256"] == hashlib.sha256((paths.sets_dir / "dev-ref.jsonl").read_bytes()).hexdigest()


def test_t15_the_wrapper_entry_point_is_author_main_not_main():
    assert callable(A.author_main) and not hasattr(A, "main")


def test_t15_production_runner_binary_is_the_pinned_broker_copy():
    p = SealPaths.real()
    assert A.author_default_claude(p) == p.broker_bin / "claude"
    assert ".local" not in str(A.author_default_claude(p))


# ------------------------------------------------------------------------------------- tracked prompts

@pytest.mark.parametrize("name", ["author", "reviewer", "resolver", "dev-author"])
def test_t15_tracked_prompts_are_public_and_content_free(name):
    text = (REPO / "eval" / "z1b" / "prompts" / (name + ".md")).read_text()
    assert len(text) > 400 and FENCE not in text
    assert not re.search(r"wiki/[a-z0-9_-]+/[a-z0-9_-]+\.md", text)       # no concrete page names
    assert not re.search(r"[0-9a-f]{32,}", text)                          # no digests or ids


def test_t15_tracked_rubric_names_every_stratum_and_the_leak_rules():
    text = (REPO / "eval" / "z1b" / "rubric.md").read_text()
    for sid in STRATA:
        assert sid in text
    assert FENCE not in text and "leak" in text.lower() and "ground" in text.lower()


def test_t15_the_role_prompts_state_their_output_format_and_the_resolver_stays_blind():
    root = REPO / "eval" / "z1b" / "prompts"
    assert "candidates" in (root / "author.md").read_text()
    assert "verdicts" in (root / "reviewer.md").read_text()
    resolver = (root / "resolver.md").read_text()
    assert "results" in resolver and "QUERY" in resolver
    assert "target" not in resolver.lower().replace("targeted", "")


def test_t15_templates_load_from_the_prompts_dir_and_refuse_missing_files(tmp_path):
    t = A.author_load_templates(REPO / "eval" / "z1b" / "prompts")
    assert set(t) == {"author", "reviewer", "resolver"} and all(t.values())
    with pytest.raises(SealRefusal) as ei:
        A.author_load_templates(tmp_path)
    assert ei.value.code == "BAD_ARGS"


# ------------------------------------------------------------------------------------- real age binary

@pytest.mark.skipif(not (os.path.exists("/opt/homebrew/bin/age") and os.path.exists("/opt/homebrew/bin/age-keygen")),
                    reason="age is not installed on this machine")
def test_t15_production_encrypt_round_trips_with_the_real_age_binary(tmp_path):
    paths = SealPaths.for_root(tmp_path / "root")
    paths.broker_bin.mkdir(parents=True)
    paths.keys_dir.mkdir(parents=True)
    (paths.broker_bin / "age").symlink_to("/opt/homebrew/bin/age")
    subprocess.run(["/opt/homebrew/bin/age-keygen", "-o", str(paths.keys_dir / "age.key")], check=True,
                   capture_output=True)
    paths.eval_home.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "set.age"
    A.author_age_encrypt(paths)(b"sealed query text\n", out)
    assert out.is_file() and b"sealed query text" not in out.read_bytes()
    plain = subprocess.run(["/opt/homebrew/bin/age", "-d", "-i", str(paths.keys_dir / "age.key"), str(out)],
                           check=True, capture_output=True).stdout
    assert plain == b"sealed query text\n"


def test_t15_production_encrypt_failure_is_a_bare_internal_refusal(tmp_path):
    paths = SealPaths.for_root(tmp_path / "root")           # no age binary, no key: nothing to run
    with pytest.raises(SealRefusal) as ei:
        A.author_age_encrypt(paths)(b"x", tmp_path / "o.age")
    assert ei.value.code == "INTERNAL"
