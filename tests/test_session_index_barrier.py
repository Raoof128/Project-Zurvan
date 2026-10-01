"""`zurvan session start|close` and `zurvan index rebuild` write inside the
mutation barrier (filed 2026-09-28 as bypasses: they wrote wiki/sessions,
wiki/log.md and wiki/index.md with raw open(), so every use left unrecorded
drift that the next managed write minted as EXTERNAL_OBSERVED).

Subprocess against a throwaway store: the store root is bound at import.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


def _store(tmp_path):
    store = tmp_path / "Zurvan"
    for d in ("wiki/decisions", "wiki/sessions", "data"):
        (store / d).mkdir(parents=True)
    (store / "wiki" / "decisions" / "d.md").write_text('---\ntitle: d\n---\n')
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.json").write_text(json.dumps(
        {"current": "zurvan", "projects": {"zurvan": {"path": str(store)}}}))
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO),
           "ZURVAN_CONFIG_DIR": str(config)}
    return store, env


def _cli(store, env, *args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), *args],
                          cwd=str(store), env=env, capture_output=True, text=True)


def _no_drift(store, env):
    status = _cli(store, env, "knowledge", "status").stdout
    assert "drift:      none" in status, status
    assert "atomicity:  MANAGED" in status, status


def test_session_start_and_close_are_managed_writes(tmp_path):
    store, env = _store(tmp_path)
    assert _cli(store, env, "knowledge", "bootstrap").returncode == 0
    r = _cli(store, env, "session", "start", "--topic", "barrier probe")
    assert r.returncode == 0, r.stderr
    _no_drift(store, env)
    r = _cli(store, env, "session", "close", "--topic", "barrier probe",
             "--summary", "s", "--checks", "c")
    assert r.returncode == 0, r.stderr
    _no_drift(store, env)
    page = next((store / "wiki" / "sessions").glob("*barrier-probe.md")).read_text()
    assert "**Status**: Closed" in page
    log = (store / "wiki" / "log.md").read_text()
    assert "Session Started" in log and "Session Closed" in log


def test_index_rebuild_writes_the_store_index_inside_the_barrier(tmp_path):
    store, env = _store(tmp_path)
    assert _cli(store, env, "knowledge", "bootstrap").returncode == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "index", "rebuild"],
                       cwd=str(elsewhere), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (store / "wiki" / "index.md").exists()          # the STORE, not the cwd
    assert not (elsewhere / "wiki").exists()
    _no_drift(store, env)
