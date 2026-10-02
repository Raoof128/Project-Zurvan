"""Z1b Task 16 tests: promotion workflow (contract s4.2) - the production caller of the Z1a dataset gate.

The broker is replaced by a fake client object that records every call, so "refused before any broker
call" is an assertion about an empty call list. The dataset registry is the real eval_datasets module
(`ed.REGISTRY` is import-bound, so tests monkeypatch it to a tmp registry); the RETIRED case reads the
REAL registry entry for the burned search_gold instrument.
"""
import copy
import json
import os
import subprocess
import types
from pathlib import Path

import pytest

import scripts.eval_datasets as ed
import scripts.z1b_promotion as zp
from scripts.eval_seal import manifest as seal_manifest
from scripts.eval_seal.spec import STRATA, load_spec
from scripts.z1b_client import ClientRefusal

REPO_ROOT = Path(__file__).resolve().parents[1]
_T16_SID = "0123456789abcdef"
_T16_HOLDOUT = "a1" * 32
_T16_MSHA = "c3" * 32
_T16_BROKER = "b" * 40
_T16_CANDIDATE = "d" * 40
SSH_KEYGEN = "/usr/bin/ssh-keygen"


def _t16_decl(commit=_T16_CANDIDATE, **over):
    # R20/R24: the commit that contains the declaration binds it; `commit` is no longer written into it
    decl = {
        "role": "candidate", "suite_id": _T16_SID, "primary_metric": "mrr",
        "critical_strata": ["exact_identifier", "path"],
        "non_inferiority_margins": {"critical": 0.02, "overall": 0.01},
        "improvement_target": {"metric": "mrr", "min_delta": 0.02},
        "resource_budgets": {"latency_p95_ms": 500, "peak_rss_mb": 4000},
        "resource_improvement": None,
    }
    decl.update(over)
    return decl


def _t16_result(commit=_T16_CANDIDATE, verdict="PASS", **over):
    metrics = {"recall_at_1": 0.25, "recall_at_3": 0.5, "recall_at_10": 0.75, "mrr": 0.4}
    res = {
        "ok": True, "schema": "zurvan.z1b.result.v1", "suite": "holdout", "suite_id": _T16_SID,
        "n_items": 150, "corpus_sha256": "b" * 64, "suite_manifest_sha256": _T16_MSHA,
        "evaluator_version": "1.0.0", "broker_sha": _T16_BROKER, "candidate_commit": commit,
        "metrics": dict(metrics, ci={m: [0.1, 0.9] for m in metrics}), "per_stratum": None,
        "target_missing": 0, "abstain": None, "latency_ms": {"p50": 40, "p95": 120}, "peak_rss_mb": 900,
        "seal_valid": True, "cached": False, "budget_remaining": 7,
        "promotion": {"declared": True, "verdict": verdict, "rules": {
            "non_inferiority_critical": verdict == "PASS", "non_inferiority_overall": True,
            "improvement": True, "resource_budget": True}},
    }
    res.update(over)
    return res


class _T16Client:
    """A fake z1b_client: records every run() call, returns a canned result."""

    def __init__(self, result=None, exc=None):
        self.calls = []
        self.result = result
        self.exc = exc

    def run(self, commit, suite, declare=None, final_acceptance=False):
        self.calls.append({"commit": commit, "suite": suite, "declare": declare,
                           "final_acceptance": final_acceptance})
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.result)


def _t16_registry(tmp_path, holdout_state="HOLDOUT"):
    reg = tmp_path / "datasets.json"
    entries = {"z1b_reserve": {"state": "RESERVE", "sha256": "e5" * 32},
               "z1b_dev": {"state": "DEV", "sha256": "f6" * 32}}
    if holdout_state is not None:
        entries["z1b_holdout"] = {"state": holdout_state, "sha256": _T16_HOLDOUT}
    reg.write_text(json.dumps({"schema": "zurvan.eval.datasets.v1", "datasets": entries}))
    return reg


