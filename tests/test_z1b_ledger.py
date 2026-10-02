"""Z1b Task 7 tests: hash-chained run ledger, budget, cache, incidents (contract s3.6)."""
import concurrent.futures as cf
import hashlib
import json
import multiprocessing
import os
import stat
import time
from pathlib import Path

import pytest

from scripts.eval_seal import ledger as L
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.ledger import Ledger, run_key

SUITE = "0123456789abcdef"
COMMIT = "c" * 40
H64 = "ab" * 32


def _t7_key(n: int) -> str:
    return hashlib.sha256(("key-%d" % n).encode()).hexdigest()


def _t7_rows(tmp: Path, name="rows.jsonl") -> Path:
    p = tmp / name
    p.write_bytes(b'{"i":1}\n')
    return p


def _t7_refused(fn, code="LEDGER_INVALID"):
    with pytest.raises(SealRefusal) as ei:
        fn()
    assert ei.value.code == code


def _t7_build(tmp_path):
    """A ledger with every entry kind, returns (ledger, runs_dir)."""
    runs = tmp_path / "runs"
    led = Ledger(runs)
    r1 = led.reserve_run(_t7_key(1), SUITE, 8)
    led.complete_run(r1, {"ok": True, "n": 1}, _t7_rows(tmp_path))
    r2 = led.reserve_run(_t7_key(2), SUITE, 8)
    led.fail_run(r2, "CANDIDATE_FAILED")
    inc = led.record_incident("canary_hit", ["item-1", "item_2"])
    led.acknowledge_incident(inc)
    led.append({"kind": "burn", "id": "0f" * 16, "reason": "canary_hit", "date": "2026-10-02"})
    led.append({"kind": "manifest_seen", "sequence": 1, "manifest_sha256": H64})
    return led, runs


def _t7_lines(runs: Path):
    return (runs / "ledger.jsonl").read_bytes().split(b"\n")[:-1]


def _t7_write(runs: Path, lines):
    (runs / "ledger.jsonl").write_bytes(b"".join(l + b"\n" for l in lines))


def _t7_canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# ------------------------------------------------------------------ run_key

def test_t7_run_key_is_deterministic_and_sensitive_to_every_input():
    # ruling R14: the runtime digest is part of the key, so a changed runtime is never served from cache
    base = run_key(COMMIT, H64, "cd" * 32, "1.0.0", "holdout", "ef" * 32)
    assert base == run_key(COMMIT, H64, "cd" * 32, "1.0.0", "holdout", "ef" * 32)
    assert len(base) == 64
    variants = [
        run_key("d" * 40, H64, "cd" * 32, "1.0.0", "holdout", "ef" * 32),
        run_key(COMMIT, "ac" * 32, "cd" * 32, "1.0.0", "holdout", "ef" * 32),
        run_key(COMMIT, H64, "ce" * 32, "1.0.0", "holdout", "ef" * 32),
        run_key(COMMIT, H64, "cd" * 32, "1.0.1", "holdout", "ef" * 32),
        run_key(COMMIT, H64, "cd" * 32, "1.0.0", "reserve", "ef" * 32),
        run_key(COMMIT, H64, "cd" * 32, "1.0.0", "holdout", "f0" * 32),
    ]
    assert len({base, *variants}) == 7


@pytest.mark.parametrize("args", [
    ("short", H64, H64, "1.0.0", "holdout", H64), (COMMIT, "x", H64, "1.0.0", "holdout", H64),
    (COMMIT, H64, "x", "1.0.0", "holdout", H64), (COMMIT, H64, H64, "v1", "holdout", H64),
    (COMMIT, H64, H64, "1.0.0", "dev", H64), (COMMIT.upper(), H64, H64, "1.0.0", "holdout", H64),
    (COMMIT, H64, H64, "1.0.0\n", "holdout", H64),
    (COMMIT, H64, H64, "1.0.0", "holdout", "short"), (COMMIT, H64, H64, "1.0.0", "holdout", H64.upper()),
    (COMMIT, H64, H64, "1.0.0", "holdout", None),
])
def test_t7_run_key_refuses_malformed_inputs(args):
    _t7_refused(lambda: run_key(*args), "BAD_ARGS")


