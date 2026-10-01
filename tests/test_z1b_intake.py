"""Z1b Task 8 tests: bundle intake from the world-writable drop dir (contract s3.7)."""
import hashlib
import os
import signal
import subprocess
from pathlib import Path

import pytest

from scripts.eval_seal import intake, sysenv
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.paths import SealPaths

_T8_RUN = "0123456789abcdef"
_T8_SECRET = b"SEALED-HOLDOUT-ITEM-7 do not leak"
_T8_GIT = "/usr/bin/git"


def _t8_env(home):
    return sysenv.clean_env(Path(home))


def _t8_git(cwd, *args, env):
    out = subprocess.run([_T8_GIT, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                          *args], cwd=str(cwd), env=env, check=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    return out.stdout.decode().strip()


@pytest.fixture
def t8(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    paths = SealPaths.for_root(tmp_path / "root")
    for d in (paths.drop_dir, paths.stage_dir, paths.keys_dir):
        d.mkdir(parents=True)
    env = _t8_env(home)
    repo = tmp_path / "repo"
    repo.mkdir()
    _t8_git(repo, "init", "-q", env=env)
    (repo / "a.txt").write_text("hello\n")
    _t8_git(repo, "add", "a.txt", env=env)
    _t8_git(repo, "commit", "-q", "-m", "one", env=env)
    commit = _t8_git(repo, "rev-parse", "HEAD", env=env)
    bundle = tmp_path / "made.bundle"
    _t8_git(repo, "bundle", "create", str(bundle), "HEAD", env=env)
    name = "z1b-%s-%s.bundle" % (commit, _T8_RUN)
    return {"paths": paths, "env": env, "commit": commit, "bundle": bundle, "name": name,
            "home": home, "repo": repo, "tmp": tmp_path}


def _t8_drop(t8, data=None, name=None):
    target = t8["paths"].drop_dir / (name or t8["name"])
    target.write_bytes(t8["bundle"].read_bytes() if data is None else data)
    return target


def _t8_stage(t8, **kw):
    kw.setdefault("expected_uid", os.getuid())
    kw.setdefault("run_id", _T8_RUN)
    return intake.stage_bundle(kw.pop("name", t8["name"]), t8["paths"], **kw)


def _t8_stage_has(t8, needle):
    for root, _dirs, files in os.walk(t8["paths"].stage_dir):
        for f in files:
            if needle in Path(root, f).read_bytes():
                return True
    return False


def _t8_stage_empty(t8):
    return list(t8["paths"].stage_dir.iterdir()) == []


class _T8Hang(BaseException):
    """BaseException so a blanket `except Exception` in the code under test cannot swallow a hang."""


@pytest.fixture
def t8_alarm():
    def _boom(signum, frame):
        raise _T8Hang("intake hung (FIFO / blocking open)")
    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(15)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


# ---------------------------------------------------------------- happy path

def test_t8_stage_copies_bytes_unlinks_drop_and_sets_mode(t8):
    drop = _t8_drop(t8)
    staged = _t8_stage(t8)
    assert staged == t8["paths"].stage_dir / (_T8_RUN + ".bundle")
    assert staged.read_bytes() == t8["bundle"].read_bytes()
    assert staged.stat().st_mode & 0o137 == 0  # never wider than 0640
    assert not drop.exists()
    assert not drop.is_symlink()


def test_t8_stage_then_verify_accepts_real_bundle(t8):
    _t8_drop(t8)
    staged = _t8_stage(t8)
    intake.verify_bundle(staged, t8["commit"], env=t8["env"])
    # the throwaway verify repo is cleaned up
    assert [p.name for p in t8["paths"].stage_dir.iterdir()] == [staged.name]


# ---------------------------------------------------------------- names

_T8_HEX40 = "a" * 40
_T8_HEX16 = "b" * 16


@pytest.mark.parametrize("bad", [
    "../x", "../../etc/passwd", "/abs/path.bundle", "/Users/zurvaneval/keys/age.key",
    "z1b-%s-%s.bundle" % ("A" * 40, _T8_HEX16),
    "z1b-%s-%s.bundle" % (_T8_HEX40, "B" * 16),
    "z1b-%s-%s.bundle.bak" % (_T8_HEX40, _T8_HEX16),
    "z1b-%s-%s.bundle\n" % (_T8_HEX40, _T8_HEX16),
    "z1b-%s-%s.bundle\x00" % (_T8_HEX40, _T8_HEX16),
    "z1b-%s\x00-%s.bundle" % (_T8_HEX40[:39], _T8_HEX16),
    "z1b-%s-%s.bundle" % (_T8_HEX40[:39], _T8_HEX16),
    "z1b-%s-%s.bundle" % (_T8_HEX40 + "a", _T8_HEX16),
    "z1b-%s-%s.bundle" % (_T8_HEX40, _T8_HEX16[:15]),
    "z1b-%s-%s.bundle" % ("а" * 40, _T8_HEX16),          # cyrillic a
    "z1b-%s-%s.bundle" % ("１" * 40, _T8_HEX16),          # fullwidth digit one
    "z1b-%s-%s.bundle" % ("١" * 40, _T8_HEX16),          # arabic-indic digit
    "ѕ" "1b-%s-%s.bundle" % (_T8_HEX40, _T8_HEX16),      # cyrillic s in the prefix
    "z1b-%s-%s.bundle" % (_T8_HEX40, _T8_HEX16) + "/../x",
    "sub/z1b-%s-%s.bundle" % (_T8_HEX40, _T8_HEX16),
    " z1b-%s-%s.bundle" % (_T8_HEX40, _T8_HEX16),
    "", ".", "..", None, b"z1b", 5,
])
def test_t8_bad_names_are_refused_before_any_filesystem_access(t8, bad):
    # a decoy at the traversal target must never be touched
    decoy = t8["tmp"] / "x"
    decoy.write_bytes(_T8_SECRET)
    with pytest.raises(SealRefusal) as ei:
        intake.stage_bundle(bad, t8["paths"], expected_uid=os.getuid(), run_id=_T8_RUN)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)
    assert decoy.read_bytes() == _T8_SECRET


def test_t8_trailing_newline_name_that_exists_on_disk_is_refused_by_the_regex_alone(t8):
    # the file really exists, so only an exact (fullmatch) name check can stop it
    name = t8["name"] + "\n"
    (t8["paths"].drop_dir / name).write_bytes(t8["bundle"].read_bytes())
    with pytest.raises(SealRefusal) as ei:
        intake.stage_bundle(name, t8["paths"], expected_uid=os.getuid(), run_id=_T8_RUN)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)
    assert (t8["paths"].drop_dir / name).exists()


