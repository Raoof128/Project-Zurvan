"""Z1b Task 17c: check.sh gate [24/25] Z1b seal.

Same pattern as tests/test_z0_closure.py::test_check_sh_gates_the_inventory: the gate must be a live
command under `set -e`, before the final banner. On top of that: every gate heading says /25, numbering
is contiguous 0..25, and the gate prints `Z1b: NOT CONFIGURED` (never a pass) on a machine without the
real identity. The configured/unconfigured branches are executed for real with PATH shims.
"""
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check.sh"
GATE_CMD = "PYTHONPATH=. python scripts/cli.py eval z1b seal-check"
_T17_HEADING = re.compile(r'^\s*echo(?: -e)? "(?:\\n)?\[(\d+)/(\d+)\]')


def _t17_lines():
    return CHECK.read_text().splitlines()


def test_t17_check_sh_runs_seal_check_exactly_once_live_before_the_final_banner():
    lines = _t17_lines()
    assert "set -e" in [l.strip() for l in lines]
    live = [i for i, l in enumerate(lines) if l.strip() == GATE_CMD]
    assert len(live) == 1, "exactly one live, unguarded seal-check line"
    assert not any(GATE_CMD in l and l.lstrip().startswith("#") for l in lines)
    assert not any("seal-check" in l and "|| true" in l for l in lines)
    final = max(i for i, l in enumerate(lines) if "All Zurvan checks passed" in l)
    exits = [i for i, l in enumerate(lines) if l.strip().startswith("exit 0")]
    assert live[0] < final and not any(i < live[0] for i in exits)


def test_t17_every_gate_heading_says_25_and_numbering_is_contiguous_0_to_25():
    heads = [(int(m.group(1)), int(m.group(2))) for m in map(_T17_HEADING.match, _t17_lines()) if m]
    assert len(heads) == 26, heads
    assert all(total == 25 for _n, total in heads), [h for h in heads if h[1] != 25]
    assert [n for n, _t in heads] == list(range(26))  # contiguous and in file order
    assert "/23]" not in CHECK.read_text()


def test_t17_the_gate_is_numbered_24_of_25_and_named_z1b_seal():
    lines = _t17_lines()
    idx = [i for i, l in enumerate(lines) if '[24/25] Z1b seal' in l]
    assert len(idx) == 1
    cmd = [i for i, l in enumerate(lines) if l.strip() == GATE_CMD][0]
    assert idx[0] < cmd and cmd - idx[0] < 15  # the command belongs to this heading


def _t17_gate_block():
    lines = _t17_lines()
    start = [i for i, l in enumerate(lines) if '[24/25] Z1b seal' in l][0]
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(lines[start:end + 1])


def _t17_shims(tmp_path, python_rc, id_rc):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "python.log"
    (bin_dir / "python").write_text('#!/bin/sh\necho "$@" >> "%s"\nexit %d\n' % (log, python_rc))
    (bin_dir / "id").write_text("#!/bin/sh\nexit %d\n" % id_rc)
    for name in ("python", "id"):
        (bin_dir / name).chmod(0o755)
    return bin_dir, log


def _t17_run_block(tmp_path, *, id_rc, python_rc=0, broker_present=True):
    bin_dir, log = _t17_shims(tmp_path, python_rc, id_rc)
    block = _t17_gate_block()
    broker = tmp_path / "zurvan-broker"
    broker.write_text("#!/bin/sh\n")
    broker.chmod(0o755 if broker_present else 0o644)
    block = block.replace("/Library/ZurvanEval/broker/bin/zurvan-broker", str(broker))
    assert str(broker) in block
    return subprocess.run(["/bin/bash", "-e", "-c", block], cwd=str(tmp_path), capture_output=True, text=True,
                          env={"PATH": "%s:/usr/bin:/bin" % bin_dir}), log


def _t17_fresh(tmp_path, id_rc, broker_present):
    d = tmp_path / ("case-%d-%d" % (id_rc, int(broker_present)))
    d.mkdir()
    return d


def test_t17_unconfigured_prints_not_configured_exits_0_and_never_says_pass(tmp_path):
    for id_rc, broker_present in ((1, True), (0, False), (1, False)):
        cp, log = _t17_run_block(_t17_fresh(tmp_path, id_rc, broker_present),
                                 id_rc=id_rc, broker_present=broker_present)
        assert cp.returncode == 0, cp.stderr
        assert "Z1b: NOT CONFIGURED" in cp.stdout
        assert "pass" not in cp.stdout.lower() and "✅" not in cp.stdout
        assert not log.exists()  # seal-check was not invoked


def test_t17_configured_runs_seal_check_and_reports_pass_only_when_it_exits_0(tmp_path):
    ok, log = _t17_run_block(_t17_fresh(tmp_path, 0, True), id_rc=0, python_rc=0)
    assert ok.returncode == 0 and "Z1b seal-check passed" in ok.stdout and "NOT CONFIGURED" not in ok.stdout
    assert log.read_text().split() == ["scripts/cli.py", "eval", "z1b", "seal-check"]


@pytest.mark.parametrize("rc", [1, 2, 3])
def test_t17_configured_seal_check_failure_fails_the_gate_and_never_prints_pass(tmp_path, rc):
    case = tmp_path / ("fail%d" % rc)
    case.mkdir()
    cp, _log = _t17_run_block(case, id_rc=0, python_rc=rc)
    assert cp.returncode == rc
    assert "passed" not in cp.stdout and "✅" not in cp.stdout


def test_t17_the_gate_block_syntax_and_whole_script_parse():
    assert subprocess.run(["/bin/bash", "-n", str(CHECK)], capture_output=True).returncode == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="identity lookup shape is macOS-specific")
def test_t17_skip_reason_is_printed_when_the_live_module_skips():
    """`pytest -rs` must show the loud reason, so an unconfigured run is visibly UNVERIFIED, not green."""
    try:
        pwd.getpwnam("zurvaneval")
        pytest.skip("Z1b: configured machine - the live module runs instead of skipping")
    except KeyError:
        pass
    cp = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                         "tests/test_z1b_seal_live.py"], cwd=str(REPO), capture_output=True, text=True,
                        env=dict(os.environ, PYTHONPATH=str(REPO)))
    text = cp.stdout + cp.stderr
    assert "SKIPPED" in text and "Z1b: NOT CONFIGURED" in text, text[-600:]
    assert " passed" not in text.replace("0 passed", "")  # no test in that module may pass while unconfigured
