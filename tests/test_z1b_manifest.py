"""Z1b Task 6 tests: signed public manifest (contract s3.5) with real ssh-keygen -Y."""
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.eval_seal import manifest as M
from scripts.eval_seal import sysenv
from scripts.eval_seal.codes import SealRefusal

SSH_KEYGEN = "/usr/bin/ssh-keygen"
pytestmark = pytest.mark.skipif(not os.path.exists(SSH_KEYGEN), reason="no /usr/bin/ssh-keygen")

H = "a1" * 32  # 64 hex


def _t6_keygen(d: Path, name: str = "k") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    key = d / name
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


def _t6_signers(path: Path, key: Path, principal="zurvaneval", namespaces="zurvan-z1b-manifest") -> Path:
    pub = (key.parent / (key.name + ".pub")).read_text().split()
    path.write_text(f'{principal} namespaces="{namespaces}" {pub[0]} {pub[1]}\n')
    return path


def _t6_manifest(**over) -> dict:
    kw = dict(
        suite_id="0123456789abcdef", sequence=3, created="2026-10-02T10:00:00Z",
        models={"author": "claude-opus-4-5", "reviewer": "claude-sonnet-5-5"},
        prompt_sha256=H, corpus_sha256="b2" * 32, holdout_age_sha256="c3" * 32,
        reserve_age_sha256="d4" * 32, dev_sha256="e5" * 32,
        strata_counts={"holdout": {"exact_identifier": 10, "path": 10},
                       "reserve": {"exact_identifier": 5, "path": 5}},
        accepted=30, rejected={"near_dup": 4, "bad_schema": 1},
        short=[{"pool": "holdout", "stratum": "hash", "have": 7, "need": 10}],
        evaluator_version="1.0.0",
        burn_ledger=[{"id": "ab" * 16, "reason": "canary_hit", "date": "2026-10-01"}],
    )
    kw.update(over)
    return M.build_manifest(**kw)


@pytest.fixture
def t6(tmp_path):
    key = _t6_keygen(tmp_path / "keys")
    signers = _t6_signers(tmp_path / "allowed_signers", key)
    mf = tmp_path / "manifest.json"
    M.write_manifest(mf, _t6_manifest())
    sig = M.sign(mf, key)
    return dict(tmp=tmp_path, key=key, signers=signers, mf=mf, sig=sig)


def _t6_refused(fn, code="MANIFEST_INVALID"):
    with pytest.raises(SealRefusal) as ei:
        fn()
    assert ei.value.code == code


# ------------------------------------------------------------ happy path

def test_t6_constants():
    assert M.SSH_KEYGEN == SSH_KEYGEN
    assert M.NAMESPACE == "zurvan-z1b-manifest"


def test_t6_round_trip(t6):
    assert t6["sig"] == Path(str(t6["mf"]) + ".sig") and t6["sig"].is_file()
    got = M.verify(t6["mf"], t6["sig"], t6["signers"])
    assert got == json.loads(t6["mf"].read_text())
    assert got["schema"] == "zurvan.z1b.manifest.v1" and got["sequence"] == 3


def test_t6_suite_manifest_sha256_is_hash_of_file_bytes(t6):
    import hashlib
    assert M.suite_manifest_sha256(t6["mf"]) == hashlib.sha256(t6["mf"].read_bytes()).hexdigest()


def test_t6_manifest_bytes_are_canonical_and_stable(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    M.write_manifest(a, _t6_manifest())
    M.write_manifest(b, _t6_manifest())
    assert a.read_bytes() == b.read_bytes()
    assert a.read_bytes().endswith(b"\n")


# ------------------------------------------------------------ verify failures

def test_t6_tampered_manifest_byte_is_invalid(t6):
    data = bytearray(t6["mf"].read_bytes())
    i = data.index(b"accepted") + 11
    data[i] = ord("9") if data[i] != ord("9") else ord("8")
    t6["mf"].write_bytes(bytes(data))
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], t6["signers"]))


def test_t6_appended_byte_is_invalid(t6):
    t6["mf"].write_bytes(t6["mf"].read_bytes() + b" ")
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], t6["signers"]))