@pytest.fixture
def t16(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "eval" / "z1b" / "declarations").mkdir(parents=True)
    (repo / "eval" / "z1b" / "spec.json").write_text((REPO_ROOT / "eval" / "z1b" / "spec.json").read_text())
    monkeypatch.setattr(zp, "REPO", repo)
    monkeypatch.setattr(ed, "REGISTRY", _t16_registry(tmp_path))
    manifest = {"suite_id": _T16_SID, "holdout_age_sha256": _T16_HOLDOUT}
    monkeypatch.setattr(zp, "promotion_load_manifest", lambda: (manifest, _T16_MSHA))
    monkeypatch.setattr(zp, "promotion_now", lambda: "2026-10-02T12:00:00Z")
    decl_path = "eval/z1b/declarations/pre-one.json"
    state = {"decl": _t16_decl()}
    monkeypatch.setattr(zp, "_promotion_read_declaration", lambda commit, path: copy.deepcopy(state["decl"]))
    return types.SimpleNamespace(repo=repo, tmp=tmp_path, manifest=manifest, decl_path=decl_path, state=state,
                                 records=repo / "eval" / "z1b" / "promotions.jsonl")


def _t16_refused(code, fn, *a, **kw):
    with pytest.raises(ClientRefusal) as ei:
        fn(*a, **kw)
    assert ei.value.code == code and str(ei.value) == code
    return ei.value


def _t16_lines(t16):
    return t16.records.read_text().splitlines() if t16.records.exists() else []


# ------------------------------------------------------------------ the dataset gate (Z1a gate 6 caller)

def test_t16_the_retired_search_gold_instrument_is_refused_before_any_broker_call(t16, monkeypatch):
    real = json.loads((REPO_ROOT / "eval" / "datasets.json").read_text())["datasets"]["search_gold"]
    assert real["state"] == "RETIRED"
    monkeypatch.setattr(ed, "REGISTRY", REPO_ROOT / "eval" / "datasets.json")  # the REAL registry
    t16.manifest["holdout_age_sha256"] = real["sha256"]
    client = _T16Client(_t16_result())
    with pytest.raises(ed.DatasetNotEligible) as ei:
        zp.promote(_T16_CANDIDATE, t16.decl_path, client=client)
    assert ei.value.code == "NOT_PROMOTION_ELIGIBLE" and "RETIRED" in str(ei.value)
    assert client.calls == [] and _t16_lines(t16) == []


@pytest.mark.parametrize("digest,state", [("e5" * 32, "RESERVE"), ("f6" * 32, "DEV"), ("99" * 32, "DEV")])
def test_t16_reserve_dev_and_unregistered_digests_are_refused_before_any_broker_call(t16, digest, state):
    t16.manifest["holdout_age_sha256"] = digest
    client = _T16Client(_t16_result())
    with pytest.raises(ed.DatasetNotEligible) as ei:
        zp.promote(_T16_CANDIDATE, t16.decl_path, client=client)
    assert ei.value.code == "NOT_PROMOTION_ELIGIBLE" and state in str(ei.value)
    assert client.calls == [] and _t16_lines(t16) == []


def test_t16_an_unregistered_holdout_when_the_registry_lacks_the_entry_is_refused(t16, monkeypatch):
    monkeypatch.setattr(ed, "REGISTRY", _t16_registry(t16.tmp, holdout_state=None))
    client = _T16Client(_t16_result())
    with pytest.raises(ed.DatasetNotEligible):
        zp.promote(_T16_CANDIDATE, t16.decl_path, client=client)
    assert client.calls == []


def test_t16_the_gate_runs_before_the_declaration_is_even_read(t16, monkeypatch):
    t16.manifest["holdout_age_sha256"] = "e5" * 32  # RESERVE
    monkeypatch.setattr(zp, "_promotion_read_declaration",
                        lambda *a: pytest.fail("declaration read before the dataset gate"))
    with pytest.raises(ed.DatasetNotEligible):
        zp.promote(_T16_CANDIDATE, "not-even-a-path", client=_T16Client())


def test_t16_promote_calls_the_real_gate_functions_in_order_before_the_broker(t16, monkeypatch):
    order = []
    real_for = ed.dataset_for_digest
    real_req = ed.require_promotion_eligible
    monkeypatch.setattr(ed, "dataset_for_digest", lambda d: (order.append("lookup"), real_for(d))[1])
    monkeypatch.setattr(ed, "require_promotion_eligible", lambda i: (order.append("require"), real_req(i))[1])
    client = _T16Client(_t16_result())
    orig = client.run
    client.run = lambda *a, **k: (order.append("broker"), orig(*a, **k))[1]
    zp.promote(_T16_CANDIDATE, t16.decl_path, client=client)
    assert order == ["lookup", "require", "broker"]


