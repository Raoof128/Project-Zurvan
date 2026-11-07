"""Z1b Task 17a: public_repo_guard refuses every tracked eval/z1b/* file that is not on the allowlist.

Pattern of tests/test_check_gate_repo_guard.py: drive main() with a stubbed `git ls-files`. The stub
asserts the command, so any new subprocess call added to main() fails these tests.
"""
import subprocess

import pytest

import scripts.public_repo_guard as guard


def _t17_fake_ls_files(monkeypatch, paths):
    calls = []

    def _run(cmd, capture_output, text, check):
        calls.append(cmd)
        assert cmd == ["git", "ls-files"]
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(paths), stderr="")
    monkeypatch.setattr(guard.subprocess, "run", _run)
    return calls


_T17_ALLOWED = [
    "eval/z1b/spec.json", "eval/z1b/manifest.json", "eval/z1b/manifest.json.sig", "eval/z1b/allowed_signers",
    "eval/z1b/rubric.md", "eval/z1b/promotions.jsonl",
    "eval/z1b/declarations/pre-one.json", "eval/z1b/declarations/.gitkeep",
    "eval/z1b/prompts/author.md", "eval/z1b/prompts/nested/reviewer.md",
]

_T17_BLOCKED = [
    "eval/z1b/holdout.jsonl", "eval/z1b/reserve.jsonl", "eval/z1b/dev.jsonl", "eval/z1b/dev-ref.jsonl",
    "eval/z1b/sets/x", "eval/z1b/sets/holdout.age", "eval/z1b/holdout.age", "eval/z1b/canaries.age",
    "eval/z1b/../x", "eval/z1b/declarations/../../x", "eval/z1b/declarations/../holdout.jsonl",
    "eval/z1b/prompts/../sets/x", "docs/../eval/z1b/holdout.jsonl", "eval/./z1b/holdout.jsonl",
    "eval//z1b/holdout.jsonl", "eval/z1b//spec.json", "eval/z1b/./spec.json",
    "eval/Z1B/holdout.jsonl", "eval/z1B/spec.json", "EVAL/z1b/manifest.json", "eval/Z1B/declarations/x.json",
    "eval/z1b", "eval/z1b/", "eval/z1b/spec.json.bak", "eval/z1b/manifest.json.sig.old",
    "eval/z1b/spec.JSON", "eval/z1b/Rubric.md", "eval/z1b/promotions.jsonl.orig",
    "eval/z1b/declarations/x.age", "eval/z1b/declarations/x.bundle", "eval/z1b/prompts/leak.sqlite",
    "eval/z1b/prompts/k.key", "eval/z1b/declarations/", "eval/z1b/declarations",
    "eval/z1b/declarations\\x.json", "eval/z1b/sub/spec.json", "eval/z1b/keys/age.key",
    "eval/z1b/spec.json\x00.age", "eval/z1b/declarations/a\\b.json", "eval/z1b/prompts/a\x00b.md",
]


@pytest.mark.parametrize("allowed", _T17_ALLOWED)
def test_t17_guard_allows_each_allowlisted_eval_z1b_file(monkeypatch, capsys, allowed):
    _t17_fake_ls_files(monkeypatch, ["README.md", allowed])
    guard.main()
    assert "Public repo guard passed." in capsys.readouterr().out


@pytest.mark.parametrize("tracked", _T17_BLOCKED)
def test_t17_guard_blocks_every_other_eval_z1b_path(monkeypatch, capsys, tracked):
    _t17_fake_ls_files(monkeypatch, ["README.md", tracked])
    with pytest.raises(SystemExit) as excinfo:
        guard.main()
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Public repo guard failed." in out and tracked in out


def test_t17_guard_allows_a_full_legitimate_z1b_tree_and_unrelated_eval_files(monkeypatch, capsys):
    _t17_fake_ls_files(monkeypatch, _T17_ALLOWED + ["eval/datasets.json", "eval/search_gold.manifest.json",
                                                    "eval/z1bish/file.json", "docs/eval/z1b/notes.md",
                                                    "tests/test_z1b_spec.py", "scripts/z1b_client.py"])
    guard.main()
    assert "passed" in capsys.readouterr().out


def test_t17_guard_reports_every_z1b_offender_and_combines_with_other_blocks(monkeypatch, capsys):
    _t17_fake_ls_files(monkeypatch, ["eval/z1b/holdout.jsonl", "raw/a.txt", "eval/z1b/sets/x", "eval/z1b/spec.json"])
    with pytest.raises(SystemExit):
        guard.main()
    out = capsys.readouterr().out
    assert "eval/z1b/holdout.jsonl" in out and "eval/z1b/sets/x" in out and "raw/a.txt" in out
    assert " - eval/z1b/spec.json" not in out


