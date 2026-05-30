import pytest
import subprocess

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
    assert "Created wiki/note-test-note.md" in res.stdout