@pytest.mark.parametrize("bad_run", ["", "../evil", "ABCDEF0123456789", "0123456789abcde", "0123456789abcdef0",
                                     "0123456789abcdef\n", None, 7])
def test_t8_bad_run_id_refused(t8, bad_run):
    _t8_drop(t8)
    with pytest.raises(SealRefusal) as ei:
        intake.stage_bundle(t8["name"], t8["paths"], expected_uid=os.getuid(), run_id=bad_run)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


@pytest.mark.parametrize("bad_uid", [None, "501", 1.0, True])
def test_t8_non_int_expected_uid_refused(t8, bad_uid):
    _t8_drop(t8)
    with pytest.raises(SealRefusal) as ei:
        intake.stage_bundle(t8["name"], t8["paths"], expected_uid=bad_uid, run_id=_T8_RUN)
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_missing_drop_file_refused(t8):
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_missing_drop_dir_refused(t8):
    os.rmdir(t8["paths"].drop_dir)
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_drop_dir_that_is_a_symlink_is_refused(t8):
    real = t8["tmp"] / "elsewhere"
    real.mkdir()
    (real / t8["name"]).write_bytes(t8["bundle"].read_bytes())
    os.rmdir(t8["paths"].drop_dir)
    os.symlink(str(real), str(t8["paths"].drop_dir))
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


# ---------------------------------------------------------------- file types and identity

def test_t8_symlink_to_sealed_file_is_refused_and_bytes_never_staged(t8):
    sealed = t8["paths"].keys_dir / "age.key"
    sealed.write_bytes(_T8_SECRET)
    os.symlink(str(sealed), str(t8["paths"].drop_dir / t8["name"]))
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert not _t8_stage_has(t8, _T8_SECRET)
    assert _t8_stage_empty(t8)
    assert sealed.read_bytes() == _T8_SECRET  # untouched, not unlinked


def test_t8_dangling_symlink_is_refused(t8):
    os.symlink(str(t8["tmp"] / "nowhere"), str(t8["paths"].drop_dir / t8["name"]))
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_hardlink_to_sealed_file_is_refused(t8):
    sealed = t8["paths"].keys_dir / "age.key"
    sealed.write_bytes(_T8_SECRET)
    os.link(str(sealed), str(t8["paths"].drop_dir / t8["name"]))
    assert sealed.stat().st_nlink == 2
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert not _t8_stage_has(t8, _T8_SECRET)
    assert sealed.read_bytes() == _T8_SECRET
    assert sealed.exists()


def test_t8_owner_uid_mismatch_is_refused(t8):
    _t8_drop(t8)
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8, expected_uid=os.getuid() + 1)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