def test_t6_signature_from_a_different_key_is_invalid(t6):
    other = _t6_keygen(t6["tmp"] / "other")
    sig2 = Path(str(t6["tmp"] / "m2.json") + ".sig")
    m2 = t6["tmp"] / "m2.json"
    m2.write_bytes(t6["mf"].read_bytes())
    sig_other = M.sign(m2, other)
    assert sig_other == sig2
    _t6_refused(lambda: M.verify(t6["mf"], sig_other, t6["signers"]))


def test_t6_signature_over_other_content_is_invalid(t6):
    m2 = t6["tmp"] / "m2.json"
    M.write_manifest(m2, _t6_manifest(sequence=4))
    sig2 = M.sign(m2, t6["key"])
    _t6_refused(lambda: M.verify(t6["mf"], sig2, t6["signers"]))


def test_t6_wrong_namespace_is_invalid(t6):
    mf = t6["tmp"] / "ns.json"
    mf.write_bytes(t6["mf"].read_bytes())
    sig = Path(str(mf) + ".sig")
    subprocess.run([SSH_KEYGEN, "-Y", "sign", "-f", str(t6["key"]), "-n", "some-other-namespace", str(mf)],
                   check=True, capture_output=True)
    _t6_refused(lambda: M.verify(mf, sig, t6["signers"]))
    # and an allowed_signers entry that only permits another namespace refuses a correct signature
    narrow = _t6_signers(t6["tmp"] / "narrow", t6["key"], namespaces="not-ours")
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], narrow))


def test_t6_swapped_allowed_signers_principal_is_invalid(t6):
    bad = _t6_signers(t6["tmp"] / "bad", t6["key"], principal="someone-else")
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], bad))


def test_t6_allowed_signers_with_different_key_is_invalid(t6):
    other = _t6_keygen(t6["tmp"] / "other2")
    bad = _t6_signers(t6["tmp"] / "bad2", other)
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], bad))


def test_t6_missing_signature_or_signers_or_manifest_is_invalid(t6):
    _t6_refused(lambda: M.verify(t6["mf"], t6["tmp"] / "nope.sig", t6["signers"]))
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], t6["tmp"] / "nope-signers"))
    _t6_refused(lambda: M.verify(t6["tmp"] / "nope.json", t6["sig"], t6["signers"]))


def test_t6_garbage_signature_is_invalid(t6):
    t6["sig"].write_bytes(b"not a signature")
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], t6["signers"]))
    t6["sig"].write_bytes(b"")
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], t6["signers"]))


def _t6_resign(t6, mutate_text=None, mutate_obj=None):
    obj = json.loads(t6["mf"].read_text())
    if mutate_obj:
        mutate_obj(obj)
    text = json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"
    if mutate_text:
        text = mutate_text(text)
    t6["mf"].write_text(text)
    return M.sign(t6["mf"], t6["key"])


@pytest.mark.parametrize("mut", [
    lambda o: o.update(schema="zurvan.z1b.manifest.v0"),
    lambda o: o.pop("schema"),
    lambda o: o.update(schema=2),
    lambda o: o.update(extra_field="x"),
    lambda o: o.pop("corpus_sha256"),
    lambda o: o.update(sequence="3"),
    lambda o: o.update(sequence=True),
    lambda o: o.update(sequence=-1),
    lambda o: o.update(suite_id="XYZ"),
    lambda o: o.update(corpus_sha256="nothex"),
    lambda o: o.update(evaluator_version="v1"),
    lambda o: o.update(accepted="30"),
    lambda o: o.update(created="yesterday"),
    lambda o: o["burn_ledger"].append({"id": "ab" * 16, "reason": "unburn", "date": "2026-10-01"}),
    lambda o: o["burn_ledger"].append({"id": "short", "reason": "canary_hit", "date": "2026-10-01"}),
    lambda o: o["burn_ledger"].append({"id": "ab" * 16, "reason": "canary_hit"}),
    lambda o: o["burn_ledger"].append({"id": "ab" * 16, "reason": "canary_hit", "date": "2026-10-01", "x": 1}),
    lambda o: o["models"].update(author="free text with spaces"),
], ids=["m%02d" % i for i in range(18)])
def test_t6_validly_signed_but_schema_wrong_manifest_is_invalid(t6, mut):
    sig = _t6_resign(t6, mutate_obj=mut)
    _t6_refused(lambda: M.verify(t6["mf"], sig, t6["signers"]))


