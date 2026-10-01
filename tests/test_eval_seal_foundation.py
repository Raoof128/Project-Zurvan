"""Z1b Task 1 tests: refusal codes, sealed paths, clean environment (contract s1, s2, s3.11)."""
import inspect
import os
import pwd
import sys
from pathlib import Path

import pytest

from scripts.eval_seal import codes, paths, sysenv
from scripts.eval_seal.codes import ADDED_CODES, ALL_CODES, SPEC_CODES, SealRefusal
from scripts.eval_seal.paths import SealPaths


# ---------------------------------------------------------------- codes

def test_t1_code_sets_are_disjoint_and_complete():
    assert len(ALL_CODES) == len(set(ALL_CODES))
    assert ALL_CODES == SPEC_CODES + ADDED_CODES
    assert len(SPEC_CODES) == 8
    assert "INTERNAL" in ADDED_CODES


@pytest.mark.parametrize("code", ALL_CODES)
def test_t1_known_code_round_trips_and_str_is_only_the_code(code):
    err = SealRefusal(code)
    assert err.code == code
    assert str(err) == code
    assert err.args == (code,)


@pytest.mark.parametrize("bad", [
    "NOPE", "", "seal_not_configured", " BAD_ARGS", "BAD_ARGS\n",
    "/Users/zurvaneval/keys/age.key", "secret holdout item 7", None, 5, b"BAD_ARGS", ("BAD_ARGS",),
])
def test_t1_unknown_code_collapses_to_internal_without_leaking(bad):
    err = SealRefusal(bad)
    assert err.code == "INTERNAL"
    assert str(err) == "INTERNAL"
    assert err.args == ("INTERNAL",)
    if isinstance(bad, str) and len(bad) > 3:
        assert bad not in repr(err)


def test_t1_rigged_str_subclass_is_not_accepted_as_a_code():
    class _T1Rigged(str):
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

        def __str__(self):
            return "leaked holdout text"

    err = SealRefusal(_T1Rigged("BAD_ARGS"))
    assert err.code == "INTERNAL"
    assert str(err) == "INTERNAL"