def test_t16_promotion_dataset_uses_the_manifest_holdout_digest(t16):
    info = zp.promotion_dataset({"holdout_age_sha256": _T16_HOLDOUT})
    assert (info.name, info.state, info.sha256) == ("z1b_holdout", "HOLDOUT", _T16_HOLDOUT)
    _t16_refused("MANIFEST_INVALID", zp.promotion_dataset, {})
    _t16_refused("MANIFEST_INVALID", zp.promotion_dataset, None)


def test_t16_registry_argument_overrides_the_registry_and_always_restores_it(t16):
    other = t16.tmp / "other.json"
    other.write_text(json.dumps({"datasets": {"x": {"state": "RESERVE", "sha256": _T16_HOLDOUT}}}))
    before = ed.REGISTRY
    with pytest.raises(ed.DatasetNotEligible):
        zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()), registry=other)
    assert ed.REGISTRY == before
    zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()))  # default registry: HOLDOUT


# ------------------------------------------------------------------ the HOLDOUT run and its record

def test_t16_holdout_calls_the_broker_with_declare_and_records_a_pass(t16):
    client = _T16Client(_t16_result())
    out = zp.promote(_T16_CANDIDATE, t16.decl_path, client=client)
    assert client.calls == [{"commit": _T16_CANDIDATE, "suite": "holdout", "declare": t16.decl_path,
                             "final_acceptance": False}]
    assert out["verdict"] == "PASS" and out["recorded"] is True and out["result"]["cached"] is False
    lines = _t16_lines(t16)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec == {"commit": _T16_CANDIDATE, "suite_id": _T16_SID, "suite_manifest_sha256": _T16_MSHA,
                   "verdict": "PASS", "broker_sha": _T16_BROKER, "created": "2026-10-02T12:00:00Z"}
    assert lines[0] == json.dumps(rec, sort_keys=True, separators=(",", ":"))


def test_t16_a_fail_verdict_records_nothing_and_creates_no_file(t16):
    out = zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result(verdict="FAIL")))
    assert out["verdict"] == "FAIL" and out["recorded"] is False
    assert not t16.records.exists()


def test_t16_a_pass_is_recorded_once_even_when_the_run_is_repeated_from_cache(t16):
    zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()))
    again = zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result(cached=True)))
    assert again["verdict"] == "PASS" and again["recorded"] is False
    assert len(_t16_lines(t16)) == 1


def test_t16_records_are_append_only_and_never_rewritten(t16):
    zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()))
    first = t16.records.read_bytes()
    other = "e" * 40
    t16.state["decl"] = _t16_decl(commit=other)
    zp.promote(other, t16.decl_path, client=_T16Client(_t16_result(commit=other)))
    assert t16.records.read_bytes().startswith(first) and len(_t16_lines(t16)) == 2


def test_t16_broker_refusals_propagate_and_record_nothing(t16):
    for code in ("SUITE_RENEWAL_REQUIRED", "INTEGRITY_INCIDENT", "BROKER_STALE", "CANDIDATE_NOT_FROZEN"):
        with pytest.raises(ClientRefusal) as ei:
            zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(exc=ClientRefusal(code)))
        assert ei.value.code == code
    assert not t16.records.exists()


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(promotion=None), "INTERNAL"),                       # a declared run came back undeclared
    (lambda r: r["promotion"].update(declared=False), "INTERNAL"),
    (lambda r: r["promotion"].update(verdict="MAYBE"), "INTERNAL"),
    (lambda r: r.update(candidate_commit="e" * 40), "INTERNAL"),            # answer for a different commit
    (lambda r: r.update(suite_id="f" * 16), "INTERNAL"),
    (lambda r: r.update(suite_manifest_sha256="00" * 32), "MANIFEST_INVALID"),  # public manifest is stale
])
def test_t16_an_inconsistent_broker_result_is_not_recorded(t16, mutate, code):
    res = _t16_result()
    mutate(res)
    _t16_refused(code, zp.promote, _T16_CANDIDATE, t16.decl_path, client=_T16Client(res))
    assert not t16.records.exists()


