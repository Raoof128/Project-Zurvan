"""check.sh gate [25/25] — status index drift.

Package A's plan (2026-11-03, Task: status index) said `status_index.py --check` would be run by the quality
gate so `docs/status/index.md` cannot drift from `docs/status/receipts.json`. It was never added to check.sh
(review follow-up (f), 2026-11-07). Same shape as tests/test_z0_closure.py::test_check_sh_gates_the_inventory:
the gate must be a live command under `set -e` that can fail the run, not a comment or an `|| true`.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check.sh"
GATE_CMD = "PYTHONPATH=. python scripts/status_index.py --check"
HEADING = "[25/25] Status index"


def _lines():
    return CHECK.read_text().splitlines()


def test_check_sh_runs_status_index_check_exactly_once_live_before_the_final_banner():
    lines = _lines()
    assert "set -e" in [l.strip() for l in lines]
    live = [i for i, l in enumerate(lines) if l.strip() == GATE_CMD]
    assert len(live) == 1, "exactly one live, unguarded `status_index.py --check` line"
    assert not any("status_index" in l and l.lstrip().startswith("#") for l in lines)
    assert not any("status_index" in l and ("|| true" in l or "||true" in l) for l in lines)
    final = max(i for i, l in enumerate(lines) if "All Zurvan checks passed" in l)
    exits = [i for i, l in enumerate(lines) if l.strip().startswith("exit 0")]
    assert live[0] < final and not any(i < live[0] for i in exits)


def test_the_gate_is_numbered_25_and_the_command_belongs_to_its_heading():
    lines = _lines()
    idx = [i for i, l in enumerate(lines) if HEADING in l]
    assert len(idx) == 1
    cmd = [i for i, l in enumerate(lines) if l.strip() == GATE_CMD][0]
    assert idx[0] < cmd and cmd - idx[0] < 10


def test_the_gate_command_passes_on_the_committed_page():
    """The page in this tree is current with its receipts (read-only: --check never writes)."""
    cp = subprocess.run([sys.executable, "scripts/status_index.py", "--check"], cwd=str(REPO),
                        capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(REPO)))
    assert cp.returncode == 0, cp.stderr