# ------------------------------------------------------------------ chain

def test_t7_chain_verifies_and_reads_back(tmp_path):
    led, runs = _t7_build(tmp_path)
    es = led.entries()
    assert [e["seq"] for e in es] == list(range(len(es)))
    assert es[0]["prev_sha256"] == "0" * 64
    for a, b in zip(es, es[1:]):
        assert b["prev_sha256"] == a["entry_sha256"]
    assert [e["kind"] for e in es] == ["reserved", "completed", "reserved", "failed", "incident", "ack",
                                       "burn", "manifest_seen"]
    assert all(e["schema"] == "zurvan.z1b.ledger.v1" for e in es)
    assert Ledger(runs).entries() == es  # a fresh object verifies the same file


def test_t7_empty_ledger_is_valid(tmp_path):
    led = Ledger(tmp_path / "runs")
    assert led.entries() == []
    assert led.budget_remaining(SUITE, 8) == 8
    assert led.burned_ids() == set() and led.last_manifest_sequence() == 0 and led.open_incidents() == []


def test_t7_tamper_any_field_of_any_line_is_ledger_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    original = _t7_lines(runs)
    checked = 0
    for i, line in enumerate(original):
        obj = json.loads(line)
        for field in list(obj):
            tampered = dict(obj)
            v = obj[field]
            if isinstance(v, bool):
                tampered[field] = not v
            elif isinstance(v, int):
                tampered[field] = v + 1
            elif isinstance(v, str):
                tampered[field] = v[:-1] + ("0" if v[-1] != "0" else "1")
            elif isinstance(v, list):
                tampered[field] = v + ["x"]
            elif isinstance(v, dict):
                tampered[field] = dict(v, extra_tamper=1)
            else:
                tampered[field] = "x"
            new = list(original)
            new[i] = _t7_canon(tampered)
            _t7_write(runs, new)
            _t7_refused(lambda: Ledger(runs).entries())
            checked += 1
    assert checked > 40
    _t7_write(runs, original)
    assert Ledger(runs).entries()


def test_t7_extra_and_missing_fields_are_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    original = _t7_lines(runs)
    obj = json.loads(original[-1])
    for mut in (lambda o: o.update(unburn=True), lambda o: o.pop("sequence")):
        o = dict(obj)
        mut(o)
        body = {k: v for k, v in o.items() if k != "entry_sha256"}
        o["entry_sha256"] = hashlib.sha256(_t7_canon(body)).hexdigest()  # hash recomputed: schema must refuse
        new = list(original)
        new[-1] = _t7_canon(o)
        _t7_write(runs, new)
        _t7_refused(lambda: Ledger(runs).entries())


def test_t7_truncating_last_line_mid_json_is_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    raw = (runs / "ledger.jsonl").read_bytes()
    (runs / "ledger.jsonl").write_bytes(raw[:-20])
    _t7_refused(lambda: Ledger(runs).entries())
    (runs / "ledger.jsonl").write_bytes(raw[:-1])  # newline only
    _t7_refused(lambda: Ledger(runs).entries())
    _t7_refused(lambda: Ledger(runs).append({"kind": "ack", "incident_id": "0" * 16}))
    _t7_refused(lambda: Ledger(runs).reserve_run(_t7_key(9), SUITE, 8))


def test_t7_deleting_a_middle_line_is_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    for i in range(1, len(lines) - 1):
        _t7_write(runs, lines[:i] + lines[i + 1:])
        _t7_refused(lambda: Ledger(runs).entries())
    _t7_write(runs, lines[1:])  # first line removed
    _t7_refused(lambda: Ledger(runs).entries())


def test_t7_reordering_lines_is_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    _t7_write(runs, [lines[1], lines[0]] + lines[2:])
    _t7_refused(lambda: Ledger(runs).entries())
    _t7_write(runs, lines + [lines[-1]])  # duplicated tail
    _t7_refused(lambda: Ledger(runs).entries())