def test_t16_a_malformed_commit_or_declaration_path_is_refused_before_the_broker(t16):
    client = _T16Client(_t16_result())
    for bad in ("HEAD", "d" * 39, "D" * 40, "", None):
        _t16_refused("CANDIDATE_NOT_FROZEN", zp.promote, bad, t16.decl_path, client=client)
    for bad in ("../x.json", "eval/z1b/declarations/../../x.json", "eval/z1b/declarations/A.json",
                "eval/z1b/declarations/x.json\n", "/abs/x.json", "eval/z1b/x.json", None, 5):
        _t16_refused("DECLARATION_INVALID", zp.promote, _T16_CANDIDATE, bad, client=client)
    assert client.calls == []


# ------------------------------------------------------------------ declaration validation

def _t16_spec():
    return load_spec(REPO_ROOT / "eval" / "z1b" / "spec.json")


def test_t16_a_complete_declaration_validates():
    zp.validate_declaration(_t16_decl(), _t16_spec())
    zp.validate_declaration(_t16_decl(critical_strata=list(STRATA)), _t16_spec())


@pytest.mark.parametrize("field", ["role", "suite_id", "primary_metric", "critical_strata",
                                   "non_inferiority_margins", "improvement_target", "resource_budgets", "resource_improvement"])
def test_t16_each_missing_field_is_declaration_invalid_and_no_broker_call(t16, field):
    decl = _t16_decl()
    del decl[field]
    t16.state["decl"] = decl
    client = _T16Client(_t16_result())
    _t16_refused("DECLARATION_INVALID", zp.promote, _T16_CANDIDATE, t16.decl_path, client=client)
    _t16_refused("DECLARATION_INVALID", zp.validate_declaration, decl, _t16_spec())
    assert client.calls == [] and not t16.records.exists()


@pytest.mark.parametrize("bad", [
    {"extra_field": 1},
    {"primary_metric": "recall_at_5"}, {"primary_metric": 1}, {"primary_metric": None},
    {"critical_strata": []}, {"critical_strata": ["exact_identifier", "exact_identifier"]},
    {"critical_strata": ["not_a_stratum"]}, {"critical_strata": "exact_identifier"},
    {"critical_strata": ["exact_identifier", 3]}, {"critical_strata": [["path"]]},
    {"non_inferiority_margins": {"critical": -0.01, "overall": 0.01}},
    {"non_inferiority_margins": {"critical": 0.02, "overall": -1}},
    {"non_inferiority_margins": {"critical": 1.5, "overall": 0.01}},
    {"non_inferiority_margins": {"critical": True, "overall": 0.01}},
    {"non_inferiority_margins": {"critical": float("nan"), "overall": 0.01}},
    {"non_inferiority_margins": {"critical": float("inf"), "overall": 0.01}},
    {"non_inferiority_margins": {"critical": "0.02", "overall": 0.01}},
    {"non_inferiority_margins": {"critical": 0.02}},
    {"non_inferiority_margins": {"critical": 0.02, "overall": 0.01, "extra": 0}},
    {"non_inferiority_margins": [0.02, 0.01]},
    {"improvement_target": {"metric": "mrr"}}, {"improvement_target": {"metric": "x", "min_delta": 0.1}},
    {"improvement_target": {"metric": "mrr", "min_delta": -0.1}},
    {"improvement_target": {"metric": "mrr", "min_delta": 2}},
    {"resource_budgets": {"latency_p95_ms": 500}},
    {"resource_budgets": {"latency_p95_ms": 5, "peak_rss_mb": 4000}},
    {"resource_budgets": {"latency_p95_ms": 500.5, "peak_rss_mb": 4000}},
    {"resource_budgets": {"latency_p95_ms": True, "peak_rss_mb": 4000}},
    {"resource_budgets": {"latency_p95_ms": 500, "peak_rss_mb": 0}},
    {"suite_id": "ABCDEF0123456789"}, {"suite_id": "short"}, {"suite_id": 7},
    {"role": "Candidate"}, {"role": None}, {"role": "promoted"}, {"candidate_commit": "d" * 40},
])
def test_t16_malformed_declaration_values_are_declaration_invalid(bad):
    _t16_refused("DECLARATION_INVALID", zp.validate_declaration, _t16_decl(**bad), _t16_spec())


