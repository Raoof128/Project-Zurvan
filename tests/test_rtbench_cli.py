"""RTBench Task 8: CLI, reports, sealed holdout runs (R9)."""
import json, subprocess, sys

def _cli(*args, cwd="."):
    return subprocess.run([sys.executable, "scripts/cli.py", "bench", "rtbench", *args],
                          capture_output=True, text=True, env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"}, cwd=cwd)

def test_generate_and_run_dev_end_to_end(tmp_path):
    g = _cli("generate", "--split", "dev", "--out", str(tmp_path / "d"))
    assert g.returncode == 0, g.stderr
    r = _cli("run", "--adapter", "oracle", "--track", "S", "--split", "dev", "--cases-dir", str(tmp_path / "d"),
             "--report-dir", str(tmp_path / "r"), "--limit", "10")
    assert r.returncode == 0, r.stderr
    rep = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert rep["score"]["headline"] == 1.0

def test_zurvan_on_track_n_is_refused(tmp_path):
    r = _cli("run", "--adapter", "zurvan", "--track", "N", "--split", "dev", "--cases-dir", str(tmp_path))
    assert r.returncode == 2
    assert "Track S only" in r.stderr, r.stderr   # the refusal, not an argparse error

def test_holdout_report_has_no_items(tmp_path):
    from scripts.rtbench.generate import write_split
    from scripts.rtbench.adapters import OverwriteAdapter
    from scripts.rtbench.run import run_cases
    from scripts.rtbench.cli import write_report
    from scripts.rtbench.generate import generate_case
    pairs = [generate_case(s, f"h-{i}") for i, s in enumerate([11, 12, 13])]
    rep = run_cases(OverwriteAdapter(), "S", [c for c, _ in pairs], [g for _, g in pairs], itemised=False)
    rep["split"] = "holdout"
    j, m = write_report(rep, tmp_path)
    text = j.read_text() + m.read_text()
    assert "h-0" not in text and "qid" not in text


def test_zurvan_through_the_cli_is_not_nested_in_the_live_operation(tmp_path):
    """cli.py wraps every command in a live-store operation; the Zurvan adapter's
    temporary-store operations must not be opened inside it (NestedMutationError
    would turn every ingest into a counted error)."""
    assert _cli("generate", "--split", "dev", "--out", str(tmp_path / "d")).returncode == 0
    r = _cli("run", "--adapter", "zurvan", "--track", "S", "--split", "dev", "--cases-dir", str(tmp_path / "d"),
             "--report-dir", str(tmp_path / "r"), "--work-dir", str(tmp_path / "w"), "--limit", "2")
    assert r.returncode == 0, r.stderr
    rep = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert rep["errors"] == 0 and rep["score"]["headline"] == 1.0


def test_holdout_files_are_private(tmp_path):
    import os, stat
    env = {"PYTHONPATH": ".", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "scripts/cli.py", "bench", "rtbench", "generate", "--split", "holdout"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    inst = tmp_path / ".zurvan" / "instruments"
    mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
    assert mode(inst / "rtbench_holdout") == 0o700
    assert {mode(p) for p in (inst / "rtbench_holdout").iterdir()} == {0o600}
    assert mode(inst / "rtbench_holdout.seeds") == 0o600
