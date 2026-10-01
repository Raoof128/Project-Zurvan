"""`zurvan decision set-project`: correcting a page's subject INSIDE the barrier.

On 2026-10-01 a session found `project: "unknown"` on decisions it had just
added (the CLI ran outside any registered project, so resolution failed
closed) and fixed them with `sed -i` in wiki/decisions — outside the ledger,
with a non-slug value, rewriting all 77 matching pages (rev 301/302 drift).
There was no managed way to do it. Now there is, and `decision add` says so
when it stamps `unknown`.

Subprocess, as in test_knowledge_attribution: `scripts/memory.py` binds
`PROJECT_ROOT` at import.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


def _store(tmp_path, register_store=True):
    store = tmp_path / "Zurvan"
    (store / "wiki" / "decisions").mkdir(parents=True)
    (store / "data").mkdir()
    config = tmp_path / "config"
    config.mkdir()
    projects = {"zurvan": {"path": str(store)}} if register_store else {}
    (config / "projects.json").write_text(json.dumps({"current": None, "projects": projects}))
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO),
           "ZURVAN_CONFIG_DIR": str(config)}
    return store, env


def _cli(store, env, *args, cwd=None):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), *args],
                          cwd=str(cwd or store), env=env, capture_output=True, text=True)


def test_an_unknown_stamp_warns_and_names_the_managed_fix(tmp_path):
    store, env = _store(tmp_path)
    elsewhere = tmp_path / "unregistered-repo"
    elsewhere.mkdir()
    r = _cli(store, env, "decision", "add", "--title", "T", "--reason", "R",
             "--status", "accepted", cwd=elsewhere)
    assert r.returncode == 0, r.stderr
    assert 'project: "unknown"' in next((store / "wiki" / "decisions").glob("*.md")).read_text()
    assert "unknown" in r.stderr and "decision set-project" in r.stderr


def test_set_project_relabels_inside_the_barrier_as_one_revision(tmp_path):
    store, env = _store(tmp_path)
    elsewhere = tmp_path / "unregistered-repo"
    elsewhere.mkdir()
    for title in ("First", "Second"):
        assert _cli(store, env, "decision", "add", "--title", title, "--reason", "R",
                    "--status", "accepted", cwd=elsewhere).returncode == 0
    pages = sorted((store / "wiki" / "decisions").glob("*.md"))
    before = kr.current(store).revision

    r = _cli(store, env, "decision", "set-project", *[str(p) for p in pages],
             "--project", "otherproj")
    assert r.returncode == 0, r.stderr
    for p in pages:
        text = p.read_text()
        assert 'project: "otherproj"' in text and "unknown" not in text
    state = kr.current(store)
    assert state.revision == before + 1          # one logical operation, one revision
    assert state.atomicity == kr.MANAGED          # not EXTERNAL_OBSERVED: no drift
    assert kr.mutations_in(kr.COMMITTED, store)[-1].store_project == "otherproj"
    assert "Decision Project Set" in (store / "wiki" / "log.md").read_text()


def test_set_project_refuses_a_malformed_slug_before_writing(tmp_path):
    store, env = _store(tmp_path)
    assert _cli(store, env, "decision", "add", "--title", "T", "--reason", "R",
                "--status", "accepted").returncode == 0
    page = next((store / "wiki" / "decisions").glob("*.md"))
    original = page.read_bytes()
    r = _cli(store, env, "decision", "set-project", str(page), "--project", "Acme")
    assert r.returncode == 2 and "not a valid identifier" in r.stderr
    assert page.read_bytes() == original


def test_set_project_refuses_a_page_outside_wiki_decisions(tmp_path):
    store, env = _store(tmp_path)
    stray = store / "wiki" / "note-x.md"
    stray.write_text('---\ntitle: x\nproject: "unknown"\n---\n')
    r = _cli(store, env, "decision", "set-project", str(stray), "--project", "zurvan")
    assert r.returncode == 2
    assert 'project: "unknown"' in stray.read_text()


def test_set_project_changes_only_the_frontmatter_line(tmp_path):
    store, env = _store(tmp_path)
    assert _cli(store, env, "decision", "add", "--title", "T",
                "--reason", 'body mentions project: "unknown" literally',
                "--status", "accepted").returncode == 0
    page = next((store / "wiki" / "decisions").glob("*.md"))
    before = page.read_text()
    assert _cli(store, env, "decision", "set-project", str(page),
                "--project", "otherproj").returncode == 0
    after = page.read_text()
    assert 'body mentions project: "unknown" literally' in after
    assert [a for a, b in zip(after.splitlines(), before.splitlines()) if a != b] == \
        ['project: "otherproj"']