def test_t16_a_declaration_for_another_commit_or_suite_is_refused_by_promote(t16):
    client = _T16Client(_t16_result())
    t16.state["decl"] = _t16_decl(role="baseline")  # a baseline declaration is measured, never promoted
    _t16_refused("DECLARATION_INVALID", zp.promote, _T16_CANDIDATE, t16.decl_path, client=client)
    t16.state["decl"] = _t16_decl(suite_id="f" * 16)  # names a different suite version
    _t16_refused("DECLARATION_INVALID", zp.promote, _T16_CANDIDATE, t16.decl_path, client=client)
    assert client.calls == [] and not t16.records.exists()


def test_t16_validate_declaration_refuses_non_dict_inputs_and_a_broken_spec():
    for bad in (None, [], "x", 3, [_t16_decl()]):
        _t16_refused("DECLARATION_INVALID", zp.validate_declaration, bad, _t16_spec())
    _t16_refused("DECLARATION_INVALID", zp.validate_declaration, _t16_decl(), {})
    _t16_refused("DECLARATION_INVALID", zp.validate_declaration, _t16_decl(), None)


def test_t16_declaration_errors_never_carry_declaration_text():
    err = _t16_refused("DECLARATION_INVALID", zp.validate_declaration,
                       _t16_decl(primary_metric="SECRET-METRIC-NAME"), _t16_spec())
    assert "SECRET" not in str(err) and "SECRET" not in repr(err) and err.__cause__ is None


# ------------------------------------------------------------------ declaration source: git show at the commit

def _t16_git(repo, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(repo), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@e.x", "-C", str(repo)]
                          + list(args), env=env, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def t16git(t16, monkeypatch):
    monkeypatch.undo()  # undo the fixture's patches; this class needs the real reader
    monkeypatch.setattr(zp, "REPO", t16.repo)
    subprocess.run(["/usr/bin/git", "init", "-q", str(t16.repo)], check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(t16.repo), "GIT_CONFIG_GLOBAL": "/dev/null"})
    (t16.repo / "README").write_text("x\n")
    _t16_git(t16.repo, "add", "-A")
    _t16_git(t16.repo, "commit", "-q", "-m", "base")
    t16.base = _t16_git(t16.repo, "rev-parse", "HEAD")
    return t16


def test_t16_declaration_is_read_from_the_commit_not_the_working_tree(t16git):
    path = t16git.repo / "eval" / "z1b" / "declarations" / "pre-one.json"
    path.write_text(json.dumps(_t16_decl()))
    _t16_git(t16git.repo, "add", "-A")
    _t16_git(t16git.repo, "commit", "-q", "-m", "declare")
    commit = _t16_git(t16git.repo, "rev-parse", "HEAD")
    assert zp._promotion_read_declaration(commit, "eval/z1b/declarations/pre-one.json") == _t16_decl()
    path.write_text(json.dumps(_t16_decl(primary_metric="recall_at_1")))  # uncommitted edit is invisible
    assert zp._promotion_read_declaration(commit, "eval/z1b/declarations/pre-one.json")["primary_metric"] == "mrr"


def test_t16_a_declaration_absent_at_the_commit_is_declaration_invalid(t16git):
    (t16git.repo / "eval" / "z1b" / "declarations" / "late.json").write_text(json.dumps(_t16_decl()))  # untracked
    _t16_refused("DECLARATION_INVALID", zp._promotion_read_declaration, t16git.base,
                 "eval/z1b/declarations/late.json")
    _t16_refused("DECLARATION_INVALID", zp._promotion_read_declaration, "e" * 40,
                 "eval/z1b/declarations/late.json")


@pytest.mark.parametrize("body", ["not json", "[]", '{"a":1,"a":2}', '{"x": NaN}', "", "x" * 70000])
def test_t16_a_committed_but_malformed_declaration_is_declaration_invalid(t16git, body):
    (t16git.repo / "eval" / "z1b" / "declarations" / "bad.json").write_text(body)
    _t16_git(t16git.repo, "add", "-A")
    _t16_git(t16git.repo, "commit", "-q", "-m", "bad")
    commit = _t16_git(t16git.repo, "rev-parse", "HEAD")
    try:
        got = zp._promotion_read_declaration(commit, "eval/z1b/declarations/bad.json")
    except ClientRefusal as exc:
        assert exc.code == "DECLARATION_INVALID"
    else:
        _t16_refused("DECLARATION_INVALID", zp.validate_declaration, got, _t16_spec())


