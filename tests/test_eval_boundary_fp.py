"""The v2 FP harness must not be able to produce a flattering number.

Three ways a false-positive rate gets quietly faked, each closed by a test here:

* **Scanning what you ingested.** A clean verdict on a file that is in the
  protected corpus measures D3's exemption logic, not overreach. The v1 result
  (11/200) was entirely this. `assert_independent` refuses the run.
* **Choosing the sample after seeing the results.** The 200 are selected by a
  seeded, stratified, documented method and frozen to a manifest before the
  measurement executes.
* **Counting anchors instead of artefacts.** A 400-byte true positive still
  produces one match per fingerprint anchor, so match-counting inflates the
  denominator's meaning. One file is one artefact.
"""

import json
from pathlib import Path

import pytest

from scripts.eval_boundary_fp import (
    FP_CEILING,
    SeedResult,
    assert_independent,
    candidate_artefacts,
    freeze_manifest,
    measure,
    select_scan_corpus,
    stratum_of,
)


def test_a_scan_set_that_overlaps_the_ingested_corpus_is_refused():
    """The v1 failure mode, as a hard stop rather than a caveat."""
    seed = SeedResult(protected=[{"sha256": "aaa", "bytes": 1,
                                  "origin_class": "PRIVATE",
                                  "provenance_scheme": "local-file"}])
    scan = [{"path": "x.md", "sha256": "aaa", "bytes": 1, "stratum": "markdown"}]

    with pytest.raises(ValueError, match="ingestion relationship"):
        assert_independent(scan, seed)


def test_an_independent_scan_set_is_accepted():
    seed = SeedResult(protected=[{"sha256": "aaa", "bytes": 1,
                                  "origin_class": "PRIVATE",
                                  "provenance_scheme": "local-file"}])
    scan = [{"path": "x.md", "sha256": "bbb", "bytes": 1, "stratum": "markdown"}]

    assert_independent(scan, seed)      # must not raise


def test_acceded_and_declared_material_also_counts_as_ingested():
    """Independence is about INGESTION, not about which class it landed in.

    A file acceded to D3 is still a file the harness fed into the store; a
    "clean" verdict on it would be D3 working, which is not what this measures.
    """
    seed = SeedResult(
        acceded=[{"sha256": "ccc", "bytes": 1, "origin_class": "PUBLIC_VERIFIED",
                  "provenance_scheme": "git-commit"}],
        declared=[{"sha256": "ddd", "bytes": 1, "origin_class": "PUBLIC_DECLARED",
                   "provenance_scheme": "url-declared"}],
    )

    for digest in ("ccc", "ddd"):
        with pytest.raises(ValueError, match="ingestion relationship"):
            assert_independent(
                [{"path": "x.md", "sha256": digest, "bytes": 1, "stratum": "markdown"}],
                seed)


def test_selection_is_deterministic_for_a_given_seed(tmp_path):
    repo = _fake_repo(tmp_path, count=60)
    candidates = sorted(repo.rglob("*.md")) + sorted(repo.rglob("*.py"))

    first = select_scan_corpus(candidates, repo, size=20, seed=7)
    second = select_scan_corpus(candidates, repo, size=20, seed=7)
    different = select_scan_corpus(candidates, repo, size=20, seed=8)

    assert [r["path"] for r in first] == [r["path"] for r in second]
    assert [r["path"] for r in first] != [r["path"] for r in different]


def test_selection_is_stratified_rather_than_dominated_by_one_class(tmp_path):
    """A corpus that is 90% Markdown measures Markdown, not Boundary."""
    repo = _fake_repo(tmp_path, count=100)
    candidates = sorted(repo.rglob("*.md")) + sorted(repo.rglob("*.py"))

    chosen = select_scan_corpus(candidates, repo, size=20, seed=SEED)

    strata = {row["stratum"] for row in chosen}
    assert strata == {"markdown", "python"}
    counts = [sum(1 for r in chosen if r["stratum"] == s) for s in strata]
    assert min(counts) >= 5, f"one stratum dominated: {counts}"


