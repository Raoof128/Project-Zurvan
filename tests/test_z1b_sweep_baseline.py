"""Z1b post-run sweep: the darwin per-user folders are BASELINE-DIFFED, every other root stays STRICT.

macOS creates /private/var/folders/<xx>/<hash>/{0,C,T,X} (and caches inside) owned by the candidate user, for
good, the first time any process runs as that user (the seal-check probe does). The old sweep treated every
such entry as a leftover, so every production run was a SANDBOX_VIOLATION regardless of what the candidate did.

The fix: snapshot the darwin roots before launch and report only what is new or changed after the run. The
strict roots (/tmp, /private/tmp, /private/var/tmp, /Users/Shared, the candidate home) are unchanged.
Nothing outside tmp_path is touched; the "candidate" is the current uid.
"""
import os
import types

import pytest

from scripts.eval_seal import broker as B
from scripts.eval_seal import sweep
from scripts.eval_seal.paths import SealPaths

from test_z1b_broker import _t10_build, _t10_ctx, _t10_refused, _t10_run, _t10_burned


def _sb_paths(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    for d in (p.scratch_dir, p.candidate_home):
        d.mkdir(parents=True, exist_ok=True)
    return p


def _sb_folders(tmp_path):
    """<base>/<xx>/<hash>/{0,C,T,X} with a cache entry, as macOS lays it out; returns (base, user_root)."""
    base = tmp_path / "folders"
    root = base / "zz" / "hash0123"
    for sub in ("0", "C", "T", "X"):
        (root / sub).mkdir(parents=True)
    (root / "0" / "com.apple.LaunchServices.dv").mkdir()
    (root / "0" / "com.apple.LaunchServices.dv" / "cache.db").write_text("cache")
    (root / "C" / "entry").write_text("c")
    return base, root


def _sb_bump(path, size_delta=1):
    """Change a file's size and mtime for certain, whatever the filesystem timestamp granularity."""
    with open(str(path), "a") as fh:
        fh.write("x" * size_delta)
    st = os.lstat(str(path))
    os.utime(str(path), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))


# ================================================================ the module-level diff