def test_t8_fifo_is_refused_without_hanging(t8, t8_alarm):
    os.mkfifo(str(t8["paths"].drop_dir / t8["name"]))
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


def test_t8_directory_is_refused(t8):
    (t8["paths"].drop_dir / t8["name"]).mkdir()
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


def test_t8_oversize_is_refused(t8, monkeypatch):
    monkeypatch.setattr(intake, "MAX_BUNDLE_BYTES", 10)
    _t8_drop(t8, data=b"x" * 11)
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


def test_t8_exactly_max_size_is_accepted(t8, monkeypatch):
    monkeypatch.setattr(intake, "MAX_BUNDLE_BYTES", 10)
    _t8_drop(t8, data=b"x" * 10)
    assert _t8_stage(t8).read_bytes() == b"x" * 10


def test_t8_file_growing_past_max_during_copy_is_refused_and_partial_removed(t8, monkeypatch):
    monkeypatch.setattr(intake, "MAX_BUNDLE_BYTES", 10)
    monkeypatch.setattr(intake, "_CHUNK", 4)
    drop = _t8_drop(t8, data=b"x" * 8)
    real_read = os.read
    state = {"n": 0}

    def _grow(fd, n):
        state["n"] += 1
        if state["n"] == 1:
            with open(drop, "ab") as fh:
                fh.write(b"y" * 50)
        return real_read(fd, n)

    monkeypatch.setattr(intake.os, "read", _grow)
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert _t8_stage_empty(t8)


# ---------------------------------------------------------------- TOCTOU and stage exclusivity

def test_t8_swap_during_copy_cannot_change_staged_bytes(t8, monkeypatch):
    monkeypatch.setattr(intake, "_CHUNK", 16)
    drop = _t8_drop(t8)
    original = drop.read_bytes()
    assert len(original) > 32
    real_read = os.read
    state = {"n": 0}
    sealed = t8["paths"].keys_dir / "age.key"
    sealed.write_bytes(_T8_SECRET)

    def _swap(fd, n):
        state["n"] += 1
        if state["n"] == 2:  # between two reads, replace the name with a symlink to a sealed file
            os.unlink(drop)
            os.symlink(str(sealed), str(drop))
        return real_read(fd, n)

    monkeypatch.setattr(intake.os, "read", _swap)
    staged = _t8_stage(t8)
    assert staged.read_bytes() == original
    assert not _t8_stage_has(t8, _T8_SECRET)


def test_t8_rewriting_the_drop_path_after_staging_leaves_staged_copy_unchanged(t8):
    drop = _t8_drop(t8)
    original = drop.read_bytes()
    staged = _t8_stage(t8)
    assert not drop.exists()
    drop.write_bytes(b"attacker rewrites after stage")
    os.replace(str(drop), str(t8["tmp"] / "moved"))
    assert staged.read_bytes() == original
    assert hashlib.sha256(staged.read_bytes()).digest() == hashlib.sha256(original).digest()


def test_t8_preexisting_stage_file_is_not_overwritten(t8):
    _t8_drop(t8)
    pre = t8["paths"].stage_dir / (_T8_RUN + ".bundle")
    pre.write_bytes(b"precious")
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert pre.read_bytes() == b"precious"
    assert (t8["paths"].drop_dir / t8["name"]).exists()  # nothing consumed


def test_t8_preexisting_stage_symlink_is_not_followed(t8):
    _t8_drop(t8)
    victim = t8["tmp"] / "victim"
    victim.write_bytes(b"victim")
    os.symlink(str(victim), str(t8["paths"].stage_dir / (_T8_RUN + ".bundle")))
    with pytest.raises(SealRefusal) as ei:
        _t8_stage(t8)
    assert ei.value.code == "BUNDLE_INVALID"
    assert victim.read_bytes() == b"victim"


# ---------------------------------------------------------------- verify_bundle

def test_t8_verify_commit_not_a_listed_head_is_candidate_not_frozen(t8):
    _t8_drop(t8)
    staged = _t8_stage(t8)
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, "c" * 40, env=t8["env"])
    assert ei.value.code == "CANDIDATE_NOT_FROZEN"


def test_t8_verify_interior_commit_that_is_not_a_head_is_not_frozen(t8):
    env, repo = t8["env"], t8["repo"]
    (repo / "b.txt").write_text("two\n")
    _t8_git(repo, "add", "b.txt", env=env)
    _t8_git(repo, "commit", "-q", "-m", "two", env=env)
    parent = t8["commit"]
    bundle = t8["tmp"] / "two.bundle"
    _t8_git(repo, "bundle", "create", str(bundle), "HEAD", env=env)
    staged = t8["paths"].stage_dir / "two.bundle"
    staged.write_bytes(bundle.read_bytes())
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, parent, env=env)
    assert ei.value.code == "CANDIDATE_NOT_FROZEN"