def test_t1_clean_env_never_contains_inherited_value_even_when_extra_given(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("PATH", "/evil")
    env = sysenv.clean_env(tmp_path, {"TMPDIR": "/s"})
    assert "SECRET_TOKEN" not in env
    assert set(env) == _T1_BASE_KEYS | {"TMPDIR"}


def test_t1_refusal_is_an_exception_and_str_never_more_than_code():
    try:
        raise SealRefusal("BROKER_STALE")
    except Exception as exc:  # noqa: BLE001
        assert str(exc) == "BROKER_STALE"
        assert repr(exc) == "SealRefusal('BROKER_STALE')"


# ---------------------------------------------------------------- paths

_T1_ENV_ATTACKS = {
    "ZURVAN_ROOT": "/tmp/evil-root", "HOME": "/tmp/evil-home", "ZURVAN_SEAL_ROOT": "/tmp/evil-seal",
    "SEAL_ROOT": "/tmp/evil2", "TMPDIR": "/tmp/evil-tmp", "PWD": "/tmp/evil-pwd", "XDG_DATA_HOME": "/tmp/evil3",
}


def _t1_path_props():
    """Every public property on SealPaths whose getter is annotated to return Path (auto-covers new ones)."""
    names = []
    for name, member in inspect.getmembers(SealPaths):
        if name.startswith("_") or not isinstance(member, property):
            continue
        ann = member.fget.__annotations__.get("return")
        if ann in (Path, "Path"):
            names.append(name)
    return names


def test_t1_path_property_introspection_finds_the_contract_properties():
    found = set(_t1_path_props())
    expected = {
        "broker_dir", "broker_bin", "broker_lib", "installed_sha_file", "runtime_dir", "corpus_dir",
        "stage_dir", "eval_home", "keys_dir", "sets_dir", "runs_dir", "incidents_dir", "manifest_file",
        "candidate_home", "scratch_dir", "drop_dir", "sudoers_file", "pf_anchor",
    }
    assert expected <= found


def test_t1_every_public_property_is_path_annotated():
    # a new property that returns a non-Path would dodge the containment test; force explicit annotation
    for name, member in inspect.getmembers(SealPaths):
        if name.startswith("_") or not isinstance(member, property):
            continue
        assert member.fget.__annotations__.get("return") in (Path, "Path"), name


def test_t1_real_ignores_every_environment_variable(monkeypatch):
    baseline = SealPaths.real()
    for key, val in _T1_ENV_ATTACKS.items():
        monkeypatch.setenv(key, val)
    attacked = SealPaths.real()
    assert attacked == baseline
    assert attacked.root == Path("/")
    for name in _t1_path_props():
        got = getattr(attacked, name)
        assert str(got).startswith("/")
        assert "evil" not in str(got)
        assert got == getattr(baseline, name)


def test_t1_real_is_hard_coded_real_locations():
    p = SealPaths.real()
    assert p.broker_dir == Path("/Library/ZurvanEval/broker")
    assert p.broker_bin == Path("/Library/ZurvanEval/broker/bin")
    assert p.broker_lib == Path("/Library/ZurvanEval/broker/lib")
    assert p.installed_sha_file == Path("/Library/ZurvanEval/broker/INSTALLED_SHA")
    assert p.runtime_dir == Path("/Library/ZurvanEval/runtime")
    assert p.corpus_dir == Path("/Library/ZurvanEval/corpus")
    assert p.stage_dir == Path("/Library/ZurvanEval/stage")
    assert p.eval_home == Path("/Users/zurvaneval")
    assert p.keys_dir == Path("/Users/zurvaneval/keys")
    assert p.sets_dir == Path("/Users/zurvaneval/sets")
    assert p.runs_dir == Path("/Users/zurvaneval/runs")
    assert p.incidents_dir == Path("/Users/zurvaneval/incidents")
    assert p.manifest_file == Path("/Users/zurvaneval/manifest.json")
    assert p.candidate_home == Path("/Users/zurvancandidate")
    assert p.scratch_dir == Path("/Users/zurvancandidate/scratch")
    assert p.drop_dir == Path("/Users/Shared/zurvan-eval-drop")
    assert p.sudoers_file == Path("/etc/sudoers.d/zurvan-eval")
    assert p.pf_anchor == Path("/etc/pf.anchors/zurvan-eval")


def test_t1_for_root_confines_every_path_under_root(tmp_path, monkeypatch):
    for key, val in _T1_ENV_ATTACKS.items():
        monkeypatch.setenv(key, val)
    p = SealPaths.for_root(tmp_path)
    assert p.root == tmp_path
    names = _t1_path_props()
    assert len(names) >= 18
    for name in names:
        got = getattr(p, name)
        assert isinstance(got, Path), name
        assert got.is_absolute(), name
        got.relative_to(tmp_path)  # raises ValueError when escaped
        assert ".." not in got.parts, name
        assert got != tmp_path, name


def test_t1_for_root_matches_real_layout_relative_to_root(tmp_path):
    real, fake = SealPaths.real(), SealPaths.for_root(tmp_path)
    for name in _t1_path_props():
        assert getattr(fake, name) == tmp_path / getattr(real, name).relative_to("/"), name


def test_t1_for_root_rejects_relative_root():
    with pytest.raises(ValueError):
        SealPaths.for_root(Path("relative/root"))


def test_t1_sealpaths_is_frozen(tmp_path):
    p = SealPaths.for_root(tmp_path)
    with pytest.raises(Exception):
        p.root = Path("/")  # type: ignore[misc]


def test_t1_identity_constants():
    assert paths.EVAL_USER == "zurvaneval"
    assert paths.CANDIDATE_USER == "zurvancandidate"
    assert paths.CORPUS_GROUP == "zurvancorpus"


# ---------------------------------------------------------------- sysenv

_T1_BASE_KEYS = {
    "PATH", "HOME", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_TERMINAL_PROMPT", "GIT_ATTR_NOSYSTEM",
}

_T1_POISON = {
    "PYTHONPATH": "/evil/py", "PATH": "/evil", "GIT_DIR": "/evil/.git", "GIT_CONFIG_GLOBAL": "/evil/gitconfig",
    "DYLD_INSERT_LIBRARIES": "/evil/lib.dylib", "HOME": "/evil/home", "LD_PRELOAD": "/evil/x.so",
    "PYTHONSTARTUP": "/evil/s.py", "GIT_SSH_COMMAND": "evil", "TMPDIR": "/evil/tmp", "SUDO_USER": "evil",
}


def test_t1_clean_env_inherits_nothing(monkeypatch, tmp_path):
    for key, val in _T1_POISON.items():
        monkeypatch.setenv(key, val)
    env = sysenv.clean_env(tmp_path)
    assert set(env) == _T1_BASE_KEYS
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert env["HOME"] == str(tmp_path)
    assert env["LANG"] == "C" and env["LC_ALL"] == "C"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ATTR_NOSYSTEM"] == "1"
    assert "TMPDIR" not in env
    assert all("evil" not in v for v in env.values())
    for key in _T1_POISON:
        if key not in _T1_BASE_KEYS:
            assert key not in env


def test_t1_clean_env_does_not_mutate_os_environ_or_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/evil")
    before = dict(os.environ)
    env = sysenv.clean_env(tmp_path)
    env["PATH"] = "/mutated"
    assert dict(os.environ) == before
    assert sysenv.clean_env(tmp_path)["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"


def test_t1_clean_env_tmpdir_only_via_extra(tmp_path):
    env = sysenv.clean_env(tmp_path, {"TMPDIR": "/scratch/x"})
    assert env["TMPDIR"] == "/scratch/x"
    assert set(env) == _T1_BASE_KEYS | {"TMPDIR"}


@pytest.mark.parametrize("key", [
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_DIR", "GIT_SSH_COMMAND",
    "GIT_TERMINAL_PROMPT", "GIT_ATTR_NOSYSTEM", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV",
])
def test_t1_clean_env_extra_cannot_override_reserved(tmp_path, key):
    with pytest.raises(ValueError):
        sysenv.clean_env(tmp_path, {key: "/evil"})


@pytest.mark.parametrize("extra", [
    {"": "x"}, {"A=B": "x"}, {"A\x00B": "x"}, {"OK": "a\x00b"}, {"OK": 5}, {5: "x"},
])
def test_t1_clean_env_extra_rejects_malformed_entries(tmp_path, extra):
    with pytest.raises(ValueError):
        sysenv.clean_env(tmp_path, extra)


def test_t1_clean_env_extra_none_and_empty_are_equal(tmp_path):
    assert sysenv.clean_env(tmp_path) == sysenv.clean_env(tmp_path, {}) == sysenv.clean_env(tmp_path, None)


def test_t1_clean_env_rejects_relative_home():
    with pytest.raises(ValueError):
        sysenv.clean_env(Path("relative/home"))


def test_t1_home_of_euid_ignores_home_env(monkeypatch):
    expected = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    monkeypatch.setenv("HOME", "/tmp/evil")
    assert sysenv.home_of_euid() == expected
    monkeypatch.delenv("HOME", raising=False)
    assert sysenv.home_of_euid() == expected
    assert "evil" not in str(sysenv.home_of_euid())


def _t1_sudo(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SUDO_UID", raising=False)
    else:
        monkeypatch.setenv("SUDO_UID", value)


@pytest.mark.parametrize("value", [
    None, "", " ", "abc", "-1", "-501", " 501", "501 ", "501\n", "\n501", "0x1f5", "1e3", "5.0", "+501",
    "٥٠١", "0501", "501;id", "99999999999999999999", "2147480000", "0",
])
def test_t1_sudo_uid_refuses_bad_values(monkeypatch, value):
    _t1_sudo(monkeypatch, value)
    with pytest.raises(SealRefusal) as ei:
        sysenv.sudo_uid()
    assert ei.value.code == "BAD_ARGS"
    assert str(ei.value) == "BAD_ARGS"


def test_t1_sudo_uid_accepts_current_uid(monkeypatch):
    uid = os.getuid()
    if uid == 0:
        pytest.skip("root uid is deliberately refused")
    _t1_sudo(monkeypatch, str(uid))
    got = sysenv.sudo_uid()
    assert got == uid and isinstance(got, int)


def test_t1_sudo_uid_nonexistent_uid_is_refused(monkeypatch):
    with pytest.raises(KeyError):
        pwd.getpwuid(2147480000)
    _t1_sudo(monkeypatch, "2147480000")
    with pytest.raises(SealRefusal) as ei:
        sysenv.sudo_uid()
    assert ei.value.code == "BAD_ARGS"


def test_t1_foundation_modules_are_stdlib_only():
    import ast
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
    for mod in (codes, paths, sysenv):
        tree = ast.parse(Path(mod.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for n in names:
                assert n != "scripts", (mod.__name__, n)
                if stdlib is not None:
                    assert n in stdlib, (mod.__name__, n)