def test_t16_an_oversized_but_valid_declaration_is_refused_by_the_reader(t16git):
    (t16git.repo / "eval" / "z1b" / "declarations" / "big.json").write_text(json.dumps({"pad": "x" * 70000}))
    _t16_git(t16git.repo, "add", "-A")
    _t16_git(t16git.repo, "commit", "-q", "-m", "big")
    commit = _t16_git(t16git.repo, "rev-parse", "HEAD")
    _t16_refused("DECLARATION_INVALID", zp._promotion_read_declaration, commit, "eval/z1b/declarations/big.json")


# ------------------------------------------------------------------ final acceptance

def _t16_record(**over):
    rec = {"commit": _T16_CANDIDATE, "suite_id": _T16_SID, "suite_manifest_sha256": _T16_MSHA,
           "verdict": "PASS", "broker_sha": _T16_BROKER, "created": "2026-10-02T12:00:00Z"}
    rec.update(over)
    return rec


def _t16_write_records(t16, *recs):
    t16.records.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in recs))


def test_t16_final_acceptance_without_a_pass_record_is_refused_before_the_broker(t16):
    client = _T16Client(_t16_result())
    _t16_refused("RESERVE_NOT_ELIGIBLE", zp.final_acceptance, _T16_CANDIDATE, client=client)
    assert client.calls == []


@pytest.mark.parametrize("over", [{"commit": "e" * 40}, {"suite_id": "f" * 16}])
def test_t16_a_record_for_another_commit_or_suite_does_not_unlock_the_reserve(t16, over):
    _t16_write_records(t16, _t16_record(**over))
    client = _T16Client(_t16_result())
    _t16_refused("RESERVE_NOT_ELIGIBLE", zp.final_acceptance, _T16_CANDIDATE, client=client)
    assert client.calls == []


def test_t16_final_acceptance_with_a_matching_pass_record_runs_the_reserve(t16):
    _t16_write_records(t16, _t16_record())
    client = _T16Client(_t16_result())
    assert zp.final_acceptance(_T16_CANDIDATE, client=client)["candidate_commit"] == _T16_CANDIDATE
    assert client.calls == [{"commit": _T16_CANDIDATE, "suite": "reserve", "declare": None,
                             "final_acceptance": True}]


def test_t16_a_failed_holdout_then_final_acceptance_is_refused_end_to_end(t16):
    zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result(verdict="FAIL")))
    client = _T16Client(_t16_result())
    _t16_refused("RESERVE_NOT_ELIGIBLE", zp.final_acceptance, _T16_CANDIDATE, client=client)
    assert client.calls == []


def test_t16_promote_then_final_acceptance_end_to_end(t16):
    zp.promote(_T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()))
    client = _T16Client(_t16_result())
    zp.final_acceptance(_T16_CANDIDATE, client=client)
    assert client.calls[0]["suite"] == "reserve"


def test_t16_final_acceptance_refuses_a_malformed_commit(t16):
    client = _T16Client(_t16_result())
    _t16_refused("CANDIDATE_NOT_FROZEN", zp.final_acceptance, "HEAD", client=client)
    assert client.calls == []


# ------------------------------------------------------------------ the record file is untrusted input

@pytest.mark.parametrize("content", [
    "not json\n", "[]\n", "{}\n", '{"commit":"x"}\n',
    json.dumps(_t16_record(verdict="FAIL")) + "\n",                  # only PASS may ever be recorded
    json.dumps(_t16_record(commit="D" * 40)) + "\n",
    json.dumps(dict(_t16_record(), extra=1)) + "\n",
    json.dumps(_t16_record(created="yesterday")) + "\n",
    json.dumps(_t16_record())[:-5],                                  # truncated, no newline
    json.dumps(_t16_record(), sort_keys=True, separators=(",", ":")),  # valid but unterminated: an append would fuse
    "\n",
])
def test_t16_a_malformed_record_file_is_ledger_invalid_for_both_entry_points(t16, content):
    t16.records.write_text(content)
    client = _T16Client(_t16_result())
    _t16_refused("LEDGER_INVALID", zp.final_acceptance, _T16_CANDIDATE, client=client)
    _t16_refused("LEDGER_INVALID", zp.promote, _T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()))
    assert client.calls == []
    assert t16.records.read_text() == content  # never repaired or appended to