def test_sb_preexisting_darwin_entries_with_nothing_changed_are_not_leftovers(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    assert str(root / "0" / "com.apple.LaunchServices.dv" / "cache.db") in snap   # the baseline did see them
    assert sweep.darwin_changes(uid, snap, str(base)) == []


def test_sb_a_file_created_under_the_darwin_root_during_the_run_is_a_leftover(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    (root / "T" / "dropped-by-candidate").write_text("x")
    assert sweep.darwin_changes(uid, snap, str(base)) == [str(root / "T" / "dropped-by-candidate")]


def test_sb_a_preexisting_file_modified_during_the_run_is_a_leftover(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    target = root / "0" / "com.apple.LaunchServices.dv" / "cache.db"
    snap = sweep.snapshot_darwin(uid, str(base))
    _sb_bump(target)
    assert sweep.darwin_changes(uid, snap, str(base)) == [str(target)]


def test_sb_a_same_size_rewrite_is_caught_by_mtime_and_a_chmod_by_mode(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    a, b = root / "C" / "entry", root / "X" / "other"
    b.write_text("o")
    snap = sweep.snapshot_darwin(uid, str(base))
    st = os.lstat(str(a))
    os.utime(str(a), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))   # same size, newer mtime
    os.chmod(str(b), 0o600 if (os.lstat(str(b)).st_mode & 0o777) != 0o600 else 0o640)
    assert sweep.darwin_changes(uid, snap, str(base)) == sorted([str(a), str(b)])


def test_sb_a_same_size_rewrite_with_the_mtime_restored_is_still_caught_by_ctime(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    target = root / "C" / "entry"
    target.write_text("aaaa")
    snap = sweep.snapshot_darwin(uid, str(base))
    st = os.lstat(str(target))
    target.write_text("bbbb")                                          # same size
    os.utime(str(target), ns=(st.st_atime_ns, st.st_mtime_ns))         # mtime put back by the candidate
    assert os.lstat(str(target)).st_mtime_ns == st.st_mtime_ns and os.lstat(str(target)).st_size == st.st_size
    assert sweep.darwin_changes(uid, snap, str(base)) == [str(target)]


def test_sb_a_path_that_disappeared_is_not_a_violation(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    os.unlink(str(root / "C" / "entry"))
    (root / "T").rmdir()
    assert sweep.darwin_changes(uid, snap, str(base)) == []


def test_sb_a_directory_losing_a_child_is_not_a_violation(tmp_path):
    # a directory's own mtime moves when a child is deleted; that must not read as a change to the directory
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    d = root / "0" / "com.apple.LaunchServices.dv"
    os.unlink(str(d / "cache.db"))
    os.utime(str(d), ns=(1, 2 * 10 ** 9))
    assert sweep.darwin_changes(uid, snap, str(base)) == []


def test_sb_a_darwin_root_that_appears_after_the_baseline_contributes_everything(tmp_path):
    base = tmp_path / "folders"
    base.mkdir()
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    assert snap == {}
    root = base / "qq" / "newhash"
    (root / "T").mkdir(parents=True)
    (root / "T" / "f").write_text("x")
    assert sweep.darwin_changes(uid, snap, str(base)) == sorted([str(root), str(root / "T"), str(root / "T" / "f")])


def test_sb_the_diff_never_follows_symlinks_and_reports_a_new_link_itself(tmp_path):
    base, root = _sb_folders(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inner.txt").write_text("x")
    uid = os.getuid()
    os.symlink(str(outside), str(root / "T" / "oldlink"))
    snap = sweep.snapshot_darwin(uid, str(base))
    assert not any(str(outside) in k and "inner" in k for k in snap)
    (outside / "inner.txt").write_text("changed behind the link")   # not under the root: invisible
    os.symlink(str(outside), str(root / "T" / "newlink"))
    assert sweep.darwin_changes(uid, snap, str(base)) == [str(root / "T" / "newlink")]


def test_sb_entries_owned_by_another_uid_are_neither_snapshotted_nor_reported(tmp_path):
    base, root = _sb_folders(tmp_path)
    assert sweep.snapshot_darwin(os.getuid() + 1, str(base)) == {}
    snap = sweep.snapshot_darwin(os.getuid() + 1, str(base))
    (root / "T" / "new").write_text("x")
    assert sweep.darwin_changes(os.getuid() + 1, snap, str(base)) == []


@pytest.mark.parametrize("bad", [True, -1, "5", None, 0])
def test_sb_bad_uids_are_refused(tmp_path, bad):
    from scripts.eval_seal.codes import SealRefusal
    with pytest.raises(SealRefusal) as ei:
        sweep.snapshot_darwin(bad, str(tmp_path))
    assert ei.value.code == "BAD_ARGS"
    with pytest.raises(SealRefusal) as ei:
        sweep.darwin_changes(bad, {}, str(tmp_path))
    assert ei.value.code == "BAD_ARGS"


def test_sb_a_missing_folders_base_snapshots_empty(tmp_path):
    assert sweep.snapshot_darwin(os.getuid(), str(tmp_path / "missing")) == {}


# ================================================================ unlistable directories are opaque, not blind

_sb_needs_nonroot = pytest.mark.skipif(os.geteuid() == 0, reason="chmod-based denial does not bind root")


@pytest.fixture
def _sb_opaque(tmp_path):
    """A candidate-owned darwin tree with a mode-000 directory (as production's eval identity sees the
    candidate's 0700 C/ and T/). Yields (base, opaque_dir, lock, unlock); perms are always restored."""
    base, root = _sb_folders(tmp_path)
    d = root / "T"
    (d / "kept").write_text("x")

    def lock():
        os.chmod(str(d), 0o000)

    def unlock():
        os.chmod(str(d), 0o700)

    lock()
    try:
        yield base, d, lock, unlock
    finally:
        unlock()


@_sb_needs_nonroot
def test_sb_an_unchanged_unlistable_directory_is_not_reported(_sb_opaque):
    base, d, _lock, _unlock = _sb_opaque
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    assert str(d) in snap                                   # the directory itself is recorded
    assert not any(k.startswith(str(d) + os.sep) for k in snap)   # its children cannot be seen
    assert sweep.darwin_changes(uid, snap, str(base)) == []


@_sb_needs_nonroot
def test_sb_a_file_created_inside_an_unlistable_directory_is_reported_as_the_directory_changing(_sb_opaque):
    base, d, lock, unlock = _sb_opaque
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    before = os.lstat(str(d)).st_mtime_ns
    unlock()
    (d / "dropped-by-escaped-process").write_text("x")
    os.utime(str(d), ns=(before, before + 10 ** 9))          # make the mtime advance whatever the FS granularity
    lock()
    assert sweep.darwin_changes(uid, snap, str(base)) == [str(d)]


@_sb_needs_nonroot
def test_sb_an_opaque_signature_cannot_collide_with_a_listable_directorys(_sb_opaque):
    base, d, _lock, unlock = _sb_opaque
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    unlock()                                                # same dir, now listable: a different signature
    assert sweep.darwin_changes(uid, snap, str(base)) == sorted([str(d), str(d / "kept")])


@_sb_needs_nonroot
def test_sb_a_listable_directorys_mtime_still_does_not_count(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    st = os.lstat(str(root / "C"))
    os.utime(str(root / "C"), ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    assert sweep.darwin_changes(uid, snap, str(base)) == []


@_sb_needs_nonroot
def test_sb_an_unlistable_directorys_ctime_counts_but_a_listable_directorys_does_not(_sb_opaque):
    base, d, lock, unlock = _sb_opaque
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    st = os.lstat(str(d))
    unlock()
    (d / "dropped").write_text("x")
    (d / "dropped").unlink()                                           # contents identical again
    os.utime(str(d), ns=(st.st_atime_ns, st.st_mtime_ns))              # mtime restored: only ctime moved
    lock()
    assert sweep.darwin_changes(uid, snap, str(base)) == [str(d)]


# ================================================================ strict roots stay strict

def test_sb_sweep_roots_are_the_strict_roots_and_exclude_the_darwin_trees(tmp_path):
    paths = _sb_paths(tmp_path)
    roots = sweep.sweep_roots(paths, os.getuid())
    assert roots == tuple(sweep.DEFAULT_ROOTS) + (str(paths.candidate_home),)


def test_sb_a_candidate_owned_entry_in_a_strict_root_is_a_leftover_even_if_it_predates_launch(tmp_path):
    paths = _sb_paths(tmp_path)
    strict = tmp_path / "strictroot"
    strict.mkdir()
    (strict / "old").write_text("x")          # exists BEFORE any baseline is taken
    base, _root = _sb_folders(tmp_path)
    uid = os.getuid()
    snap = sweep.snapshot_darwin(uid, str(base))
    assert sweep.find_candidate_leftovers(paths, uid, roots=[str(strict)]) == [str(strict / "old")]
    assert sweep.darwin_changes(uid, snap, str(base)) == []


# ================================================================ the broker: orchestration

def _sb_ctx(E, base, **over):
    uid = os.getuid()
    seams = dict(sweep_baseline=lambda: sweep.snapshot_darwin(uid, str(base)),
                 sweep_darwin_changes=lambda b: sweep.darwin_changes(uid, b, str(base)))
    seams.update(over)
    return _t10_ctx(E, **seams)


def test_sb_run_with_preexisting_darwin_entries_and_a_quiet_candidate_completes(tmp_path):
    E = _t10_build(tmp_path)
    base, _root = _sb_folders(tmp_path)
    rc, _text, doc = _t10_run(E, ctx=_sb_ctx(E, base))
    assert rc == 0 and doc["ok"] is True
    assert _t10_burned(E) == set()


def test_sb_run_where_the_candidate_creates_a_darwin_file_is_a_sandbox_violation(tmp_path):
    E = _t10_build(tmp_path)
    base, root = _sb_folders(tmp_path)
    inner = _t10_ctx(E)

    def launching(run_id, corpus_sha, commit):
        (root / "T" / "dropped").write_text("x")      # between baseline and sweep
        return inner.launch_candidate(run_id, corpus_sha, commit)

    _t10_refused(_t10_run(E, ctx=_sb_ctx(E, base, launch_candidate=launching)), "SANDBOX_VIOLATION")
    assert _t10_burned(E)


def test_sb_run_where_the_candidate_modifies_a_preexisting_darwin_file_is_a_sandbox_violation(tmp_path):
    E = _t10_build(tmp_path)
    base, root = _sb_folders(tmp_path)
    inner = _t10_ctx(E)

    def launching(run_id, corpus_sha, commit):
        _sb_bump(root / "C" / "entry")
        return inner.launch_candidate(run_id, corpus_sha, commit)

    _t10_refused(_t10_run(E, ctx=_sb_ctx(E, base, launch_candidate=launching)), "SANDBOX_VIOLATION")


def test_sb_the_baseline_is_taken_strictly_before_the_candidate_is_launched(tmp_path):
    E = _t10_build(tmp_path)
    base, _root = _sb_folders(tmp_path)
    order = []
    inner = _t10_ctx(E)

    def launching(run_id, corpus_sha, commit):
        order.append("launch")
        return inner.launch_candidate(run_id, corpus_sha, commit)

    uid = os.getuid()

    def baseline():
        order.append("baseline")
        return sweep.snapshot_darwin(uid, str(base))

    assert _t10_run(E, ctx=_sb_ctx(E, base, launch_candidate=launching, sweep_baseline=baseline))[0] == 0
    assert order == ["baseline", "launch"]


@pytest.mark.parametrize("exc", [OSError("folders unreadable"), RuntimeError("boom")])
def test_sb_a_baseline_that_cannot_be_taken_is_a_sandbox_violation(tmp_path, exc):
    E = _t10_build(tmp_path)
    base, _root = _sb_folders(tmp_path)

    def broken():
        raise exc

    _t10_refused(_t10_run(E, ctx=_sb_ctx(E, base, sweep_baseline=broken)), "SANDBOX_VIOLATION")
    assert _t10_burned(E)


def test_sb_a_diff_that_cannot_be_computed_is_a_sandbox_violation(tmp_path):
    E = _t10_build(tmp_path)
    base, _root = _sb_folders(tmp_path)

    def exploding(_baseline):
        raise OSError("vanished")

    _t10_refused(_t10_run(E, ctx=_sb_ctx(E, base, sweep_darwin_changes=exploding)), "SANDBOX_VIOLATION")


def test_sb_the_strict_leftovers_seam_still_decides_independently_of_the_darwin_diff(tmp_path):
    E = _t10_build(tmp_path)
    base, _root = _sb_folders(tmp_path)
    ctx = _sb_ctx(E, base, sweep_leftovers=lambda: ["/private/tmp/dropped-by-candidate"])
    _t10_refused(_t10_run(E, ctx=ctx), "SANDBOX_VIOLATION")


def test_sb_broker_post_run_violation_passes_the_baseline_to_the_diff(tmp_path):
    seen = []
    ctx = types.SimpleNamespace(wipe=lambda r: None, sweep_leftovers=lambda: [], sweep_processes=lambda: 0,
                                sweep_darwin_changes=lambda b: seen.append(b) or [])
    assert B.broker_post_run_violation(ctx, "r" * 16, {"k": 1}) is False
    assert seen == [{"k": 1}]
    ctx.sweep_darwin_changes = lambda b: ["/x"]
    assert B.broker_post_run_violation(ctx, "r" * 16, {}) is True


def _sb_proc_ctx(procs, calls, leftovers=(), changes=()):
    seq = list(procs)
    return types.SimpleNamespace(
        wipe=lambda r: calls.append("wipe"),
        sweep_processes=lambda: calls.append("procs") or seq.pop(0),
        sweep_leftovers=lambda: calls.append("leftovers") or list(leftovers),
        sweep_darwin_changes=lambda b: calls.append("darwin") or list(changes))


def test_sb_a_live_process_before_the_filesystem_sweep_is_a_violation_without_touching_the_fs_seams(tmp_path):
    calls = []
    ctx = types.SimpleNamespace(wipe=lambda r: calls.append("wipe"),
                                sweep_processes=lambda: calls.append("procs") or [1, 0].pop(0))   # always 1
    assert B.broker_post_run_violation(ctx, "r" * 16, {}) is True      # no fs seams exist on this ctx at all
    assert calls == ["wipe", "procs"]


def test_sb_the_sweep_order_is_processes_then_strict_then_darwin_then_processes_again(tmp_path):
    calls = []
    ctx = _sb_proc_ctx([0, 0], calls)
    assert B.broker_post_run_violation(ctx, "r" * 16, {}) is False
    assert calls == ["wipe", "procs", "leftovers", "darwin", "procs"]


def test_sb_a_process_that_appears_during_the_filesystem_sweep_is_caught_by_the_second_check(tmp_path):
    calls = []
    assert B.broker_post_run_violation(_sb_proc_ctx([0, 2], calls), "r" * 16, {}) is True
    assert calls == ["wipe", "procs", "leftovers", "darwin", "procs"]


def test_sb_the_first_process_check_failing_is_a_violation(tmp_path):
    def boom():
        raise OSError("ps vanished")

    ctx = types.SimpleNamespace(wipe=lambda r: None, sweep_processes=boom)
    assert B.broker_post_run_violation(ctx, "r" * 16, {}) is True


# ================================================================ the production wiring

def test_sb_production_context_wires_strict_roots_and_the_darwin_diff(tmp_path, monkeypatch):
    uid = os.getuid()
    paths = _sb_paths(tmp_path)
    base, root = _sb_folders(tmp_path)
    strict = tmp_path / "strictroot"
    strict.mkdir()
    monkeypatch.setattr(B.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_uid=uid))
    monkeypatch.setattr(sweep, "FOLDERS_BASE", str(base))
    monkeypatch.setattr(sweep, "DEFAULT_ROOTS", (str(strict),))
    monkeypatch.setattr(B, "broker_vm_leftovers", lambda p: [])
    calls = _sb_door(monkeypatch, base)
    ctx = B.production_context(paths)
    assert ctx.candidate_uid == uid

    baseline = ctx.sweep_baseline()
    assert ctx.sweep_leftovers() == []                         # the macOS-owned folder is NOT a strict leftover
    assert ctx.sweep_darwin_changes(baseline) == []

    (root / "T" / "dropped").write_text("x")
    assert ctx.sweep_darwin_changes(baseline) == [str(root / "T" / "dropped")]
    (strict / "dropped").write_text("x")
    assert ctx.sweep_leftovers() == [str(strict / "dropped")]
    # both darwin snapshots came through the door, as the candidate -- never read by the eval identity
    assert calls and all(c[:4] == ["/usr/bin/sudo", "-n", "-u", "zurvancandidate"] and c[-1] == "darwin-snapshot"
                         for c in calls)


# ================================================================ the darwin snapshot is taken AS THE CANDIDATE (gate 4, 2026-11-08)
# The first charged gate-4 run after the 2026-11-07 fix still ended SANDBOX_VIOLATION: the candidate's T/ is 0700,
# so from the eval identity it was OPAQUE and its own mtime was the only evidence -- and a transient temp file
# created and removed by the host-side VM machinery (running as the candidate) moved it at 12:16:57, inside the
# run window. The door now lists the candidate's own folders as the candidate and the broker diffs real entries.

from scripts.eval_seal import candidate_run as CR  # noqa: E402


def _sb_door(monkeypatch, base, rc=None, stdout=None):
    """Route the broker's door call for `darwin-snapshot` through candidate_run.main in-process (the test uid
    plays the candidate); other tool calls are refused. Returns the list of door argv seen."""
    import io
    import subprocess as sp
    calls = []

    def run_tool(cmd, env, *, data=None, timeout=B.CHECK_TIMEOUT):
        calls.append(list(cmd))
        if cmd[-1] != "darwin-snapshot":
            raise OSError("unexpected tool in this test: %r" % (cmd,))
        if rc is not None:
            return sp.CompletedProcess(cmd, rc, stdout or b"", b"")
        buf = io.StringIO()
        monkeypatch.setattr(CR.sys, "stdout", buf)
        code = CR.main(["darwin-snapshot"], darwin_base=str(base))
        return sp.CompletedProcess(cmd, code, buf.getvalue().encode(), b"")
    monkeypatch.setattr(B, "_broker_run_tool", run_tool)
    return calls


def test_sb_snapshot_round_trips_through_its_serialized_form(tmp_path):
    base, root = _sb_folders(tmp_path)
    snap = sweep.snapshot_darwin(os.getuid(), str(base))
    assert snap and sweep.parse_snapshot(sweep.serialize_snapshot(snap), str(base)) == snap
    assert sweep.parse_snapshot(sweep.serialize_snapshot(snap).encode(), str(base)) == snap  # bytes from a pipe


@pytest.mark.parametrize("doc", [
    "", "not json", "[]", "{}",
    '{"schema":"zurvan.z1b.darwin-snapshot.v0","entries":[]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[],"extra":1}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":{}}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["/elsewhere/x",[1,2,3,4,5,false]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a/../../etc",[1,2,3,4,5,false]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a",[1,2,3,4,5]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a",[1,2,3,4,5,0]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a",[1,2,3,4,true,false]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a",[1,2,3,4,-5,false]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a",[1,2,3,4,5.0,false]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a",[1,2,3,4,5,false]],["BASE/a",[1,2,3,4,5,false]]]}',
    '{"schema":"zurvan.z1b.darwin-snapshot.v1","entries":[["BASE/a\\nb",[1,2,3,4,5,false]]]}',
])
def test_sb_a_malformed_snapshot_is_refused_as_a_violation(tmp_path, doc):
    base = str(tmp_path / "folders")
    with pytest.raises(B.SealRefusal) as ei:
        sweep.parse_snapshot(doc.replace("BASE", base), base)
    assert ei.value.code == "SANDBOX_VIOLATION"


def test_sb_a_transient_file_the_vm_machinery_made_and_removed_is_not_a_change(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    before = sweep.snapshot_darwin(uid, str(base))
    (root / "T" / "lima-vz-transient").write_text("x")       # T/ seen AS ITS OWNER: listable, not opaque
    (root / "T" / "lima-vz-transient").unlink()
    assert sweep.diff_snapshots(before, sweep.snapshot_darwin(uid, str(base))) == []


def test_sb_a_residue_in_the_candidates_own_t_is_still_a_change(tmp_path):
    base, root = _sb_folders(tmp_path)
    uid = os.getuid()
    before = sweep.snapshot_darwin(uid, str(base))
    (root / "T" / "sub").mkdir()
    (root / "T" / "sub" / "leak").write_text("query text")
    _sb_bump(root / "C" / "entry")
    assert sweep.diff_snapshots(before, sweep.snapshot_darwin(uid, str(base))) == sorted(
        [str(root / "T" / "sub"), str(root / "T" / "sub" / "leak"), str(root / "C" / "entry")])


def test_sb_door_prints_the_candidates_snapshot_and_refuses_any_other_uid(tmp_path, monkeypatch, capsys):
    base, _root = _sb_folders(tmp_path)
    uid = os.getuid()
    monkeypatch.setattr(CR.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_uid=uid))
    assert CR.main(["darwin-snapshot"], darwin_base=str(base)) == 0
    out = capsys.readouterr().out
    assert sweep.parse_snapshot(out, str(base)) == sweep.snapshot_darwin(uid, str(base))
    monkeypatch.setattr(CR.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_uid=uid + 1))
    assert CR.main(["darwin-snapshot"], darwin_base=str(base)) == 2      # not the candidate: nothing printed
    assert capsys.readouterr().out == ""
    assert CR.main(["darwin-snapshot", "extra"], darwin_base=str(base)) == 2


@pytest.mark.parametrize("rc,stdout", [(2, b""), (3, b""), (0, b"garbage"), (0, b"")])
def test_sb_a_failed_or_garbled_door_snapshot_is_a_violation_before_and_after(tmp_path, monkeypatch, rc, stdout):
    uid = os.getuid()
    paths = _sb_paths(tmp_path)
    base, _root = _sb_folders(tmp_path)
    monkeypatch.setattr(B.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_uid=uid))
    monkeypatch.setattr(sweep, "FOLDERS_BASE", str(base))
    _sb_door(monkeypatch, base, rc=rc, stdout=stdout)
    ctx = B.production_context(paths)
    assert B.broker_sweep_baseline(ctx) is B._BASELINE_FAILED          # before launch
    with pytest.raises(B.SealRefusal):
        ctx.sweep_darwin_changes({})                                    # after teardown
    probe = types.SimpleNamespace(wipe=lambda r: None, sweep_processes=lambda: 0, sweep_leftovers=lambda: [],
                                  sweep_darwin_changes=ctx.sweep_darwin_changes)
    assert B.broker_post_run_violation(probe, "r" * 16, {}) is True
