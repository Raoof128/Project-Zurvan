"""Z1b Task 11 tests: leak canaries, fingerprint scan, burn, replacement and reseal (contract s3.10, addendum B).

The end-to-end tests drive broker_main with the REAL scan_run / reseal_after_burn on top of the Task 10
fixtures (tests/test_z1b_broker.py): real ledger, real signed manifest, real git bundles, a real python
candidate. Developer artifacts are a tmp "dev home". Nothing outside tmp_path is touched.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.eval_seal import broker as B
from scripts.eval_seal import manifest as manifest_mod
from scripts.eval_seal import scan as S
from scripts.eval_seal import sealed_store
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.ledger import Ledger
from scripts.eval_seal.paths import SealPaths
from scripts.eval_seal.spec import load_spec

from test_z1b_broker import (SPEC, _t10_argv, _t10_build, _t10_call, _t10_commit, _t10_ctx, _t10_drop,
                             _t10_item, _t10_kinds, _t10_ledger, _t10_oracle, _t10_refused, _t10_run,
                             _t10_seal)

ROOT = Path(__file__).resolve().parent.parent
CLT_PY = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"


def _t11_long_query(tag):
    # 100+ bytes of distinctive prose: a verbatim copy of any 64+ byte span is detectable
    return ("What did the committee decide about %s when the migration plan was reviewed in the autumn, "
            "and which rollback criteria were attached to that decision?" % tag)


def _t11_build(tmp_path, **kw):
    E = _t10_build(tmp_path, **kw)
    for n, item in enumerate(E.holdout):
        item["query"] = _t11_long_query("holdout-topic-%02d-%s" % (n, item["id"][:8]))
    for n, item in enumerate(E.reserve):
        item["query"] = _t11_long_query("reserve-topic-%02d-%s" % (n, item["id"][:8]))
    E.cand_cfg["oracle"] = _t10_oracle(E.holdout + E.reserve)
    E.manifest = _t10_seal(E)
    E.dev = tmp_path / "devhome"
    E.devrepo = tmp_path / "devrepo"
    (E.dev / ".claude" / "projects" / "-Users-x-Zurvan").mkdir(parents=True)
    E.devrepo.mkdir()
    E.paths.scan_targets_file.write_text(json.dumps({"dev_home": str(E.dev), "repo": str(E.devrepo)}))
    E.canaries = S.make_canaries(3)
    E.canary_bound = [E.holdout[0]["id"], E.holdout[1]["id"], None]
    _t11_write_canaries(E)
    E.trace = E.dev / ".claude" / "projects" / "-Users-x-Zurvan" / "session.jsonl"
    E.trace.write_text("an ordinary transcript line\n" * 20)
    return E


def _t11_write_canaries(E):
    lines = [json.dumps({"token": t, "item_id": i}) for t, i in zip(E.canaries, E.canary_bound)]
    (E.paths.sets_dir / "canaries.age").write_text("\n".join(lines) + "\n")


def _t11_ctx(E, **over):
    base = dict(scan=S.scan_run, reseal=S.reseal_after_burn)
    base.update(over)
    return _t10_ctx(E, **base)


def _t11_run(E, commit=None, **kw):
    kw.setdefault("ctx", _t11_ctx(E))
    return _t10_run(E, commit, **kw)


def _t11_holdout_ids(E):
    return {i["id"] for i in B.broker_parse_items((sealed_store.holdout_age(E.paths)).read_bytes(),
                                                  load_spec(SPEC))}


def _t11_reserve_ids(E):
    return {i["id"] for i in B.broker_parse_items((sealed_store.reserve_age(E.paths)).read_bytes(),
                                                  load_spec(SPEC))}


# ================================================================ d1 loading

def test_t11_load_d1_by_file_path_never_imports_scripts_boundary():
    code = ("import sys; sys.path.insert(0, %r); from scripts.eval_seal import scan; d1 = scan.load_d1(); "
            "assert d1.__name__ == 'zurvan_eval_seal_d1'; assert hasattr(d1, 'confirm'); "
            "assert 'scripts.boundary' not in sys.modules, sorted(m for m in sys.modules if 'boundary' in m); "
            "assert 'scripts.boundary.d1' not in sys.modules; print('ok')") % str(ROOT)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr


@pytest.mark.skipif(not os.path.exists(CLT_PY), reason="CLT python 3.9 absent")
def test_t11_scan_imports_and_runs_under_the_sealed_python_39():
    code = ("import sys; sys.path.insert(0, %r); from scripts.eval_seal import scan; "
            "fp = scan.build_fingerprints(['x' * 10 + 'distinct text that is quite long enough to fingerprint, yes']); "
            "assert fp; print(len(scan.make_canaries(2)))") % str(ROOT)
    done = subprocess.run([CLT_PY, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.strip() == "2", done.stderr


# ================================================================ canaries and fingerprints

def test_t11_make_canaries_are_random_distinct_and_well_formed():
    a, b = S.make_canaries(50), S.make_canaries(50)
    assert len(set(a + b)) == 100
    assert all(c.startswith("zc-") and len(c) == 27 and c[3:] == c[3:].lower() for c in a)
    assert S.make_canaries(0) == []
    for bad in (-1, "3", None, 10 ** 6):
        with pytest.raises(ValueError):
            S.make_canaries(bad)


def test_t11_fingerprints_map_hashes_to_texts_and_short_texts_have_none():
    long_a = _t11_long_query("alpha")
    long_b = _t11_long_query("bravo")
    fp = S.build_fingerprints([long_a, "short", long_b])
    assert fp and all(isinstance(h, int) for h in fp)
    owners = {i for ix in fp.values() for i in ix}
    assert owners == {0, 2}                      # the short text contributes no fingerprint at all
    shared = [h for h, ix in fp.items() if ix == [0, 2]]
    assert shared                                # the common prose prefix is fingerprinted in both
    assert S.build_fingerprints([long_a]) == S.build_fingerprints([long_a])
    assert S.build_fingerprints([]) == {}


# ================================================================ scan(): detection and its floor

def _t11_scan_dir(tmp_path, files):
    d = tmp_path / "targets"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        f = d / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body if isinstance(body, bytes) else body.encode())
    return d


def test_t11_an_80_byte_verbatim_excerpt_is_found_and_confirmed_byte_exactly(tmp_path):
    q = _t11_long_query("planted-topic")
    excerpt = q.encode()[20:100]
    assert len(excerpt) == 80
    d = _t11_scan_dir(tmp_path, {"a/transcript.jsonl": b"noise " * 50 + excerpt + b" more noise" * 30,
                                 "b/clean.jsonl": "nothing here\n" * 100})
    got = S.scan([d], [], {"item-planted": q, "item-other": _t11_long_query("other-topic")})
    assert got["hits"] == ["item-planted"] and got["unreadable"] == 0 and got["canary_hits"] == 0


def test_t11_a_40_byte_excerpt_is_below_the_kgram_floor_and_is_not_a_hit(tmp_path):
    q = _t11_long_query("planted-topic")
    excerpt = q.encode()[20:60]
    assert len(excerpt) == 40
    d = _t11_scan_dir(tmp_path, {"t.jsonl": b"noise " * 50 + excerpt + b" more noise" * 30})
    # documents the floor: a shared span shorter than k+w-1 = 64 bytes is not detected
    assert S.MIN_MATCH == 64
    assert S.scan([d], [], {"item-planted": q})["hits"] == []


def test_t11_a_63_byte_excerpt_is_not_a_hit_and_a_64_byte_one_is(tmp_path):
    q = _t11_long_query("edge-topic")
    for length, expect in ((63, []), (64, ["i"])):
        d = _t11_scan_dir(tmp_path, {"t%d.jsonl" % length: b"zzz " * 40 + q.encode()[10:10 + length] + b" yyy" * 40})
        got = S.scan([d / ("t%d.jsonl" % length)], [], {"i": q})["hits"]
        assert got == expect, (length, got)


def test_t11_a_whole_query_of_32_bytes_or_more_is_matched_by_exact_substring(tmp_path):
    q = "exactly thirty-two bytes long!!!"
    assert len(q) == 32
    d = _t11_scan_dir(tmp_path, {"t.txt": "prefix " + q + " suffix"})
    assert S.scan([d], [], {"short-item": q})["hits"] == ["short-item"]
    q31 = q[:-1]
    assert S.scan([d], [], {"short-item": q31 + "?"})["hits"] == []   # 31 bytes + mismatch: below the floor


def test_t11_a_paraphrase_is_not_a_hit(tmp_path):
    q = _t11_long_query("paraphrase-topic")
    para = ("Which rollback criteria came with the decision the committee took on paraphrase-topic during "
            "the autumn review of the migration plan?")
    d = _t11_scan_dir(tmp_path, {"t.txt": para * 3})
    assert S.scan([d], [], {"i": q})["hits"] == []


def test_t11_a_short_repeating_run_is_not_a_hit(tmp_path):
    q = "ab" * 60
    d = _t11_scan_dir(tmp_path, {"t.txt": "ab" * 200})
    assert S.scan([d], [], {"i": q})["hits"] == []   # degenerate content identifies no origin (d1 semantics)


def test_t11_canary_tokens_are_found_by_exact_bytes_anywhere(tmp_path):
    canaries = S.make_canaries(3)
    d = _t11_scan_dir(tmp_path, {"deep/er/log.txt": "x" * 100 + canaries[1] + "y" * 100, "other.txt": "none"})
    got = S.scan([d], canaries, {})
    assert got["canary_hits"] == 1 and got["canary_found"] == [canaries[1]] and got["hits"] == []
    assert S.scan([d], [], {})["canary_hits"] == 0
    assert S.scan([d], [canaries[0], canaries[2]], {})["canary_hits"] == 0


def test_t11_a_match_straddling_a_chunk_boundary_is_still_found(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SCAN_CHUNK", 2048)
    monkeypatch.setattr(S, "SCAN_OVERLAP", 512)
    q = _t11_long_query("boundary-topic")
    canary = S.make_canaries(1)[0]
    body = b"a" * 2000 + q.encode()[:100] + b"b" * 4000 + canary.encode() + b"c" * 100
    assert 2000 < 2048 < 2000 + 100
    d = _t11_scan_dir(tmp_path, {"t.bin": body})
    got = S.scan([d], [canary], {"i": q})
    assert got["hits"] == ["i"] and got["canary_hits"] == 1


def test_t11_unreadable_files_and_directories_are_counted_not_skipped(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("chmod does not bind root")
    d = _t11_scan_dir(tmp_path, {"ok.txt": "fine", "locked.txt": "secret", "closed/inner.txt": "inner"})
    os.chmod(str(d / "locked.txt"), 0)
    os.chmod(str(d / "closed"), 0)
    try:
        got = S.scan([d], [], {"i": _t11_long_query("x")})
        assert got["unreadable"] == 2
        assert S.scan([tmp_path / "missing-target"], [], {})["unreadable"] == 1
    finally:
        os.chmod(str(d / "closed"), 0o755)
        os.chmod(str(d / "locked.txt"), 0o644)


def test_t11_symlinks_are_not_followed(tmp_path):
    q = _t11_long_query("link-topic")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text(q)
    d = _t11_scan_dir(tmp_path, {"plain.txt": "x"})
    os.symlink(str(outside), str(d / "dirlink"))
    os.symlink(str(outside / "leak.txt"), str(d / "filelink"))
    assert S.scan([d], [], {"i": q}) == {"hits": [], "canary_hits": 0, "canary_found": [], "unreadable": 0,
                                         "target_confirmed": [], "target_suspect": []}


def test_t11_scan_takes_only_sealed_needles_and_a_target_list():
    import inspect
    assert list(inspect.signature(S.scan).parameters) == ["targets", "canaries", "queries", "items"]
    assert list(inspect.signature(S.scan_run).parameters) == ["paths", "ctx", "ledger"]


# ================================================================ targets

def test_t11_scan_targets_lists_the_documented_locations_that_exist(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    proj = home / ".claude" / "projects"
    for name in ("-Users-x-Zurvan", "-Users-x-Zurvan--worktrees-z1b", "-Users-x-Other"):
        (proj / name).mkdir(parents=True)
    (proj / "stray-Zurvan-file").write_text("not a directory")
    (repo / "data" / "traces").mkdir(parents=True)
    (repo / "logs").mkdir()
    (home / ".zurvan" / "reports").mkdir(parents=True)
    got = S.scan_targets(SealPaths.for_root(tmp_path), home, repo)
    assert got == [proj / "-Users-x-Zurvan", proj / "-Users-x-Zurvan--worktrees-z1b", repo / "data" / "traces",
                   repo / "logs", home / ".zurvan" / "reports"]
    assert S.scan_targets(SealPaths.for_root(tmp_path), tmp_path / "none", tmp_path / "none2") == []


def test_t11_an_unlistable_projects_dir_is_itself_a_target_so_it_counts_unreadable(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("chmod does not bind root")
    home = tmp_path / "home"
    proj = home / ".claude" / "projects"
    (proj / "-Users-x-Zurvan").mkdir(parents=True)
    os.chmod(str(proj), 0)
    try:
        got = S.scan_targets(SealPaths.for_root(tmp_path), home, tmp_path / "repo")
        assert got == [proj]
        assert S.scan(got, [], {})["unreadable"] == 1
    finally:
        os.chmod(str(proj), 0o755)


@pytest.mark.parametrize("body", [None, "{}", "not json", '{"dev_home": "rel", "repo": "/x"}',
                                  '{"dev_home": "/x", "repo": "/y", "extra": 1}', '{"dev_home": 1, "repo": "/y"}'])
def test_t11_missing_or_malformed_scan_targets_config_is_scan_incomplete(tmp_path, body):
    paths = SealPaths.for_root(tmp_path)
    paths.broker_dir.mkdir(parents=True)
    if body is not None:
        paths.scan_targets_file.write_text(body)
    with pytest.raises(SealRefusal) as ei:
        S.scan_load_config(paths)
    assert ei.value.code == "SCAN_INCOMPLETE"


def test_t11_scan_run_reads_only_sealed_or_installed_files_and_the_targets(tmp_path, monkeypatch):
    E = _t11_build(tmp_path)
    opened = []
    real_open, real_os_open = open, os.open

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_os_open(path, *a, **k):
        opened.append(str(path))
        return real_os_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    S.scan_run(E.paths, _t11_ctx(E), _t10_ledger(E))
    monkeypatch.undo()
    allowed = [str(E.paths.eval_home), str(E.paths.broker_dir), str(E.dev), str(E.devrepo), str(SPEC),
               str(E.paths.runs_dir)]
    stray = [p for p in opened if not any(p.startswith(a) for a in allowed) and not p.startswith(
        ("/dev/", "/dev", sys.prefix)) and "site-packages" not in p and "__pycache__" not in p
        and not p.endswith((".py", ".pyc", ".so", ".dylib"))]
    assert stray == [], stray
    # and the broker grammar offers the dev side no way to feed the scan anything
    for argv in (["scan", "x"], ["scan", "--query", "y"], ["run", "--scan", "x"]):
        with pytest.raises(SealRefusal) as ei:
            B.broker_parse_argv(argv)
        assert ei.value.code == "BAD_ARGS"


# ================================================================ end to end: gate 6

def test_t11_a_planted_canary_in_a_dev_trace_raises_an_incident_burns_the_item_and_blocks_the_next_run(tmp_path):
    E = _t11_build(tmp_path)
    bound = E.holdout[0]
    with open(str(E.trace), "a") as fh:
        fh.write("leaked: %s\n" % E.canaries[0])
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    led = _t10_ledger(E)
    assert led.burned_ids() == {bound["id"]}
    assert [e["incident_kind"] for e in led.open_incidents()] == ["canary_hit"]
    assert "reserved" not in _t10_kinds(E)
    # next run: still refused, whatever else is true
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    # the incident file names no query text
    files = list(E.paths.incidents_dir.iterdir())
    assert len(files) == 1 and bound["query"] not in files[0].read_text()


def test_t11_acknowledging_the_incident_clears_the_refusal_but_never_the_burn(tmp_path):
    E = _t11_build(tmp_path)
    bound = E.holdout[0]
    E.trace.write_text("leaked: %s\n" % E.canaries[0])
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    iid = _t10_ledger(E).open_incidents()[0]["incident_id"]
    assert _t10_call(E, ["ack-incident", iid], ctx=_t11_ctx(E))[2]["open_incidents"] == 0
    # the canary is still in the trace, but its item is already burned: nothing new is exposed
    rc, _t, doc = _t11_run(E)
    assert rc == 0 and doc["ok"] is True and doc["n_items"] == len(E.holdout)
    assert bound["id"] in _t10_ledger(E).burned_ids()
    assert bound["id"] not in _t11_holdout_ids(E) | _t11_reserve_ids(E)


def test_t11_an_unbound_canary_hit_burns_every_live_item(tmp_path):
    E = _t11_build(tmp_path)
    E.trace.write_text("leaked: %s\n" % E.canaries[2])    # canary 2 has item_id null
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    assert _t10_ledger(E).burned_ids() == {i["id"] for i in E.holdout + E.reserve}


def test_t11_a_verbatim_query_excerpt_in_a_dev_artifact_burns_that_item(tmp_path):
    E = _t11_build(tmp_path)
    victim = E.holdout[4]
    (E.devrepo / "logs").mkdir()
    (E.devrepo / "logs" / "debug.log").write_text("pasted: " + victim["query"][5:95] + "\n")
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    led = _t10_ledger(E)
    assert led.burned_ids() == {victim["id"]}
    assert [e["incident_kind"] for e in led.open_incidents()] == ["item_leak"]


def test_t11_a_40_byte_excerpt_in_a_dev_artifact_does_not_trigger_anything(tmp_path):
    E = _t11_build(tmp_path)
    (E.devrepo / "logs").mkdir()
    (E.devrepo / "logs" / "debug.log").write_text("pasted: " + E.holdout[4]["query"][5:45] + "\n")
    assert _t11_run(E)[0] == 0
    assert _t10_ledger(E).burned_ids() == set()


def test_t11_an_unreadable_target_refuses_the_run_with_scan_incomplete_and_charges_nothing(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("chmod does not bind root")
    E = _t11_build(tmp_path)
    locked = E.dev / ".claude" / "projects" / "-Users-x-Zurvan" / "locked.jsonl"
    locked.write_text("x")
    os.chmod(str(locked), 0)
    try:
        _t10_refused(_t11_run(E), "SCAN_INCOMPLETE")
    finally:
        os.chmod(str(locked), 0o644)
    assert "reserved" not in _t10_kinds(E) and not _t10_ledger(E).open_incidents()
    assert _t11_run(E)[0] == 0   # readable again: the run proceeds


def test_t11_a_hit_outranks_an_unreadable_target(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("chmod does not bind root")
    E = _t11_build(tmp_path)
    locked = E.dev / ".claude" / "projects" / "-Users-x-Zurvan" / "locked.jsonl"
    locked.write_text("x")
    os.chmod(str(locked), 0)
    E.trace.write_text(E.canaries[0])
    try:
        _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    finally:
        os.chmod(str(locked), 0o644)
    assert _t10_ledger(E).burned_ids() == {E.holdout[0]["id"]}


def test_t11_a_missing_or_malformed_sealed_canary_file_is_manifest_invalid_not_a_pass(tmp_path):
    E = _t11_build(tmp_path)
    canaries = E.paths.sets_dir / "canaries.age"
    saved = canaries.read_text()
    canaries.unlink()
    _t10_refused(_t11_run(E), "MANIFEST_INVALID")
    canaries.write_text('{"token": "short", "item_id": null}\n')
    _t10_refused(_t11_run(E), "MANIFEST_INVALID")
    canaries.write_text("not json\n")
    _t10_refused(_t11_run(E), "MANIFEST_INVALID")
    canaries.write_text(saved)
    assert _t11_run(E)[0] == 0
    assert "reserved" in _t10_kinds(E)


# ================================================================ burn, replace, reseal

def _t11_items(strata_ids):
    """Items with explicit (id char, stratum, difficulty)."""
    out = []
    for ch, stratum, diff in strata_ids:
        item = _t10_item(0)
        item.update(id=ch * 32, stratum="path" if stratum is None else stratum, difficulty=diff)
        out.append(item)
    return out


def test_t11_replace_burned_moves_same_stratum_reserve_items_and_never_returns_a_burned_id():
    holdout = _t11_items([("a", "path", "easy"), ("b", "path", "hard"), ("c", "semantic", "easy")])
    reserve = _t11_items([("1", "path", "hard"), ("2", "path", "easy"), ("3", "semantic", "medium"),
                          ("4", "hash", "easy")])
    h, r, retired = S.replace_burned(holdout, reserve, {"a" * 32, "3" * 32})
    assert {i["id"] for i in retired} == {"a" * 32, "3" * 32}
    assert [i["id"] for i in h] == sorted(["b" * 32, "c" * 32, "2" * 32])     # "a" (easy) -> "2" (easy), same stratum
    assert [i["id"] for i in r] == sorted(["1" * 32, "4" * 32])
    assert not ({"a" * 32, "3" * 32} & ({i["id"] for i in h} | {i["id"] for i in r}))
    assert all(x["stratum"] in ("path", "semantic") for x in h)
    # a burned HOLDOUT semantic item had no unburned semantic reserve left: the stratum is short, not padded
    no_semantic = [i for i in reserve if i["id"] != "3" * 32]
    h2, r2, _ = S.replace_burned(holdout, no_semantic, {"c" * 32})
    assert sorted(i["stratum"] for i in h2) == ["path", "path"] and len(r2) == 3
    # nothing burned: unchanged (sorted)
    assert S.replace_burned(holdout, reserve, set())[0] == sorted(holdout, key=lambda i: i["id"])


def test_t11_replace_burned_with_a_burned_reserve_item_retires_it_and_never_promotes_it():
    holdout = _t11_items([("a", "path", "easy")])
    reserve = _t11_items([("1", "path", "easy"), ("2", "path", "easy")])
    h, r, retired = S.replace_burned(holdout, reserve, {"a" * 32, "1" * 32})
    assert [i["id"] for i in h] == ["2" * 32] and r == []
    assert {i["id"] for i in retired} == {"a" * 32, "1" * 32}


def test_t11_burn_is_irreversible_and_idempotent_through_the_scan_module(tmp_path):
    led = Ledger(tmp_path / "runs")
    assert S.burn(led, ["a" * 32], "operator", "2026-10-02") == ["a" * 32]
    assert S.burn(led, ["a" * 32], "canary_hit", "2026-10-03") == []
    assert led.burned_ids() == {"a" * 32}


def test_t11_reseal_increments_sequence_keeps_suite_changes_the_manifest_digest_and_keeps_strata(tmp_path):
    E = _t11_build(tmp_path)
    led = _t10_ledger(E)
    burned = E.holdout[2]                                   # a 'semantic' item; reserve has one 'semantic'
    old_digest = manifest_mod.suite_manifest_sha256(sealed_store.manifest(E.paths))
    led.burn_items([burned["id"]], "operator", "2026-10-02")
    out = S.reseal_after_burn(E.paths, _t11_ctx(E), led)
    assert out == {"resealed": True, "sequence": 2}
    m = manifest_mod.verify(sealed_store.manifest(E.paths), sealed_store.manifest_sig(E.paths),
                            E.paths.keys_dir / "allowed_signers")
    assert m["sequence"] == 2 and m["suite_id"] == E.manifest["suite_id"]
    assert manifest_mod.suite_manifest_sha256(sealed_store.manifest(E.paths)) != old_digest
    assert m["burn_ledger"] == [{"id": burned["id"], "reason": "operator", "date": "2026-10-02"}]
    holdout, reserve = _t11_holdout_ids(E), _t11_reserve_ids(E)
    assert burned["id"] not in holdout | reserve
    promoted = (holdout - {i["id"] for i in E.holdout})
    assert len(holdout) == len(E.holdout) and len(promoted) == 1
    promoted_item = next(i for i in E.reserve if i["id"] in promoted)
    assert promoted_item["stratum"] == burned["stratum"]
    assert len(reserve) == len(E.reserve) - 1
    assert m["strata_counts"]["holdout"][burned["stratum"]] == 2
    assert m["strata_counts"]["reserve"].get(burned["stratum"], 0) == 0
    assert m["holdout_age_sha256"] == hashlib.sha256((sealed_store.holdout_age(E.paths)).read_bytes()).hexdigest()
    assert led.last_manifest_sequence() == 2
    # idempotent: nothing left to replace
    assert S.reseal_after_burn(E.paths, _t11_ctx(E), led) == {"resealed": False}


def test_t11_a_short_stratum_after_a_burn_is_reported_short_never_padded(tmp_path):
    E = _t11_build(tmp_path)
    led = _t10_ledger(E)
    victim = next(i for i in E.holdout if i["stratum"] == "path")
    E.reserve = [i for i in E.reserve if i["stratum"] != "path"]
    _t10_seal(E)
    led.burn_items([victim["id"]], "operator", "2026-10-02")
    S.reseal_after_burn(E.paths, _t11_ctx(E), led)
    m = manifest_mod.verify(sealed_store.manifest(E.paths), sealed_store.manifest_sig(E.paths),
                            E.paths.keys_dir / "allowed_signers")
    assert len(_t11_holdout_ids(E)) == len(E.holdout) - 1
    assert m["strata_counts"]["holdout"]["path"] == 1
    assert {"pool": "holdout", "stratum": "path", "have": 1, "need": 10} in m["short"]


def test_t11_reseal_refuses_when_the_current_sealed_state_does_not_verify(tmp_path):
    E = _t11_build(tmp_path)
    led = _t10_ledger(E)
    led.burn_items([E.holdout[0]["id"]], "operator", "2026-10-02")
    before = (sealed_store.holdout_age(E.paths)).read_bytes()
    sealed_store.manifest(E.paths).write_bytes(sealed_store.manifest(E.paths).read_bytes() + b" ")
    with pytest.raises(SealRefusal) as ei:
        S.reseal_after_burn(E.paths, _t11_ctx(E), led)
    assert ei.value.code == "MANIFEST_INVALID"
    assert (sealed_store.holdout_age(E.paths)).read_bytes() == before
    assert not list(E.paths.sets_dir.glob("*.new"))


def test_t11_after_a_burn_the_burned_ids_are_absent_from_holdout_and_reserve_and_present_in_the_manifest(tmp_path):
    E = _t11_build(tmp_path)
    (E.devrepo / "logs").mkdir()
    victims = [E.holdout[1], E.holdout[5]]
    (E.devrepo / "logs" / "a.log").write_text(victims[0]["query"][:100])
    (E.devrepo / "logs" / "b.log").write_text(victims[1]["query"][3:110])
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    ids = {v["id"] for v in victims}
    assert _t10_ledger(E).burned_ids() == ids
    assert not ids & (_t11_holdout_ids(E) | _t11_reserve_ids(E))
    m = manifest_mod.verify(sealed_store.manifest(E.paths), sealed_store.manifest_sig(E.paths),
                            E.paths.keys_dir / "allowed_signers")
    assert {e["id"] for e in m["burn_ledger"]} == ids and {e["reason"] for e in m["burn_ledger"]} == {"item_leak"}
    # operator clears the artifacts and the incident: the next run uses the resealed suite
    for f in (E.devrepo / "logs").iterdir():
        f.unlink()
    for inc in _t10_ledger(E).open_incidents():
        _t10_call(E, ["ack-incident", inc["incident_id"]], ctx=_t11_ctx(E))
    rc, _t, doc = _t11_run(E)
    assert rc == 0 and doc["n_items"] == len(E.holdout)
    assert doc["suite_id"] == E.manifest["suite_id"]
    assert doc["suite_manifest_sha256"] != E.manifest["holdout_age_sha256"]


def test_t11_a_crafted_set_that_re_adds_a_burned_id_to_holdout_is_manifest_invalid(tmp_path):
    E = _t11_build(tmp_path)
    led = _t10_ledger(E)
    led.burn_items([E.holdout[3]["id"]], "item_leak", "2026-10-01")
    _t10_seal(E, sequence=1)           # validly signed, but its holdout still contains the burned item
    _t10_refused(_t11_run(E), "MANIFEST_INVALID")
    assert "reserved" not in _t10_kinds(E) and E.launched == []


def test_t11_the_scan_subcommand_heals_a_pending_reseal_and_run_then_works(tmp_path):
    E = _t11_build(tmp_path)
    led = _t10_ledger(E)
    led.burn_items([E.holdout[3]["id"]], "item_leak", "2026-10-01")   # burned, reseal never happened (crash)
    _t10_refused(_t11_run(E), "MANIFEST_INVALID")
    rc, _t, doc = _t10_call(E, ["scan"], ctx=_t11_ctx(E))
    assert rc == 0 and doc["manifest_valid"] is True
    assert E.holdout[3]["id"] not in _t11_holdout_ids(E)
    assert _t11_run(E)[0] == 0


def test_t11_a_sandbox_violation_reseals_through_the_real_reseal(tmp_path):
    E = _t11_build(tmp_path)
    _t10_refused(_t11_run(E, ctx=_t11_ctx(E, sweep_processes=lambda: 1)), "SANDBOX_VIOLATION")
    sent = {i["id"] for i in E.holdout}
    led = _t10_ledger(E)
    assert led.burned_ids() == sent
    assert _t11_holdout_ids(E) == set() or not (_t11_holdout_ids(E) & sent)
    assert not (_t11_reserve_ids(E) & sent)
    # every holdout item was burned; the reserve refilled what it could, stratum by stratum
    assert len(_t11_holdout_ids(E)) == len(E.reserve)
    for inc in led.open_incidents():
        _t10_call(E, ["ack-incident", inc["incident_id"]], ctx=_t11_ctx(E))
    assert _t11_run(E)[2]["n_items"] == len(E.reserve)


def test_t11_a_failed_reseal_after_a_hit_still_ends_in_integrity_incident(tmp_path):
    E = _t11_build(tmp_path)
    E.trace.write_text(E.canaries[0])
    sealed_store.manifest(E.paths).write_bytes(sealed_store.manifest(E.paths).read_bytes() + b" ")   # reseal will refuse
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    assert _t10_ledger(E).burned_ids() == {E.holdout[0]["id"]}
    assert (E.paths.runs_dir / "errors.log").is_file()   # the failed reseal is logged, sealed-side only


def test_t11_reseal_also_refuses_when_a_set_file_no_longer_matches_the_signed_digest(tmp_path):
    E = _t11_build(tmp_path)
    led = _t10_ledger(E)
    led.burn_items([E.holdout[0]["id"]], "operator", "2026-10-02")
    for name in ("holdout", "reserve"):
        f = sealed_store.age_path(E.paths, name)
        saved = f.read_bytes()
        f.write_bytes(saved + b"\n")
        with pytest.raises(SealRefusal) as ei:
            S.reseal_after_burn(E.paths, _t11_ctx(E), led)
        assert ei.value.code == "MANIFEST_INVALID"
        f.write_bytes(saved)
    assert S.reseal_after_burn(E.paths, _t11_ctx(E), led)["resealed"] is True


def test_t11_a_leak_that_is_already_burned_is_not_rescanned_into_a_fresh_incident_every_run(tmp_path):
    E = _t11_build(tmp_path)
    victim = E.holdout[4]
    (E.devrepo / "logs").mkdir()
    (E.devrepo / "logs" / "debug.log").write_text("pasted: " + victim["query"][5:95] + "\n")
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    for inc in _t10_ledger(E).open_incidents():
        _t10_call(E, ["ack-incident", inc["incident_id"]], ctx=_t11_ctx(E))
    # the evidence is still on disk, but the item is retired: the suite no longer contains it
    rc, _t, doc = _t11_run(E)
    assert rc == 0 and doc["n_items"] == len(E.holdout)
    assert not _t10_ledger(E).open_incidents()


def test_t11_burned_items_are_excluded_from_the_scan_even_while_a_reseal_is_pending(tmp_path):
    E = _t11_build(tmp_path)
    victim = E.holdout[4]
    (E.devrepo / "logs").mkdir()
    (E.devrepo / "logs" / "debug.log").write_text("pasted: " + victim["query"][5:95] + "\n")
    no_reseal = _t11_ctx(E, reseal=lambda paths, ctx, led: {"resealed": False})
    _t10_refused(_t10_run(E, ctx=no_reseal), "INTEGRITY_INCIDENT")
    for inc in _t10_ledger(E).open_incidents():
        _t10_call(E, ["ack-incident", inc["incident_id"]], ctx=no_reseal)
    # the set still holds the burned item: refused for THAT reason, not as a fresh scan hit
    _t10_refused(_t10_run(E, ctx=no_reseal), "MANIFEST_INVALID")
    assert not _t10_ledger(E).open_incidents()


# ================================================================ #13: target-page (answer-key) leaks

_TID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"   # a real 32-hex item id
_TPAGE = "wiki/x.md"


def _classify(text, iid=_TID, pages=(_TPAGE,)):
    return S.classify_target_associations(text, [{"id": iid, "pages": list(pages)}])


def _verdict(text, **kw):
    r = _classify(text, **kw)
    if r["confirmed"]:
        return "burn"
    if r["suspect"]:
        return "suspect"
    return "clean"


def test_t13_bare_path_and_bare_id_and_manifest_are_clean():
    assert _verdict("see wiki/x.md for details") == "clean"               # bare path
    assert _verdict("item %s was slow" % _TID) == "clean"                 # bare id
    manifest = "\n".join("wiki/%s.md" % n for n in ("a", "x", "b", "c"))  # corpus manifest of paths, no id
    assert _verdict(manifest) == "clean"


def test_t13_id_and_path_far_apart_or_unrelated_same_page_are_clean():
    far = "item %s ran\n" % _TID + ("filler line\n" * 50) + "the page wiki/x.md exists\n"
    assert _verdict(far) == "clean"                                       # far apart in a log
    same_page = "notes about item %s here\nand separately wiki/x.md is a page\n" % _TID
    assert _verdict(same_page) == "clean"                                 # different lines, no relation


def test_t13_structured_record_one_line_and_multiline_burns():
    assert _verdict('{"item":"%s","target":"%s"}' % (_TID, _TPAGE)) == "burn"
    assert _verdict('{\n  "item": "%s",\n  "expected": "%s"\n}' % (_TID, _TPAGE)) == "burn"
    assert _verdict('{"id": "%s", "answer": ["%s"]}' % (_TID, _TPAGE)) == "burn"


def test_t13_arrow_mapping_and_relation_sentence_burn():
    assert _verdict("%s -> %s" % (_TID, _TPAGE)) == "burn"
    assert _verdict("%s => %s" % (_TID, _TPAGE)) == "burn"
    assert _verdict("answer to %s is %s" % (_TID, _TPAGE)) == "burn"      # the prose the old #3 wrongly allowed
    assert _verdict("The expected target for item %s is %s" % (_TID, _TPAGE)) == "burn"


def test_t13_case_whitespace_and_order_variants_hold_the_verdict():
    assert _verdict("ANSWER  to  %s  is  %s" % (_TID, _TPAGE)) == "burn"
    assert _verdict("%s    ->    %s" % (_TID, _TPAGE)) == "burn"
    assert _verdict("%s <- %s" % (_TPAGE, _TID)) == "burn"                # reversed arrow
    assert _verdict('{"target":"%s","item":"%s"}' % (_TPAGE, _TID)) == "burn"  # reversed key order


def test_t13_ambiguous_association_is_suspect_not_a_burn():
    # a mapping-shaped line (brace/colon hint) with both tokens but no clean grammar: review, never burn
    assert _verdict("row { %s ... some text ... %s } end" % (_TID, _TPAGE)) == "suspect"


def test_t13_basename_only_does_not_burn_the_canonical_target():
    # the mapping names only the basename; the canonical target page is wiki/x.md -> not a confirmed leak
    assert _verdict("%s -> x.md" % _TID) == "clean"


def test_t13_abstain_items_have_no_pages_and_never_match():
    assert S.classify_target_associations("%s -> %s" % (_TID, _TPAGE),
                                          [{"id": _TID, "pages": []}]) == {"confirmed": [], "suspect": []}


def test_t13_confirmed_answer_key_in_a_scanned_location_burns_via_target_leak(tmp_path):
    E = _t11_build(tmp_path)
    (E.devrepo / "logs").mkdir()
    victim = next(i for i in E.holdout if i["targets"])
    # an answer key pairing the item id with its target page: the exfiltration #13 must catch
    (E.devrepo / "logs" / "answers.jsonl").write_text(
        '{"item":"%s","target":"%s"}\n' % (victim["id"], victim["targets"][0]["page"]))
    _t10_refused(_t11_run(E), "INTEGRITY_INCIDENT")
    assert victim["id"] in _t10_ledger(E).burned_ids()
    assert victim["id"] not in (_t11_holdout_ids(E) | _t11_reserve_ids(E))
    m = manifest_mod.verify(sealed_store.manifest(E.paths), sealed_store.manifest_sig(E.paths),
                            E.paths.keys_dir / "allowed_signers")
    assert victim["id"] in {e["id"] for e in m["burn_ledger"]}
    assert "target_leak" in {e["reason"] for e in m["burn_ledger"]}


def test_t13_suspect_association_is_recorded_for_review_and_never_burns(tmp_path):
    E = _t11_build(tmp_path)
    (E.devrepo / "logs").mkdir()
    victim = next(i for i in E.holdout if i["targets"])
    # ambiguous: id and page inside a brace-ish blob but no clean answer-key grammar -> review, not burn
    (E.devrepo / "logs" / "notes.txt").write_text(
        "row { %s ... some notes ... %s } end\n" % (victim["id"], victim["targets"][0]["page"]))
    rc, _t, doc = _t11_run(E)
    assert rc == 0                                              # a suspect never blocks the run
    assert victim["id"] not in _t10_ledger(E).burned_ids()     # and never burns
    assert victim["id"] in _t11_holdout_ids(E)                 # the item survives
    reviews = list(E.paths.incidents_dir.glob("review-target_suspect-*.json"))
    assert reviews, "a suspect association must leave a non-blocking review note"
    assert victim["id"] in json.loads(reviews[0].read_text())["item_ids"]


def test_t13_benign_dev_artifacts_referencing_ids_and_paths_never_burn(tmp_path):
    # the FP guard: a realistic dev blob that references items by id (sanctioned) and lists corpus paths,
    # but is NOT an answer key, must yield zero CONFIRMED burns (the 0/200-ceiling discipline for this
    # new auto-burn detector). SUSPECT is allowed (non-destructive); CONFIRMED is not.
    E = _t11_build(tmp_path)
    items = [{"id": i["id"], "pages": [t["page"] for t in i["targets"]]} for i in E.holdout if i["targets"]]
    v = items[0]
    blob = "\n".join([
        "status: item %s regressed vs baseline (recall down)" % v["id"],           # id alone
        "corpus manifest:", v["pages"][0], "wiki/other.md", "docs/readme.md",       # bare paths
        "see item %s for the cross_project case" % v["id"],                         # id alone again
        "the page %s is large" % v["pages"][0],                                     # bare path again
        "run notes: %s failed; also reviewed %s separately" % (v["id"], v["pages"][0]),  # same line, no rel/arrow
    ])
    res = S.classify_target_associations(blob, items)
    assert res["confirmed"] == []                                                   # zero false burns