def test_t7_blank_line_bad_json_and_non_ascii_are_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    _t7_write(runs, lines + [b""])
    _t7_refused(lambda: Ledger(runs).entries())
    _t7_write(runs, lines + [b"{not json"])
    _t7_refused(lambda: Ledger(runs).entries())
    _t7_write(runs, lines + ["é".encode()])
    _t7_refused(lambda: Ledger(runs).entries())
    _t7_write(runs, [b"[]"])
    _t7_refused(lambda: Ledger(runs).entries())


def test_t7_noncanonical_whitespace_or_key_order_is_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    obj = json.loads(lines[0])
    _t7_write(runs, [json.dumps(obj, sort_keys=True).encode()] + lines[1:])  # spaces after separators
    _t7_refused(lambda: Ledger(runs).entries())


def _t7_forge_line(prev_line, mutate):
    """Forge a fully hash-consistent next line, so only the SCHEMA / state machine can refuse it."""
    prev = json.loads(prev_line)
    o = {"schema": "zurvan.z1b.ledger.v1", "seq": prev["seq"] + 1, "prev_sha256": prev["entry_sha256"]}
    mutate(o)
    o["entry_sha256"] = hashlib.sha256(_t7_canon(o)).hexdigest()
    return _t7_canon(o)


@pytest.mark.parametrize("fields", [
    {"kind": "unburn", "id": "0f" * 16},
    {"kind": "burn", "id": "0f" * 16, "reason": "unburn", "date": "2026-10-02"},
    {"kind": "burn", "id": "0f" * 16, "reason": "canary_hit", "date": "2026-10-02", "unburn": True},
    {"kind": "burn", "id": "0f" * 16, "reason": "canary_hit", "date": "2026-10-02", "undo": "x"},
    {"kind": "burn", "id": "0f" * 8, "reason": "canary_hit", "date": "2026-10-02"},
    {"kind": "burn", "id": "0f" * 32, "reason": "canary_hit", "date": "2026-10-02"},
    {"kind": "burn", "id": "0f" * 16, "reason": "canary_hit", "date": "today"},
    {"kind": "burn", "ids": ["0f" * 16], "reason": "canary_hit", "date": "2026-10-02"},
    {"kind": "burn_reversal", "id": "0f" * 16, "reason": "canary_hit", "date": "2026-10-02"},
    {"kind": "restore", "id": "0f" * 16},
], ids=["u%02d" % i for i in range(10)])
def test_t7_hand_crafted_unburn_shaped_entries_are_invalid(tmp_path, fields):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    forged = _t7_forge_line(lines[-1], lambda o: o.update(fields))
    _t7_write(runs, lines + [forged])
    _t7_refused(lambda: Ledger(runs).entries())
    assert not hasattr(Ledger, "unburn") and not hasattr(Ledger, "remove_burn") and not hasattr(Ledger, "clear_burns")


def test_t7_unknown_kind_and_unknown_schema_are_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    _t7_write(runs, lines + [_t7_forge_line(lines[-1], lambda o: o.update(kind="mystery"))])
    _t7_refused(lambda: Ledger(runs).entries())
    bad = _t7_forge_line(lines[-1], lambda o: o.update(kind="ack", incident_id="0" * 16))
    obj = json.loads(bad)
    obj["schema"] = "zurvan.z1b.ledger.v2"
    obj.pop("entry_sha256")
    obj["entry_sha256"] = hashlib.sha256(_t7_canon(obj)).hexdigest()
    _t7_write(runs, lines + [_t7_canon(obj)])
    _t7_refused(lambda: Ledger(runs).entries())
    _t7_refused(lambda: Ledger(tmp_path / "runs").append({"kind": "mystery"}))
    _t7_refused(lambda: Ledger(tmp_path / "runs").append({"kind": "burn", "id": "0f" * 16,
                                                         "reason": "unburn", "date": "2026-10-02"}))