def test_too_small_a_population_is_refused_rather_than_silently_shrunk(tmp_path):
    """<= 1/200 and <= 1/40 are different criteria; the harness may not swap them."""
    repo = _fake_repo(tmp_path, count=10)
    candidates = sorted(repo.rglob("*.md"))

    with pytest.raises(ValueError, match="Expand the benign source pool"):
        select_scan_corpus(candidates, repo, size=200, seed=SEED)


def test_the_manifest_is_frozen_before_measurement_and_records_the_method(tmp_path):
    scan = [{"path": "a.md", "sha256": "aaa", "bytes": 3, "stratum": "markdown"}]
    out = tmp_path / "manifest.json"

    manifest = freeze_manifest(out, scan, SeedResult(), "rev-123", SEED)
    written = json.loads(out.read_text(encoding="utf-8"))

    assert written == manifest
    assert written["selection"]["seed"] == SEED
    assert written["selection"]["method"]
    assert written["store_revision"] == "rev-123"
    assert written["scan_corpus"][0]["sha256"] == "aaa"
    assert written["criterion"].startswith(f"<= {FP_CEILING}")


def test_one_file_with_many_anchors_is_one_blocked_artefact(tmp_path):
    """The open item about anchor-per-match noise must not inflate the metric."""
    class _Match:
        def __init__(self):
            self.origin_id, self.detector = "opaque-1", "D1"

    class _Verdict:
        blocked = True
        matches = tuple(_Match() for _ in range(5))

    target = tmp_path / "a.md"
    target.write_text("x", encoding="utf-8")

    result = measure(tmp_path, tmp_path,
                     [{"path": "a.md", "sha256": "aaa", "bytes": 1,
                       "stratum": "markdown"}],
                     scan_fn=lambda *a, **k: _Verdict())

    assert result["blocked_artefacts"] == 1, "five anchors, one file, one artefact"
    assert result["total_matches"] == 5, "matches are still recorded, as diagnostics"
    assert result["artefacts"] == 1


def test_the_gate_fails_when_the_ceiling_is_exceeded(tmp_path):
    class _Verdict:
        blocked = True
        matches = ()

    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")

    rows = [{"path": n, "sha256": n, "bytes": 1, "stratum": "markdown"}
            for n in ("a.md", "b.md", "c.md")]
    result = measure(tmp_path, tmp_path, rows, scan_fn=lambda *a, **k: _Verdict())

    assert result["blocked_artefacts"] == 3
    assert not result["passed"]


def test_adversarial_fixtures_are_excluded_from_the_benign_population(tmp_path):
    """Including a file built to trigger Boundary would measure the fixture."""
    repo = _fake_repo(tmp_path, count=5, git=True)
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "boundary_redteam.py").write_text("attack", encoding="utf-8")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_boundary_gauntlet.py").write_text("fixture", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True)

    paths = {str(p.relative_to(repo)) for p in candidate_artefacts(repo)}

    assert "scripts/boundary_redteam.py" not in paths
    assert "tests/test_boundary_gauntlet.py" not in paths


def test_stratum_classification_covers_the_declared_extensions():
    assert stratum_of(Path("a.md")) == "markdown"
    assert stratum_of(Path("a.py")) == "python"
    assert stratum_of(Path("a.json")) == "config"
    assert stratum_of(Path("a.sh")) == "shell"
    assert stratum_of(Path("a.png")) is None


# ── helpers ───────────────────────────────────────────────────────────────────

SEED = 4242


def _fake_repo(tmp_path: Path, count: int, git: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    for i in range(count):
        (repo / f"doc-{i:03d}.md").write_text(f"# doc {i}\n", encoding="utf-8")
        (repo / f"mod-{i:03d}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
    if git:
        import subprocess
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)
    return repo
