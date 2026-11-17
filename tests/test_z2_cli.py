"""Z2 Task 7: the `zurvan claim` CLI, run as a subprocess against a throwaway root."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = "wiki/sources/s.md"
BODY = "Week 9 totals 52 days.\nWeek 10 totals 58 days.\n"


@pytest.fixture
def root(tmp_path):
    for d in ("wiki/sources", "wiki/claims", "wiki/decisions", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    return tmp_path


def zurvan(root, *args):
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", *args],
                          capture_output=True, text=True, env=env, cwd=str(REPO))


def _revision(root):
    conn = sqlite3.connect(root / "data" / "registry.sqlite")
    try:
        return conn.execute("SELECT current_revision FROM knowledge_state").fetchone()[0]
    finally:
        conn.close()


def _add(root):
    res = zurvan(root, "add", "--text", "Acme week 9", "--source", SRC,
                 "--evidence", "Week 9 totals 52 days", "--confidence", "high",
                 "--tags", "acme")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])["claim_id"]


def test_claim_add_cli_unchanged_contract(root):
    cid = _add(root)
    assert (root / "wiki" / "claims" / f"{cid}.md").is_file()


def test_supersede_cli_requires_reason(root):
    cid = _add(root)
    res = zurvan(root, "supersede", cid, "--text", "x", "--source", SRC,
                 "--evidence", "Week 10 totals 58 days", "--reason", " ")
    assert res.returncode == 1 and "REASON_REQUIRED" in res.stderr


def test_supersede_retract_revise_cli(root):
    cid = _add(root)
    res = zurvan(root, "supersede", cid, "--text", "Acme week 10", "--source", SRC,
                 "--evidence", "Week 10 totals 58 days", "--reason", "week closed",
                 "--valid-from", "2026-11-16")
    assert res.returncode == 0, res.stderr
    new = json.loads(res.stdout.strip().splitlines()[-1])["claim_id"]
    assert zurvan(root, "revise", new, "--reason", "basis", "--basis", "primary_source").returncode == 0
    assert zurvan(root, "retract", new, "--reason", "wrong", "--basis", "direct_measurement",
                  "--expected-rev", "2").returncode == 0
    res = zurvan(root, "retract", cid, "--reason", "x", "--expected-rev", "9")
    assert res.returncode == 1 and "CLAIM_CONFLICT" in res.stderr


def test_show_as_of_revision_cli(root):
    cid = _add(root)
    r1 = _revision(root)
    assert zurvan(root, "retract", cid, "--reason", "wrong").returncode == 0
    then = json.loads(zurvan(root, "show", cid, "--as-of-revision", str(r1), "--json").stdout)
    now = json.loads(zurvan(root, "show", cid, "--json").stdout)
    assert then["status"] == "asserted" and now["status"] == "retracted"
    hist = json.loads(zurvan(root, "history", cid, "--json").stdout)
    assert [h["event"] for h in hist] == ["assert", "retract"]
    listed = json.loads(zurvan(root, "list", "--status", "retracted", "--json").stdout)
    assert [c["claim_id"] for c in listed] == [cid]


def test_read_commands_open_no_operation(root):
    cid = _add(root)
    before = _revision(root)
    for args in (("show", cid), ("history", cid), ("list",), ("conflicts",)):
        assert zurvan(root, *args).returncode == 0
    assert _revision(root) == before


def test_conflicts_adopt_regenerate_cli(root):
    cid = _add(root)
    page = root / "wiki" / "claims" / f"{cid}.md"
    page.write_text(page.read_text(encoding="utf-8").replace("Acme week 9", "Acme wk 9"),
                    encoding="utf-8")
    assert cid in zurvan(root, "conflicts").stdout
    assert zurvan(root, "adopt", cid, "--reason", "typo").returncode == 0
    assert "Acme wk 9" in json.loads(zurvan(root, "show", cid, "--json").stdout)["text"]
    page.write_text(page.read_text(encoding="utf-8") + "\nnote\n", encoding="utf-8")
    assert zurvan(root, "regenerate", cid, "--force").returncode == 0
    assert zurvan(root, "conflicts").stdout.strip() == ""


def test_actor_and_method_recorded(root):
    res = zurvan(root, "add", "--text", "t", "--source", SRC, "--evidence",
                 "Week 9 totals 52 days", "--confidence", "low", "--actor", "local:someone",
                 "--method", "agent")
    cid = json.loads(res.stdout.strip().splitlines()[-1])["claim_id"]
    hist = json.loads(zurvan(root, "history", cid, "--json").stdout)
    assert hist[0]["actor"] == "local:someone" and hist[0]["method"] == "agent"