def test_t7_illegal_state_transitions_in_a_forged_file_are_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    first = json.loads(lines[0])
    for fields in (
        {"kind": "completed", "run_id": "9" * 16, "result": {}, "rows_sha256": H64},  # never reserved
        {"kind": "completed", "run_id": first["run_id"], "result": {}, "rows_sha256": H64},  # already completed
        {"kind": "failed", "run_id": first["run_id"], "code": "INTERNAL"},  # already completed
        {"kind": "failed", "run_id": "9" * 16, "code": "INTERNAL"},
        {"kind": "failed", "run_id": json.loads(lines[2])["run_id"], "code": "NOT_A_CODE"},
        {"kind": "ack", "incident_id": "9" * 16},  # no such incident
        {"kind": "manifest_seen", "sequence": 0, "manifest_sha256": H64},  # rollback below 1
    ):
        _t7_write(runs, lines + [_t7_forge_line(lines[-1], lambda o, f=fields: o.update(f))])
        _t7_refused(lambda: Ledger(runs).entries())


def test_t7_forged_line_with_consistent_hash_but_wrong_seq_or_prev_is_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    ack = {"kind": "manifest_seen", "sequence": 9, "manifest_sha256": H64}
    for patch in ({"seq": 99}, {"seq": 0}, {"seq": True}, {"prev_sha256": "0" * 64}, {"prev_sha256": H64}):
        prev = json.loads(lines[-1])
        o = {"schema": "zurvan.z1b.ledger.v1", "seq": prev["seq"] + 1, "prev_sha256": prev["entry_sha256"]}
        o.update(ack)
        o.update(patch)
        o["entry_sha256"] = hashlib.sha256(_t7_canon(o)).hexdigest()
        _t7_write(runs, lines + [_t7_canon(o)])
        _t7_refused(lambda: Ledger(runs).entries())
    forged_ok = _t7_forge_line(lines[-1], lambda o: o.update(ack))
    _t7_write(runs, lines + [forged_ok])
    assert Ledger(runs).last_manifest_sequence() == 9  # control: the same forgery, correctly chained, reads


def test_t7_duplicate_json_keys_and_nan_are_invalid(tmp_path):
    led, runs = _t7_build(tmp_path)
    lines = _t7_lines(runs)
    dup = lines[0].replace(b'"seq":0', b'"seq":5,"seq":0')
    _t7_write(runs, [dup] + lines[1:])
    _t7_refused(lambda: Ledger(runs).entries())
    nan = lines[1].replace(b'"n":1', b'"n":NaN')
    _t7_write(runs, lines[:1] + [nan] + lines[2:])
    _t7_refused(lambda: Ledger(runs).entries())


