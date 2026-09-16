"""The commit gate: `scripts/boundary_guard.py`.

Two distinct outcomes that must never be confused:
  NOT CONFIGURED - no store exists; the gate says so loudly and exits 0
  ACTIVE         - a store exists; anything it cannot prove clean exits 1
"""
import subprocess
import sys

from scripts import boundary_guard


def test_no_store_reports_not_configured_and_does_not_block(tmp_path, capsys):
    """Boundary is opt-in. Blocking every commit before setup is not fail-closed,
    it is broken — and it teaches people to bypass the gate."""
    rc = boundary_guard.main(boundary_root=tmp_path / "absent", repo=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "NOT CONFIGURED" in out


def test_a_configured_store_blocks_protected_content(tmp_path, capsys):
    import scripts.boundary as boundary
    root = tmp_path / "b"
    (root / "sources").mkdir(parents=True)
    PROT = ("The quarterly containment review concluded that the residue threshold "
            "must be recalibrated before the next audit cycle begins.")
    (root / "sources" / "a.md").write_text(PROT, encoding="utf-8")
    boundary.build(root)

    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "leak.md").write_text(f"notes\n\n{PROT}\n", encoding="utf-8")
    subprocess.run(["git", "add", "leak.md"], cwd=repo, check=True)

    rc = boundary_guard.main(boundary_root=root, repo=repo)
    out = capsys.readouterr().out
    assert rc == 1
    assert "leak.md" in out
    assert PROT[:40] not in out, "the guard must not echo the protected span"


def test_a_configured_store_passes_clean_content(tmp_path, capsys):
    import scripts.boundary as boundary
    root = tmp_path / "b"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "a.md").write_text("Protected material lives here only.",
                                           encoding="utf-8")
    boundary.build(root)
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "fine.md").write_text("ordinary engineering prose, nothing protected\n",
                                  encoding="utf-8")
    subprocess.run(["git", "add", "fine.md"], cwd=repo, check=True)
    assert boundary_guard.main(boundary_root=root, repo=repo) == 0


def test_a_broken_store_fails_closed_rather_than_passing(tmp_path, capsys):
    """An unreadable store must not be treated as 'nothing to check'."""
    import scripts.boundary as boundary
    root = tmp_path / "b"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "a.md").write_text("Protected material here.", encoding="utf-8")
    boundary.build(root)
    (root / "boundary.sqlite").write_bytes(b"\x00corrupt")
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    rc = boundary_guard.main(boundary_root=root, repo=repo)
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out