def test_t6_validly_signed_duplicate_key_json_is_invalid(t6):
    sig = _t6_resign(t6, mutate_text=lambda t: t.replace('"sequence":3', '"sequence":99,"sequence":3'))
    assert b'"sequence":99' in t6["mf"].read_bytes()
    _t6_refused(lambda: M.verify(t6["mf"], sig, t6["signers"]))


def test_t6_validly_signed_nan_or_non_object_is_invalid(t6):
    sig = _t6_resign(t6, mutate_text=lambda t: t.replace('"accepted":30', '"accepted":NaN'))
    _t6_refused(lambda: M.verify(t6["mf"], sig, t6["signers"]))
    t6["mf"].write_text("[1,2,3]\n")
    sig = M.sign(t6["mf"], t6["key"])
    _t6_refused(lambda: M.verify(t6["mf"], sig, t6["signers"]))


def test_t6_validly_signed_per_item_digest_is_invalid_on_verify(t6):
    sig = _t6_resign(t6, mutate_obj=lambda o: o["strata_counts"]["holdout"].update({H: 1}))
    _t6_refused(lambda: M.verify(t6["mf"], sig, t6["signers"]))


# ------------------------------------------------------------ rollback

def test_t6_check_sequence_rollback_refused_equal_and_higher_ok(t6):
    m = M.verify(t6["mf"], t6["sig"], t6["signers"])
    M.check_sequence(m, 3)
    M.check_sequence(m, 0)
    _t6_refused(lambda: M.check_sequence(m, 4))
    bad = dict(m, sequence="3")
    _t6_refused(lambda: M.check_sequence(bad, 0))
    _t6_refused(lambda: M.check_sequence(dict(m, sequence=True), 0))
    _t6_refused(lambda: M.check_sequence(m, "1"))
    _t6_refused(lambda: M.check_sequence({}, 0))


# ------------------------------------------------------------ per-item digest ban

def test_t6_whitelisted_keys_may_hold_64_hex():
    m = _t6_manifest()
    M.assert_no_per_item_digests(m)  # builds and passes


@pytest.mark.parametrize("key", ["corpus_sha256", "holdout_age_sha256", "reserve_age_sha256",
                                 "dev_sha256", "prompt_sha256"])
def test_t6_whitelisted_key_must_still_be_exactly_64_hex(key):
    m = _t6_manifest()
    m[key] = "a" * 63
    _t6_refused(lambda: M.assert_no_per_item_digests(m))
    m[key] = "g" * 64
    _t6_refused(lambda: M.assert_no_per_item_digests(m))


def test_t6_64_hex_refused_in_any_non_whitelisted_place():
    base = _t6_manifest()
    cases = []
    c = copy.deepcopy(base); c["suite_id"] = H; cases.append(c)
    c = copy.deepcopy(base); c["burn_ledger"][0]["id"] = H; cases.append(c)
    c = copy.deepcopy(base); c["burn_ledger"][0]["reason"] = H; cases.append(c)
    c = copy.deepcopy(base); c["burn_ledger"].append(H); cases.append(c)
    c = copy.deepcopy(base); c["strata_counts"]["holdout"][H] = 1; cases.append(c)
    c = copy.deepcopy(base); c["strata_counts"][H] = {}; cases.append(c)
    c = copy.deepcopy(base); c["rejected"][H] = 1; cases.append(c)
    c = copy.deepcopy(base); c["models"]["author"] = H; cases.append(c)
    c = copy.deepcopy(base); c["short"][0]["pool"] = H; cases.append(c)
    c = copy.deepcopy(base); c["created"] = "x" + H; cases.append(c)
    c = copy.deepcopy(base); c["evaluator_version"] = "1.0.0-" + H.upper(); cases.append(c)
    c = copy.deepcopy(base); c["extra"] = {"deep": [[{"k": H}]]}; cases.append(c)
    c = copy.deepcopy(base); c["short"].append({"nested": [H]}); cases.append(c)
    for case in cases:
        _t6_refused(lambda case=case: M.assert_no_per_item_digests(case))