def test_t7_symlinked_or_non_regular_ledger_file_is_invalid(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (tmp_path / "real.jsonl").write_bytes(b"")
    os.symlink(tmp_path / "real.jsonl", runs / "ledger.jsonl")
    _t7_refused(lambda: Ledger(runs).entries())
    os.unlink(runs / "ledger.jsonl")
    os.mkfifo(runs / "ledger.jsonl")
    _t7_refused(lambda: Ledger(runs).entries())


def test_t7_append_refuses_chain_fields_and_unserialisable_values(tmp_path):
    led = Ledger(tmp_path / "runs")
    for bad in ({"kind": "ack", "incident_id": "0" * 16, "seq": 0},
                {"kind": "ack", "incident_id": "0" * 16, "prev_sha256": "x"},
                {"kind": "ack", "incident_id": "0" * 16, "schema": "x"},
                {"kind": "completed", "run_id": "0" * 16, "result": {"x": float("nan")}, "rows_sha256": H64},
                {"kind": "completed", "run_id": "0" * 16, "result": {"x": object()}, "rows_sha256": H64},
                "not a dict"):
        _t7_refused(lambda bad=bad: led.append(bad))
    assert not (tmp_path / "runs" / "ledger.jsonl").exists() or led.entries() == []


# ------------------------------------------------------------------ budget / cache

def test_t7_eight_reserves_then_ninth_needs_renewal(tmp_path):
    led = Ledger(tmp_path / "runs")
    for n in range(8):
        assert led.budget_remaining(SUITE, 8) == 8 - n
        led.reserve_run(_t7_key(n), SUITE, 8)
    assert led.budget_remaining(SUITE, 8) == 0
    _t7_refused(lambda: led.reserve_run(_t7_key(99), SUITE, 8), "SUITE_RENEWAL_REQUIRED")
    assert len(led.entries()) == 8  # the refused attempt wrote nothing


def test_t7_budget_counts_failed_and_crashed_runs_fail_closed(tmp_path):
    led = Ledger(tmp_path / "runs")
    rows = _t7_rows(tmp_path)
    ids = [led.reserve_run(_t7_key(n), SUITE, 8) for n in range(8)]
    led.fail_run(ids[0], "CANDIDATE_FAILED")
    led.fail_run(ids[1], "SANDBOX_VIOLATION")
    led.complete_run(ids[2], {"ok": True}, rows)
    # ids[3..7] never resolve: crashed. Nothing is ever refunded.
    assert led.budget_remaining(SUITE, 8) == 0
    assert len(led.unresolved_runs()) == 5
    _t7_refused(lambda: led.reserve_run(_t7_key(50), SUITE, 8), "SUITE_RENEWAL_REQUIRED")


def test_t7_budget_is_per_suite_id_and_validates_args(tmp_path):
    led = Ledger(tmp_path / "runs")
    led.reserve_run(_t7_key(1), SUITE, 1)
    _t7_refused(lambda: led.reserve_run(_t7_key(2), SUITE, 1), "SUITE_RENEWAL_REQUIRED")
    led.reserve_run(_t7_key(2), "f" * 16, 1)
    _t7_refused(lambda: led.reserve_run(_t7_key(3), SUITE, 0), "SUITE_RENEWAL_REQUIRED")
    _t7_refused(lambda: led.reserve_run("short", SUITE, 8), "BAD_ARGS")
    _t7_refused(lambda: led.reserve_run(_t7_key(3), "zz", 8), "BAD_ARGS")
    _t7_refused(lambda: led.reserve_run(_t7_key(3), SUITE, -1), "BAD_ARGS")
    _t7_refused(lambda: led.reserve_run(_t7_key(3), SUITE, 1.5), "BAD_ARGS")
    _t7_refused(lambda: led.reserve_run(_t7_key(3), SUITE, True), "BAD_ARGS")
    _t7_refused(lambda: led.budget_remaining(SUITE, -1), "BAD_ARGS")


def test_t7_cached_key_returns_result_and_does_not_consume_budget(tmp_path):
    led = Ledger(tmp_path / "runs")
    key = _t7_key(1)
    assert led.cached(key) is None
    rid = led.reserve_run(key, SUITE, 8)
    assert led.cached(key) is None  # reserved is not completed
    led.complete_run(rid, {"ok": True, "metrics": {"mrr": 0.5}}, _t7_rows(tmp_path))
    assert led.cached(key) == {"ok": True, "metrics": {"mrr": 0.5}}
    before = led.budget_remaining(SUITE, 8)
    n_entries = len(led.entries())
    for _ in range(5):
        assert led.reserve_run(key, SUITE, 8) == rid  # no new reservation
    assert led.budget_remaining(SUITE, 8) == before == 7
    assert len(led.entries()) == n_entries
    got = led.cached(key)
    got["ok"] = False  # callers cannot mutate the ledger's view
    assert led.cached(key)["ok"] is True


def test_t7_cached_key_is_served_even_when_budget_is_exhausted(tmp_path):
    led = Ledger(tmp_path / "runs")
    rids = [led.reserve_run(_t7_key(n), SUITE, 2) for n in range(2)]
    led.complete_run(rids[0], {"ok": True}, _t7_rows(tmp_path))
    _t7_refused(lambda: led.reserve_run(_t7_key(5), SUITE, 2), "SUITE_RENEWAL_REQUIRED")
    assert led.reserve_run(_t7_key(0), SUITE, 2) == rids[0]


def test_t7_complete_or_fail_twice_or_unknown_run_is_refused(tmp_path):
    led = Ledger(tmp_path / "runs")
    rows = _t7_rows(tmp_path)
    rid = led.reserve_run(_t7_key(1), SUITE, 8)
    led.complete_run(rid, {"ok": True}, rows)
    _t7_refused(lambda: led.complete_run(rid, {"ok": True}, rows))
    _t7_refused(lambda: led.fail_run(rid, "INTERNAL"))
    _t7_refused(lambda: led.complete_run("9" * 16, {"ok": True}, rows))
    rid2 = led.reserve_run(_t7_key(2), SUITE, 8)
    _t7_refused(lambda: led.fail_run(rid2, "NOT_A_CODE"))
    _t7_refused(lambda: led.complete_run(rid2, {"ok": True}, tmp_path / "missing-rows"))
    _t7_refused(lambda: led.complete_run(rid2, "not-a-dict", rows))
    assert len(led.entries()) == 3  # two reservations and one completion; refusals wrote nothing


def test_t7_completed_entry_binds_the_rows_file_digest(tmp_path):
    led = Ledger(tmp_path / "runs")
    rows = _t7_rows(tmp_path)
    rid = led.reserve_run(_t7_key(1), SUITE, 8)
    led.complete_run(rid, {"ok": True}, rows)
    done = [e for e in led.entries() if e["kind"] == "completed"][0]
    assert done["rows_sha256"] == hashlib.sha256(rows.read_bytes()).hexdigest()


def test_t7_failed_run_key_can_be_retried_at_cost_of_budget(tmp_path):
    led = Ledger(tmp_path / "runs")
    key = _t7_key(1)
    rid = led.reserve_run(key, SUITE, 3)
    led.fail_run(rid, "CANDIDATE_FAILED")
    rid2 = led.reserve_run(key, SUITE, 3)
    assert rid2 != rid
    assert led.budget_remaining(SUITE, 3) == 1


def test_t7_unresolved_same_key_reservation_is_refused(tmp_path):
    led = Ledger(tmp_path / "runs")
    key = _t7_key(1)
    led.reserve_run(key, SUITE, 8)
    _t7_refused(lambda: led.reserve_run(key, SUITE, 8))
    assert led.budget_remaining(SUITE, 8) == 7


# ------------------------------------------------------------------ concurrency

def _t7_race_worker(args):
    runs, key, suite_id, budget, start_at = args
    while time.time() < start_at:
        time.sleep(0.001)
    led = Ledger(Path(runs))
    try:
        return "ok:" + led.reserve_run(key, suite_id, budget)
    except SealRefusal as exc:
        return exc.code


def _t7_pool(n):
    return cf.ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"))


def test_t7_concurrent_reserve_race_16_workers_budget_8_exactly_8_win(tmp_path):
    runs = tmp_path / "runs"
    start_at = time.time() + 4.0
    jobs = [(str(runs), _t7_key(n), SUITE, 8, start_at) for n in range(16)]
    with _t7_pool(16) as pool:
        out = list(pool.map(_t7_race_worker, jobs))
    wins = [o for o in out if o.startswith("ok:")]
    losses = [o for o in out if not o.startswith("ok:")]
    assert len(wins) == 8, out
    assert losses == ["SUITE_RENEWAL_REQUIRED"] * 8, out
    led = Ledger(runs)
    es = led.entries()  # chain valid after the race
    assert len(es) == 8 and all(e["kind"] == "reserved" for e in es)
    assert len({e["run_id"] for e in es}) == 8
    assert led.budget_remaining(SUITE, 8) == 0


def test_t7_same_key_race_16_workers_exactly_one_reservation(tmp_path):
    runs = tmp_path / "runs"
    start_at = time.time() + 4.0
    key = _t7_key(7)
    jobs = [(str(runs), key, SUITE, 8, start_at) for _ in range(16)]
    with _t7_pool(16) as pool:
        out = list(pool.map(_t7_race_worker, jobs))
    wins = [o for o in out if o.startswith("ok:")]
    assert len(wins) == 1, out
    assert out.count("LEDGER_INVALID") == 15, out  # in-flight same key: documented deviation code
    led = Ledger(runs)
    assert len(led.entries()) == 1
    assert led.budget_remaining(SUITE, 8) == 7


def test_t7_lock_is_reentrant_in_process_without_deadlock(tmp_path):
    import threading
    led = Ledger(tmp_path / "runs")
    done = []

    def _go():
        with led.lock():
            with led.lock():
                led.reserve_run(_t7_key(1), SUITE, 8)
        done.append(True)

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(15)
    assert done == [True], "re-entrant lock deadlocked"


def test_t7_lock_excludes_other_processes_and_releases_after_block(tmp_path):
    runs = tmp_path / "runs"
    led = Ledger(runs)
    with led.lock():
        with led.lock():
            led.reserve_run(_t7_key(1), SUITE, 8)
        probe = Ledger(runs)
        import fcntl
        fd = os.open(str(runs / "ledger.lock"), os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        del probe
    fd = os.open(str(runs / "ledger.lock"), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released after the outer block
    finally:
        os.close(fd)


# ------------------------------------------------------------------ incidents / burn / manifest seq

def test_t7_incident_open_acknowledge_closed(tmp_path):
    led = Ledger(tmp_path / "runs")
    assert led.open_incidents() == []
    iid = led.record_incident("canary_hit", ["item-1", "item_2"])
    opened = led.open_incidents()
    assert [i["incident_id"] for i in opened] == [iid]
    assert opened[0]["item_ids"] == ["item-1", "item_2"] and opened[0]["incident_kind"] == "canary_hit"
    led.acknowledge_incident(iid)
    assert led.open_incidents() == []
    _t7_refused(lambda: led.acknowledge_incident(iid))  # no double ack
    _t7_refused(lambda: led.acknowledge_incident("9" * 16))  # unknown
    iid2 = led.record_incident("sandbox_violation", [])
    assert iid2 != iid and [i["incident_id"] for i in led.open_incidents()] == [iid2]


def test_t7_incident_ids_are_unique_for_identical_content(tmp_path):
    led = Ledger(tmp_path / "runs")
    a = led.record_incident("canary_hit", ["x"])
    b = led.record_incident("canary_hit", ["x"])
    assert a != b and len(led.open_incidents()) == 2


@pytest.mark.parametrize("kind,ids", [("Bad Kind", []), ("canary_hit", ["has space"]), ("canary_hit", "notalist"),
                                      ("canary_hit", [""]), ("", []), ("canary_hit", ["a" * 65]), (5, [])])
def test_t7_record_incident_refuses_malformed_input(tmp_path, kind, ids):
    led = Ledger(tmp_path / "runs")
    _t7_refused(lambda: led.record_incident(kind, ids))
    assert led.entries() == []


def test_t7_burn_is_irreversible_union_with_no_unburn_api(tmp_path):
    led = Ledger(tmp_path / "runs")
    a, b = "0a" * 16, "0b" * 16
    led.append({"kind": "burn", "id": a, "reason": "canary_hit", "date": "2026-10-01"})
    assert led.burned_ids() == {a}
    led.append({"kind": "burn", "id": b, "reason": "item_leak", "date": "2026-10-02"})
    led.append({"kind": "burn", "id": a, "reason": "operator", "date": "2026-10-03"})  # re-burn is harmless
    assert led.burned_ids() == {a, b}
    public = {n for n in dir(Ledger) if not n.startswith("_")}
    assert not any(w in n for n in public for w in ("unburn", "restore", "remove", "clear", "delete", "forget"))
    # a fresh object (as after a restart) still sees every burn
    assert Ledger(tmp_path / "runs").burned_ids() == {a, b}
    # burns survive other traffic and incident acknowledgement
    iid = led.record_incident("canary_hit", [a])
    led.acknowledge_incident(iid)
    assert led.burned_ids() == {a, b}


def test_t7_burn_entry_schema_refused_via_append(tmp_path):
    led = Ledger(tmp_path / "runs")
    for bad in ({"kind": "burn", "id": "0a" * 16, "reason": "unburn", "date": "2026-10-01"},
                {"kind": "burn", "id": "0a" * 16, "reason": "canary_hit"},
                {"kind": "burn", "id": "0a" * 32, "reason": "canary_hit", "date": "2026-10-01"},
                {"kind": "burn", "id": "0A" * 16, "reason": "canary_hit", "date": "2026-10-01"},
                {"kind": "burn", "id": "0a" * 16, "reason": "canary_hit", "date": "2026-10-01", "undo": True}):
        _t7_refused(lambda bad=bad: led.append(bad))
    assert led.burned_ids() == set()


def test_t7_last_manifest_sequence_is_monotonic(tmp_path):
    led = Ledger(tmp_path / "runs")
    assert led.last_manifest_sequence() == 0
    led.append({"kind": "manifest_seen", "sequence": 3, "manifest_sha256": H64})
    assert led.last_manifest_sequence() == 3
    led.append({"kind": "manifest_seen", "sequence": 3, "manifest_sha256": H64})
    led.append({"kind": "manifest_seen", "sequence": 5, "manifest_sha256": H64})
    _t7_refused(lambda: led.append({"kind": "manifest_seen", "sequence": 4, "manifest_sha256": H64}))
    _t7_refused(lambda: led.append({"kind": "manifest_seen", "sequence": -1, "manifest_sha256": H64}))
    _t7_refused(lambda: led.append({"kind": "manifest_seen", "sequence": True, "manifest_sha256": H64}))
    assert led.last_manifest_sequence() == 5
    assert Ledger(tmp_path / "runs").last_manifest_sequence() == 5


# ------------------------------------------------------------------ durability

def test_t7_append_fsyncs_file_and_directory(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "runs")
    kinds = []
    real = os.fsync

    def _rec(fd):
        mode = os.fstat(fd).st_mode
        kinds.append("dir" if stat.S_ISDIR(mode) else "file")
        return real(fd)

    monkeypatch.setattr(os, "fsync", _rec)
    led.reserve_run(_t7_key(1), SUITE, 8)
    assert kinds == ["file", "dir"]
    kinds.clear()
    led.append({"kind": "manifest_seen", "sequence": 1, "manifest_sha256": H64})
    assert kinds == ["file", "dir"]


def test_t7_refused_append_writes_nothing_and_fsyncs_nothing(tmp_path, monkeypatch):
    led = Ledger(tmp_path / "runs")
    led.reserve_run(_t7_key(1), SUITE, 8)
    before = (tmp_path / "runs" / "ledger.jsonl").read_bytes()
    calls = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))
    _t7_refused(lambda: led.append({"kind": "ack", "incident_id": "9" * 16}))
    assert (tmp_path / "runs" / "ledger.jsonl").read_bytes() == before
    assert calls == []


def _t7_is_stdlib(module_name):
    """True when `module_name` ships with the RUNNING interpreter's standard library (works on 3.9, which has no
    sys.stdlib_module_names): built in, frozen, or located under sysconfig's stdlib dir and not in site-packages."""
    import importlib.util
    import os
    import sys
    import sysconfig
    if module_name in sys.builtin_module_names:
        return True
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    origin = os.path.realpath(spec.origin)
    stdlib = os.path.realpath(sysconfig.get_paths()["stdlib"])
    return origin.startswith(stdlib + os.sep) and "site-packages" not in origin


def test_t7_ledger_file_is_private_and_modules_are_stdlib_only(tmp_path):
    led = Ledger(tmp_path / "runs")
    led.reserve_run(_t7_key(1), SUITE, 8)
    mode = (tmp_path / "runs" / "ledger.jsonl").stat().st_mode & 0o777
    assert mode == 0o600
    import ast
    tree = ast.parse(Path(L.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert _t7_is_stdlib(a.name.split(".")[0]), a.name
        elif isinstance(node, ast.ImportFrom) and not node.level:
            assert _t7_is_stdlib(node.module.split(".")[0]), node.module