def test_t17_guard_makes_exactly_one_subprocess_call(monkeypatch, capsys):
    calls = _t17_fake_ls_files(monkeypatch, _T17_ALLOWED)
    guard.main()
    assert calls == [["git", "ls-files"]]


def test_t17_allowlist_constants_match_the_contract():
    assert guard.EVAL_Z1B_ALLOWED == {
        "eval/z1b/spec.json", "eval/z1b/manifest.json", "eval/z1b/manifest.json.sig",
        "eval/z1b/allowed_signers", "eval/z1b/rubric.md", "eval/z1b/promotions.jsonl"}
    assert guard.EVAL_Z1B_ALLOWED_PREFIXES == ("eval/z1b/declarations/", "eval/z1b/prompts/")


def test_t17_the_shipped_tree_passes_its_own_guard():
    """Every currently tracked eval/z1b file (spec.json, ...) must be allowlisted."""
    out = subprocess.run(["git", "ls-files", "eval/z1b"], capture_output=True, text=True, check=True).stdout
    assert guard.eval_z1b_offenders(out.splitlines()) == []


# ------------------------------------------------------------------ Task 17b: .claude/settings.json deny rules
#
# Syntax used (Claude Code permission rules, `permissions.deny`): `Read(//abs/path/**)` and `Edit(//abs/path/**)`
# - the leading `//` is the absolute-filesystem-path form (a single `/` means project-relative), `Edit` rules apply
# to every file-editing tool, and `Bash(*<path>*)` is a substring wildcard on the command. UNVERIFIED against the
# live harness (no harness here to deny a real tool call); Bash rules are best effort by nature. The OS permission
# (Z1b gate 1) is the wall; these rules stop an honest session from even trying.

import json
from pathlib import Path

from scripts.eval_seal.paths import SealPaths

_T17_SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
_T17_ROOTS = ("/Users/zurvaneval", "/Library/ZurvanEval/corpus", "/Library/ZurvanEval/stage",
              "/Users/zurvancandidate")


def _t17_deny():
    return json.loads(_T17_SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]


def _t17_covers(rule, tool, path):
    """An independent reading of the rule syntax (does not use the client under test)."""
    if not rule.startswith(tool + "(//") or not rule.endswith(")"):
        return False
    body = rule[len(tool) + 1:-1]  # "//abs/**"
    body = body[1:]  # "/abs/**"
    body = body[:-3] if body.endswith("/**") else body
    return path == body or path.startswith(body + "/")


def test_t17_settings_json_parses_and_keeps_its_existing_hooks_and_env():
    data = json.loads(_T17_SETTINGS.read_text(encoding="utf-8"))
    assert data["env"]["ZURVAN_EMBED_PROVIDER"] == "sentence_transformers"
    assert "agent prime" in data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert isinstance(data["permissions"]["deny"], list) and data["permissions"]["deny"]


def test_t17_settings_deny_rules_cover_every_sealed_path_for_read_and_edit():
    import scripts.z1b_client as zc
    deny = _t17_deny()
    sealed = zc._client_sealed_paths(SealPaths.real())
    assert len(sealed) >= 10
    for path in sealed:
        for tool in ("Read", "Edit"):
            assert any(_t17_covers(r, tool, str(path)) for r in deny), (tool, str(path))


@pytest.mark.parametrize("root", _T17_ROOTS)
def test_t17_each_sealed_root_has_the_literal_read_edit_and_bash_rules(root):
    deny = _t17_deny()
    for tool in ("Read", "Edit"):
        assert "%s(/%s/**)" % (tool, root) in deny
    assert "Bash(*%s*)" % root in deny


def test_t17_settings_carry_no_write_rule_because_claude_code_never_matches_one():
    # Claude Code checks every file write against Edit(...) rules; a Write(...) deny rule is
    # never consulted and the harness warns "Deny rule Write(...) never matches" (2026-11-04).
    assert not [r for r in _t17_deny() if r.startswith("Write(")]


def test_t17_settings_rules_equal_what_the_client_expects_so_seal_check_and_the_file_cannot_drift():
    import scripts.z1b_client as zc
    assert set(zc.client_deny_rules(SealPaths.real())) <= set(_t17_deny())
    assert zc._client_deny_rules_present([str(p) for p in zc._client_sealed_paths(SealPaths.real())]) is True


def test_t17_settings_deny_has_no_single_slash_project_relative_rules_for_sealed_roots():
    for rule in _t17_deny():
        for root in _T17_ROOTS:
            assert "(%s" % root not in rule, rule  # `(/Users/...` would be relative to the project root


def test_t17_settings_deny_rules_only_use_known_tools():
    import re
    for rule in _t17_deny():
        assert re.fullmatch(r"(Read|Edit|Write|Bash)\(.+\)", rule), rule
