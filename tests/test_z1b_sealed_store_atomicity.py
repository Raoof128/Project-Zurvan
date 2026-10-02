"""Z1b Rev 3 P1 #14 Step C: the reseal is crash-ATOMIC via one immutable-generation + pointer swap.

Fault injection at every durable boundary of a real reseal (os.fork a child that crashes with os._exit at
the chosen point, then the parent inspects the on-disk store). The ONLY acceptable post-crash observations
are a readable VALID generation N or N+1 -- never a hybrid, never MANIFEST_INVALID, and never a state that
needs recovery before the active manifest can be read. Also: primitive unit tests + the orphan-GC proof.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import broker as B           # noqa: E402
from scripts.eval_seal import manifest as manifest_mod  # noqa: E402
from scripts.eval_seal import scan as S             # noqa: E402
from scripts.eval_seal import sealed_store as ss    # noqa: E402
from test_z1b_scan import (_t10_ledger, _t11_build, _t11_ctx, _t11_holdout_ids,  # noqa: E402
                           _t11_reserve_ids)

needs_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork unavailable")


# ---------------------------------------------------------------- primitive unit tests

def test_t14_new_generation_is_fresh_and_clears_a_partial(tmp_path):
    E = _t11_build(tmp_path)
    assert ss.active_seq(E.paths) == 1            # the fixture sealed generation 1
    gen = ss.new_generation(E.paths, 2)
    (gen / "stale").write_text("x")               # a partial write
    gen2 = ss.new_generation(E.paths, 2)          # rebuilding the same seq wipes the partial
    assert gen2 == gen and not (gen2 / "stale").exists() and list(gen2.iterdir()) == []


def test_t14_promote_requires_a_complete_generation_and_then_resolves(tmp_path):
    E = _t11_build(tmp_path)
    gen = ss.new_generation(E.paths, 2)
    for name in (ss.HOLDOUT, ss.RESERVE, ss.MANIFEST):     # only three of four
        (gen / name).write_text("x")
    with pytest.raises(ValueError):
        ss.promote(E.paths, 2)                             # incomplete -> refused
    (gen / ss.SIG).write_text("x")
    ss.promote(E.paths, 2)
    assert ss.active_seq(E.paths) == 2 and ss.manifest(E.paths).read_text() == "x"


def test_t14_gc_keeps_the_live_generation_and_reclaims_the_rest(tmp_path):
    E = _t11_build(tmp_path)                       # active = gen 1
    ss.new_generation(E.paths, 2)                  # an orphan (never promoted)
    ss.new_generation(E.paths, 3)
    reclaimed = ss.gc(E.paths)
    assert reclaimed == ["000002", "000003"]
    assert ss.active_seq(E.paths) == 1 and ss.generation_dir(E.paths, 1).is_dir()
    assert not ss.generation_dir(E.paths, 2).exists()


# ---------------------------------------------------------------- the crash gate

def _verify_active(E, expected_seq):
    """The active generation must be readable and VALID with no recovery: a fresh verified-manifest read
    succeeds, names the expected sequence, and its signed digests match the active .age files."""
    led = _t10_ledger(E)
    m, _sha = B.broker_verified_manifest(E.paths, led, record=False)
    assert m["sequence"] == expected_seq, (m["sequence"], expected_seq)
    for pool in ("holdout", "reserve"):
        assert B.broker_file_sha256(ss.age_path(E.paths, pool)) == m[pool + "_age_sha256"]
    for name in ss.GENERATION_FILES:
        assert (ss.active_dir(E.paths) / name).is_file()


def _promote_crash(paths, seq, *, after_replace):
    """A crash variant of sealed_store.promote: stop either just BEFORE the atomic swap or just AFTER it
    (before the parent-dir fsync and the ledger append). Mirrors the real promote's steps."""
    gen = ss.generation_dir(paths, seq)
    ss.fsync_dir(gen)
    if not after_replace:
        os._exit(0)                                        # boundary: complete gen built, swap not done
    link = ss.active_dir(paths)
    tmp = Path(str(paths.eval_home)) / ("active.swap-crash")
    try:
        os.unlink(str(tmp))
    except OSError:
        pass
    os.symlink(os.path.join(ss.GENERATIONS, str(seq).zfill(6)), str(tmp))
    os.replace(str(tmp), str(link))                        # the single atomic swap
    os._exit(0)                                            # boundary: swapped, parent fsync + ledger not done


def _reseal_crashing(E, point):
    """In a forked CHILD, burn an item and run a real reseal, crashing at `point`; os._exit so no pytest
    finalizer runs in the child. The parent waits and returns."""
    pid = os.fork()
    if pid == 0:                                           # child
        try:
            led = _t10_ledger(E)
            victim = next(i for i in E.holdout if i["targets"])
            led.burn_items([victim["id"]], "operator", "2026-10-02")
            ctx = _t11_ctx(E)
            if point == "midwrite":
                real = S.broker_dump_items
                calls = {"n": 0}
                def crashing(items):                       # crash after holdout is dumped, before reserve
                    calls["n"] += 1
                    if calls["n"] >= 2:
                        os._exit(0)
                    return real(items)
                S.broker_dump_items = crashing
            elif point == "before_replace":
                ss.promote = lambda paths, seq: _promote_crash(paths, seq, after_replace=False)
            elif point == "after_replace":
                ss.promote = lambda paths, seq: _promote_crash(paths, seq, after_replace=True)
            elif point == "during_gc":
                ss.gc = lambda paths: os._exit(0)
            S.reseal_after_burn(E.paths, ctx, led)
        except SystemExit:
            raise
        except BaseException:
            pass
        os._exit(0)
    _wpid, _status = os.waitpid(pid, 0)


@needs_fork
@pytest.mark.parametrize("point,expected", [
    ("midwrite", 1),          # crash mid generation-file write -> orphan partial gen2, active still N
    ("before_replace", 1),    # complete gen2 built + fsynced, swap not done -> active still N
    ("after_replace", 2),     # atomic swap done, parent-fsync + ledger append not done -> active N+1
    ("during_gc", 2),         # swapped + parent fsync done, crash at gc -> active N+1
])
def test_t14_a_crash_at_any_reseal_boundary_leaves_a_valid_generation(tmp_path, point, expected):
    E = _t11_build(tmp_path)                 # seals generation 1
    _reseal_crashing(E, point)               # the child crashes at `point`; parent's modules are untouched
    assert ss.active_seq(E.paths) == expected        # never a torn pointer
    _verify_active(E, expected)                      # readable + VALID with no recovery, never hybrid
    # the unreferenced (partial or complete) orphan generation is safe and GC-able
    ss.gc(E.paths)
    assert ss.active_seq(E.paths) == expected
    _verify_active(E, expected)
