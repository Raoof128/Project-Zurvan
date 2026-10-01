"""check.sh gate 3 (reliability gauntlet) runs ingest -> extract -> audit in a
THROWAWAY store. It ran against the live store (filed 2026-09-28): every gate
ingested five raw/ sources into the corpus every baseline is measured on.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_the_gauntlet_processes_a_source_without_touching_any_real_store(tmp_path):
    project = tmp_path / "project"
    (project / "raw" / "notes").mkdir(parents=True)
    note = project / "raw" / "notes" / "n.txt"
    note.write_text("Zurvan stores decisions as Markdown pages.\n")
    before = note.read_bytes()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = {**os.environ, "ZURVAN_LLM_PROVIDER": "mock", "TMPDIR": str(scratch),
           "PYTHONPATH": str(REPO)}
    env.pop("ZURVAN_ROOT", None)

    r = subprocess.run([sys.executable, str(REPO / "scripts" / "run_reliability_gauntlet.py"),
                        "raw/notes/n.txt"], cwd=str(project), env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    summary = r.stdout.split("Gauntlet Summary")[-1]
    assert "raw/notes/n.txt:" in summary and "Failed" not in summary, r.stdout
    assert sorted(p.name for p in project.iterdir()) == ["raw"]   # no wiki/ or data/ beside it
    assert note.read_bytes() == before                             # raw/ is read, never written
    assert list(scratch.iterdir()) == []                           # the throwaway store is gone
