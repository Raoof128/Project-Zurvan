"""Z1b Task 18 tests: integration fixes the dry-run gauntlet found between tasks.

Rulings (plan "Gauntlet" section): R12 no hard-coded developer home in the sandbox profile; R17 wrapper
entry names; R18 plaintext authoring state lives in the Time Machine-excluded private/ dir; R20/R24 the
predeclaration is bound by the commit that contains it (no candidate_commit field) and carries a role;
R21 a failing seal-check is a seal-check, not a refusal; R26 the installed broker gets the signed spec and
the canary file format is the one the scan reads; R15 refresh_acl.sh.
"""
import ast
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEAL = ROOT / "scripts" / "eval_seal"
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import author as A  # noqa: E402
from scripts.eval_seal import output as O  # noqa: E402
from scripts.eval_seal import render as R  # noqa: E402
from scripts.eval_seal import scan as SC  # noqa: E402
from scripts.eval_seal import spec as S  # noqa: E402
from scripts.eval_seal.codes import SealRefusal  # noqa: E402
from scripts.eval_seal.paths import SealPaths  # noqa: E402
from scripts import z1b_client as C  # noqa: E402
from scripts import z1b_promotion as P  # noqa: E402

DEV_HOME = "/Users/dev"


def _t18_spec():
    return S.load_spec(ROOT / "eval" / "z1b" / "spec.json")


def _t18_decl(**over):
    decl = {
        "role": "candidate", "suite_id": "0123456789abcdef", "primary_metric": "mrr",
        "critical_strata": ["exact_identifier", "path"],
        "non_inferiority_margins": {"critical": 0.02, "overall": 0.01},
        "improvement_target": {"metric": "mrr", "min_delta": 0.02},
        "resource_budgets": {"latency_p95_ms": 500, "peak_rss_mb": 4000},
        "resource_improvement": None,
    }
    decl.update(over)
    return decl


# ------------------------------------------------------------------ R17 wrappers, R26 export

def test_t18_every_wrapper_entry_is_a_module_level_function_of_its_module():
    for _name, mod, entry in R.WRAPPERS:
        tree = ast.parse((SEAL / (mod + ".py")).read_text())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert entry in defined, (mod, entry)


@pytest.mark.parametrize("module,entry", [("broker;import os", "main"), ("Broker", "main"), ("broker", "a b"),
                                          ("broker", "main\nimport os"), ("", "main")])
def test_t18_wrapper_source_refuses_names_that_could_inject_code(module, entry):
    with pytest.raises(R.OperatorRefusal):
        R.wrapper_source(module, entry)


def test_t18_wrapper_source_imports_the_named_entry_and_nothing_else():
    text = R.wrapper_source("broker", "broker_main")
    assert "from scripts.eval_seal.broker import broker_main as _entry\n" in text
    assert text.endswith("sys.exit(_entry(sys.argv[1:]))\n")


def test_t18_the_installed_broker_receives_the_signed_spec():
    assert "eval/z1b/spec.json" in R.EXPORT_PATHS


# ------------------------------------------------------------------ R15 refresh_acl.sh

def test_t18_refresh_acl_renders_a_read_only_regrant_on_every_transcript_dir(tmp_path):
    stage = tmp_path / "acl"
    r = subprocess.run(["/bin/bash", str(SEAL / "refresh_acl.sh"), "--render", str(stage), "--repo", str(ROOT),
                        "--dev-user", "dev", "--dev-home", DEV_HOME],
                       capture_output=True, text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path), "LANG": "C"})
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in (stage / "commands.txt").read_text().splitlines() if ln and not ln.startswith("#")]
    grants = [ln for ln in lines if " +a " in ln]
    # the transcript dirs get the read-only inherited grant; the projects dir itself gets exactly ONE listing grant
    # (no inheritance) so the scanner can enumerate transcript dirs (operator gate 2026-10-05: SCAN_INCOMPLETE)
    projects = DEV_HOME + "/.claude/projects"
    listing = [ln for ln in grants if ln.endswith(" " + projects)]
    assert listing == ["/bin/chmod +a 'user:zurvaneval allow list,search,readattr' " + projects]
    rest = [ln for ln in grants if ln not in listing]
    assert rest and all(projects + "/*Zurvan*" in ln for ln in rest)
    for ln in grants:
        for forbidden in ("write", "delete", "add_file", "add_subdirectory", "writeattr", "chown"):
            assert forbidden not in ln, ln
    removes = [i for i, ln in enumerate(lines) if " -a " in ln]
    assert removes and min(removes) < lines.index(grants[0])  # idempotent: drop the old entry first