def test_t8_verify_garbage_is_bundle_invalid(t8):
    staged = t8["paths"].stage_dir / "g.bundle"
    staged.write_bytes(b"not a bundle at all\n" * 10)
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, t8["commit"], env=t8["env"])
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_verify_missing_file_is_bundle_invalid(t8):
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(t8["paths"].stage_dir / "none.bundle", t8["commit"], env=t8["env"])
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_verify_corrupted_pack_is_bundle_invalid(t8):
    data = bytearray(t8["bundle"].read_bytes())
    data[-30] ^= 0xFF  # inside the pack: `git bundle verify` alone does not notice this
    staged = t8["paths"].stage_dir / "c.bundle"
    staged.write_bytes(bytes(data))
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, t8["commit"], env=t8["env"])
    assert ei.value.code == "BUNDLE_INVALID"


def test_t8_verify_truncated_bundle_is_bundle_invalid(t8):
    data = t8["bundle"].read_bytes()
    staged = t8["paths"].stage_dir / "t.bundle"
    staged.write_bytes(data[: len(data) // 2])
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, t8["commit"], env=t8["env"])
    assert ei.value.code == "BUNDLE_INVALID"


@pytest.mark.parametrize("bad", ["", "abc", "A" * 40, "a" * 39, "a" * 41, "a" * 40 + "\n", "HEAD", "--upload-pack=x", None])
def test_t8_verify_bad_commit_is_bad_args(t8, bad):
    _t8_drop(t8)
    staged = _t8_stage(t8)
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, bad, env=t8["env"])
    assert ei.value.code == "BAD_ARGS"


def _t8_hostile_config(base):
    marker = base / "fsmonitor-ran"
    hook = base / "hook.sh"
    hook.write_text("#!/bin/sh\ntouch '%s'\nexit 0\n" % marker)
    hook.chmod(0o755)
    cfg = base / "hostile.gitconfig"
    cfg.write_text("[core]\n\tfsmonitor = %s\n\thooksPath = %s\n[uploadpack]\n\tpackObjectsHook = %s\n"
                   "[protocol \"ext\"]\n\tallow = always\n" % (hook, base, hook))
    return marker, cfg


def test_t8_verify_ignores_hostile_ambient_git_environment(t8, monkeypatch):
    base = t8["tmp"] / "hostile"
    base.mkdir()
    marker, cfg = _t8_hostile_config(base)
    (t8["home"] / ".gitconfig").write_text(cfg.read_text())
    monkeypatch.setenv("GIT_DIR", str(base / "nope.git"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(base / "hook.sh"))
    monkeypatch.setenv("GIT_EXEC_PATH", str(base))
    monkeypatch.setenv("HOME", str(t8["home"]))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    _t8_drop(t8)
    staged = _t8_stage(t8)
    intake.verify_bundle(staged, t8["commit"], env=sysenv.clean_env(t8["home"]))
    assert not marker.exists()


@pytest.mark.parametrize("key", ["GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
                                 "GIT_EXEC_PATH", "GIT_INDEX_FILE", "GIT_SSH_COMMAND", "DYLD_INSERT_LIBRARIES",
                                 "PYTHONPATH", "LD_PRELOAD"])
def test_t8_verify_refuses_a_caller_env_that_carries_dangerous_variables(t8, key):
    base = t8["tmp"] / "hostile2"
    base.mkdir()
    marker, cfg = _t8_hostile_config(base)
    _t8_drop(t8)
    staged = _t8_stage(t8)
    env = dict(t8["env"])
    env[key] = str(cfg)
    with pytest.raises(SealRefusal) as ei:
        intake.verify_bundle(staged, t8["commit"], env=env)
    assert ei.value.code == "BUNDLE_INVALID"
    assert not marker.exists()


def test_t8_verify_refuses_an_env_that_unpins_git_config(t8):
    _t8_drop(t8)
    staged = _t8_stage(t8)
    for bad in ({**t8["env"], "GIT_CONFIG_GLOBAL": str(t8["tmp"] / "hostile")},
                {k: v for k, v in t8["env"].items() if k != "GIT_CONFIG_NOSYSTEM"}, {}, None):
        with pytest.raises(SealRefusal) as ei:
            intake.verify_bundle(staged, t8["commit"], env=bad)
        assert ei.value.code == "BUNDLE_INVALID"


def test_t8_intake_is_stdlib_only_and_does_not_import_dev_code():
    import ast
    tree = ast.parse(Path(intake.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level:
            assert (node.module or "").split(".")[0] != "scripts"
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "scripts" for a in node.names)