def test_t16_a_symlinked_record_file_is_not_followed(t16):
    victim = t16.tmp / "victim.jsonl"
    victim.write_text("")
    t16.records.symlink_to(victim)
    _t16_refused("LEDGER_INVALID", zp.promote, _T16_CANDIDATE, t16.decl_path, client=_T16Client(_t16_result()))
    assert victim.read_text() == ""


# ------------------------------------------------------------------ manifest source (real signatures)

@pytest.mark.skipif(not os.path.exists(SSH_KEYGEN), reason="no /usr/bin/ssh-keygen")
def test_t16_the_public_manifest_is_signature_verified_before_it_is_believed(t16, monkeypatch, tmp_path):
    monkeypatch.undo()
    monkeypatch.setattr(zp, "REPO", t16.repo)
    key = tmp_path / "k"
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    pub = (tmp_path / "k.pub").read_text().split()
    base = t16.repo / "eval" / "z1b"
    (base / "allowed_signers").write_text('zurvaneval namespaces="zurvan-z1b-manifest" %s %s\n' % (pub[0], pub[1]))
    h = "a1" * 32
    m = seal_manifest.build_manifest(
        suite_id=_T16_SID, sequence=1, created="2026-10-02T10:00:00Z",
        models={"author": "claude-opus-4-5", "reviewer": "claude-sonnet-5-5"}, prompt_sha256=h,
        corpus_sha256="b2" * 32, holdout_age_sha256=_T16_HOLDOUT, reserve_age_sha256="d4" * 32,
        dev_sha256="e5" * 32, strata_counts={"holdout": {"path": 10}, "reserve": {"path": 5}}, accepted=15,
        rejected={}, short=[], evaluator_version="1.0.0", burn_ledger=[])
    seal_manifest.write_manifest(base / "manifest.json", m)
    seal_manifest.sign(base / "manifest.json", key)
    parsed, sha = zp.promotion_load_manifest()
    assert parsed["suite_id"] == _T16_SID and sha == seal_manifest.suite_manifest_sha256(base / "manifest.json")
    blob = bytearray((base / "manifest.json").read_bytes())
    blob[10] ^= 1
    (base / "manifest.json").write_bytes(bytes(blob))
    _t16_refused("MANIFEST_INVALID", zp.promotion_load_manifest)
    (base / "manifest.json.sig").unlink()
    _t16_refused("MANIFEST_INVALID", zp.promotion_load_manifest)


def test_t16_promote_refuses_when_the_public_manifest_cannot_be_verified(t16, monkeypatch):
    def refuse():
        raise ClientRefusal("MANIFEST_INVALID")
    monkeypatch.setattr(zp, "promotion_load_manifest", refuse)
    client = _T16Client(_t16_result())
    _t16_refused("MANIFEST_INVALID", zp.promote, _T16_CANDIDATE, t16.decl_path, client=client)
    _t16_refused("MANIFEST_INVALID", zp.final_acceptance, _T16_CANDIDATE, client=client)
    assert client.calls == []


# ------------------------------------------------------------------ CLI wiring

def test_t16_cli_promote_and_final_acceptance_print_only_the_code_on_refusal(t16, capsys, monkeypatch):
    import scripts.z1b_client as zc
    t16.manifest["holdout_age_sha256"] = "e5" * 32  # RESERVE: not promotion eligible
    args = types.SimpleNamespace(z1b_action="promote", commit=_T16_CANDIDATE, declaration=t16.decl_path)
    assert zc.client_cli_main(args) == 2
    assert capsys.readouterr().out.strip() == "NOT_PROMOTION_ELIGIBLE"
    args = types.SimpleNamespace(z1b_action="final-acceptance", commit=_T16_CANDIDATE)
    assert zc.client_cli_main(args) == 2
    assert capsys.readouterr().out.strip() == "RESERVE_NOT_ELIGIBLE"