def test_t18_refresh_acl_refuses_real_mode_for_a_non_root_user(tmp_path):
    r = subprocess.run(["/bin/bash", str(SEAL / "refresh_acl.sh"), "--repo", str(ROOT), "--dev-user", "dev",
                        "--dev-home", DEV_HOME], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C"})
    assert r.returncode == 2 and "real mode must run as root" in r.stderr


# ------------------------------------------------------------------ R18 private/

def test_t18_private_dir_sits_in_the_eval_home(tmp_path):
    p = SealPaths.for_root(tmp_path)
    assert p.private_dir == p.eval_home / "private"
    assert p.private_dir != p.runs_dir and p.runs_dir not in p.private_dir.parents


def test_t18_claude_session_state_goes_to_the_private_dir(tmp_path):
    seen = {}

    def fake_run(argv, **kw):
        seen["env"] = kw["env"]
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"result": "[]", "is_error": False}).encode())

    home = tmp_path / "Users" / "zurvaneval"
    runner = A.claude_runner(tmp_path / "bin" / "claude", home, run=fake_run)
    runner("author", "opus", "prompt", cwd=tmp_path)
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(home / "private" / "claude")
    assert seen["env"]["HOME"] == str(home)


def test_t18_seal_check_lists_the_private_dir_exclusion():
    assert "tm_excluded_private" in O.CHECK_IDS


# ------------------------------------------------------------------ R26 canary format

def test_t18_seal_suite_canaries_round_trip_through_the_scan_reader(tmp_path):
    paths = SealPaths.for_root(tmp_path)

    def plain_encrypt(data, out):
        Path(out).write_bytes(data)

    tokens = ["zc-" + ("%024x" % i) for i in range(A.AUTHOR_CANARIES)]
    records = A.author_canary_records(tokens)
    ctx = types.SimpleNamespace(decrypt=lambda p: Path(p).read_bytes())
    paths.sets_dir.mkdir(parents=True)
    (paths.sets_dir / "canaries.age").write_bytes(records)
    got = SC.scan_load_canaries(paths, ctx)
    assert [r["token"] for r in got] == tokens and all(r["item_id"] is None for r in got)


def test_t18_canary_records_refuse_tokens_the_scan_would_refuse():
    for bad in (["short"], ["has space" + "x" * 20], ["x" * 81], ["zc-é" + "x" * 20]):
        with pytest.raises(SealRefusal):
            A.author_canary_records(bad)


# ------------------------------------------------------------------ R20/R24 declaration binding

def test_t18_predeclaration_has_role_and_no_candidate_commit():
    schema = _t18_spec()["predeclaration_schema"]
    assert "candidate_commit" not in schema["required"] and "candidate_commit" not in schema["properties"]
    assert "role" in schema["required"]
    assert schema["properties"]["role"]["enum"] == ["baseline", "candidate"]


def test_t18_a_declaration_with_the_old_candidate_commit_field_is_refused_as_unknown():
    with pytest.raises(C.ClientRefusal) as exc:
        P.validate_declaration(_t18_decl(candidate_commit="d" * 40), _t18_spec())
    assert exc.value.code == "DECLARATION_INVALID"


@pytest.mark.parametrize("role", ["Baseline", "", None, 1, "promoted"])
def test_t18_a_declaration_with_an_unknown_role_is_refused(role):
    with pytest.raises(C.ClientRefusal):
        P.validate_declaration(_t18_decl(role=role), _t18_spec())


def test_t18_both_roles_validate():
    P.validate_declaration(_t18_decl(role="baseline"), _t18_spec())
    P.validate_declaration(_t18_decl(role="candidate"), _t18_spec())


# ------------------------------------------------------------------ R21 failing seal-check

def test_t18_a_failing_seal_check_reaches_the_client_as_a_seal_check(monkeypatch):
    doc = {"ok": False, "schema": O.SEALCHECK_SCHEMA_ID, "broker_sha": "a" * 40,
           "checks": {c: (c != "tm_excluded_private") for c in O.CHECK_IDS}}
    text = O.serialize_sealcheck(doc)

    def runner(cmd, **kw):
        return types.SimpleNamespace(returncode=2, stdout=text.encode())

    got = C.call_broker(["seal-check"], runner=runner)
    assert got["ok"] is False and got["checks"]["tm_excluded_private"] is False
    assert not hasattr(C, "_client_validate")


def test_t18_a_refusal_document_still_raises_its_code():
    text = O.serialize_refusal("SEAL_NOT_CONFIGURED", "a" * 40)

    def runner(cmd, **kw):
        return types.SimpleNamespace(returncode=2, stdout=text.encode())

    with pytest.raises(C.ClientRefusal) as exc:
        C.call_broker(["status"], runner=runner)
    assert exc.value.code == "SEAL_NOT_CONFIGURED"


# ------------------------------------------------------------------ R12 sandbox profile

def test_t18_sandbox_profile_names_no_developer_home():
    text = (SEAL / "sandbox" / "candidate.sb").read_text()
    literals = [ln.strip() for ln in text.splitlines() if '"/Users/' in ln and not ln.lstrip().startswith(";")]
    assert literals == ['(subpath "/Users/zurvaneval")']
    assert '(subpath (param "DEVHOME"))' in text
