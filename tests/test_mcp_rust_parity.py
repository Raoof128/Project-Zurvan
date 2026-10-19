"""A/B parity harness (scripts/mcp_parity.py) against a THROWAWAY root.

Never the live store: both servers get ZURVAN_ROOT=tmp_path. Skips unless the
release binary has been built (cargo build --release in rust/zurvan-mcp).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "rust" / "zurvan-mcp" / "target" / "release" / "zurvan-mcp"
HARNESS = ROOT / "scripts" / "mcp_parity.py"
SENTINEL = "SENTINEL-SECRET-7f3a"

pytestmark = pytest.mark.skipif(not BIN.exists(), reason="zurvan-mcp release binary not built")


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    for d in ("wiki/traces", "wiki/sources", "docs", "eval", ".git", ".claude", ".remember", "sources", "data"):
        (root / d).mkdir(parents=True, exist_ok=True)
    files = {
        "wiki/index.md": "# Index\n\n" + "line of the index\n" * 2000,
        "wiki/log.md": "# Log\n",
        "wiki/overview.md": "# Overview\n",
        "wiki/open-questions.md": "# Open questions\n",
        "wiki/sources/x.md": "a source page\r\nwith CRLF\n",
        "wiki/traces/t.md": "trace twin\n",
        "docs/API.md": "# API\n",
        "eval/README.md": "# Eval\n",
        "README.md": "# Zurvan\n",
        "AGENTS.md": "# Agents\n",
        ".git/HEAD": f"ref: refs/heads/{SENTINEL}\n",
        ".claude/settings.json": f'{{"secret": "{SENTINEL}"}}\n',
        ".remember/now.md": f"{SENTINEL}\n",
        "sources/private-doc.md": f"{SENTINEL}\n",
    }
    for rel, text in files.items():
        (root / rel).write_bytes(text.encode("utf-8"))
    return root


def _run(args, env_extra=None, timeout=300):
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(HARNESS), *args], capture_output=True, text=True,
                          env=env, cwd=str(ROOT), timeout=timeout)


def test_listing_parity_on_fixture_root(tmp_path):
    root = _fixture_root(tmp_path)
    report = tmp_path / "report.json"
    p = _run(["--root", str(root), "--only", "listing,read_page,read_gate", "--report", str(report)])
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    rows = json.loads(report.read_text())
    verdicts = {r["case_id"]: r["verdict"] for r in rows}
    assert verdicts["listing:tools/list"] == "PASS"
    assert verdicts["listing:initialize.instructions"] == "PASS"
    assert verdicts["read_page:wiki/index.md@0"] == "PASS"
    assert verdicts["read_page:wiki/index.md@24000"] == "PASS"
    assert verdicts["read_gate:.claude/settings.json"] in ("EXPECTED_DIVERGENCE", "PASS")
    assert verdicts["read_gate:sources#0"] in ("EXPECTED_DIVERGENCE", "PASS")
    assert not [c for c, v in verdicts.items() if v == "FAIL"]
    # Privacy: the report and console carry ids, verdicts and sizes only.
    assert set(rows[0]) == {"case_id", "group", "verdict", "a_chars", "b_chars", "diff_lines"}
    assert SENTINEL not in report.read_text()
    assert SENTINEL not in p.stdout and SENTINEL not in p.stderr
    assert "private-doc" not in p.stdout and "private-doc" not in report.read_text()


def test_harness_flags_a_planted_difference(tmp_path):
    root = _fixture_root(tmp_path)
    report = tmp_path / "report.json"
    plant = f"{sys.executable} {ROOT / 'tests' / '_parity_plant_server.py'}"
    p = _run(["--root", str(root), "--only", "listing", "--b", plant, "--report", str(report)],
             env_extra={"ZURVAN_PARITY_PLANT": "1"})
    assert p.returncode == 1, p.stdout[-2000:]
    verdicts = {r["case_id"]: r["verdict"] for r in json.loads(report.read_text())}
    assert verdicts["listing:tools/list"] == "FAIL"


def test_both_protocols_on_fixture_root(tmp_path):
    root = _fixture_root(tmp_path)
    for proto in ("2025-11-25", "2026-07-28"):
        p = _run(["--root", str(root), "--only", "listing", "--protocol", proto, "--report", str(tmp_path / f"{proto}.json")])
        assert p.returncode == 0, proto + "\n" + p.stdout[-2000:] + p.stderr[-1500:]