def test_t6_long_hex_runs_inside_other_strings_are_refused():
    for run in ("f" * 64, "0" * 128, "ab" * 20, "AB" * 32):
        m = _t6_manifest()
        m["models"] = {"author": "pre-" + run + "-post"}
        _t6_refused(lambda m=m: M.assert_no_per_item_digests(m))


def test_t6_burn_ledger_ids_32_hex_are_allowed():
    m = _t6_manifest(burn_ledger=[{"id": "0f" * 16, "reason": "item_leak", "date": "2026-10-02"}])
    M.assert_no_per_item_digests(m)
    assert len("0f" * 16) == 32


def test_t6_build_manifest_itself_refuses_a_per_item_digest():
    _t6_refused(lambda: _t6_manifest(strata_counts={"holdout": {H: 1}, "reserve": {}}))
    _t6_refused(lambda: _t6_manifest(burn_ledger=[{"id": H, "reason": "canary_hit", "date": "2026-10-01"}]))
    _t6_refused(lambda: _t6_manifest(burn_ledger=[{"id": "ab" * 16, "reason": "unburn", "date": "2026-10-01"}]))


def test_t6_build_manifest_rejects_bad_scalars():
    _t6_refused(lambda: _t6_manifest(sequence=-1))
    _t6_refused(lambda: _t6_manifest(suite_id="short"))
    _t6_refused(lambda: _t6_manifest(corpus_sha256="x"))
    _t6_refused(lambda: _t6_manifest(evaluator_version="1.0"))


def test_t6_sign_refuses_public_key_path_and_missing_key(t6):
    pub = Path(str(t6["key"]) + ".pub")
    _t6_refused(lambda: M.sign(t6["mf"], pub))
    _t6_refused(lambda: M.sign(t6["mf"], t6["tmp"] / "nokey"))
    _t6_refused(lambda: M.sign(t6["tmp"] / "nofile.json", t6["key"]))


# ------------------------------------------------------------ environment hygiene

def test_t6_hostile_env_does_not_change_behaviour(t6, monkeypatch):
    evil_home = t6["tmp"] / "evilhome"
    (evil_home / ".ssh").mkdir(parents=True)
    (evil_home / ".ssh" / "config").write_text("Host *\n  ProxyCommand touch %s\n" % (t6["tmp"] / "pwned"))
    monkeypatch.setenv("HOME", str(evil_home))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(t6["tmp"] / "no-agent.sock"))
    monkeypatch.setenv("SSH_ASKPASS", str(t6["tmp"] / "askpass"))
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("SSH_KEYGEN", "/tmp/evil-keygen")
    monkeypatch.setenv("PATH", str(t6["tmp"] / "evilbin"))
    monkeypatch.setenv("LD_PRELOAD", "/tmp/x.so")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/x.dylib")
    mf2 = t6["tmp"] / "h.json"
    M.write_manifest(mf2, _t6_manifest(sequence=5))
    sig2 = M.sign(mf2, t6["key"])
    assert M.verify(mf2, sig2, t6["signers"])["sequence"] == 5
    assert not (t6["tmp"] / "pwned").exists()
    t6["sig"].write_bytes(b"junk")
    _t6_refused(lambda: M.verify(t6["mf"], t6["sig"], t6["signers"]))


def test_t6_every_subprocess_gets_clean_env_and_absolute_binary(t6, monkeypatch):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    monkeypatch.setenv("SECRET_TOKEN", "x")
    seen = []
    real = subprocess.run

    def _spy(args, *a, **k):
        seen.append((list(args), k.get("env")))
        return real(args, *a, **k)

    monkeypatch.setattr(M.subprocess, "run", _spy)
    M.verify(t6["mf"], t6["sig"], t6["signers"])
    M.sign(t6["mf"], t6["key"])
    assert len(seen) == 2
    expected = sysenv.clean_env(sysenv.home_of_euid())
    for args, env in seen:
        assert args[0] == SSH_KEYGEN
        assert env == expected
        assert "SSH_AUTH_SOCK" not in env and "SECRET_TOKEN" not in env


def test_t6_top_level_key_that_is_a_digest_is_refused_by_the_standalone_guard():
    m = _t6_manifest()
    m[H] = 1
    _t6_refused(lambda: M.assert_no_per_item_digests(m))
    m2 = _t6_manifest()
    m2[1] = "x"
    _t6_refused(lambda: M.assert_no_per_item_digests(m2))
