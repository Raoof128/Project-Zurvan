import pytest
import subprocess
import os

def test_cli_help(capsys):
    res = subprocess.run(["python", "scripts/cli.py", "--help"], capture_output=True, text=True)
    assert "Zurvan - Local-first CLI Memory Interface" in res.stdout
    assert "remember" in res.stdout
    assert "decision" in res.stdout
    assert "claim" in res.stdout

def test_cli_remember():
    # Test just calling it to ensure argparse is set up correctly (will write to wiki but that's safe in test)
    res = subprocess.run(["python", "scripts/cli.py", "remember", "--title", "Test Note", "--body", "Test body"], capture_output=True, text=True)
    assert res.returncode == 0
    assert os.path.join("wiki", "note-test-note.md") in res.stdout

def test_cli_eval_validate_gold_propagates_failure():
    # Regression: subprocess-backed commands ignored the child's exit code, so
    # a failing eval (missing gold file, threshold miss) still exited 0.
    import sys
    res = subprocess.run(
        [sys.executable, "scripts/cli.py", "eval", "validate-gold",
         "--gold", "eval/definitely_missing_gold.jsonl"],
        capture_output=True, text=True,
    )
    assert res.returncode != 0

def test_cli_subcommands_work_from_foreign_cwd(tmp_path):
    # Regression: subprocess-backed commands spawned "python scripts/..."
    # relative to the CWD, so they broke when invoked from any other directory.
    import sys
    cli_path = os.path.abspath(os.path.join("scripts", "cli.py"))
    res = subprocess.run(
        [sys.executable, cli_path, "eval", "validate-gold"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert res.returncode == 0
    assert "validated successfully" in res.stdout

def test_zurvan_wrapper_works_from_any_cwd(tmp_path):
    # scripts/zurvan is the PATH wrapper: resolves the repo through symlinks,
    # sets PYTHONPATH, and execs cli.py from any working directory.
    wrapper = os.path.abspath(os.path.join("scripts", "zurvan"))
    res = subprocess.run([wrapper, "version"], capture_output=True, text=True, cwd=str(tmp_path))
    assert res.returncode == 0
    assert "Zurvan Version" in res.stdout
