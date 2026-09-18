"""The production commit gate must implement the policy that was measured.

Scan-side sink provenance was designed, given 13 contracts, and measured at
0/200 on a frozen operational corpus — and then `boundary_guard` called
`gitscan.scan_staged` without a baseline, so `baseline=None` reached the
scanner and none of it was active in a real commit. The evaluation path and the
production path had different semantics, and the 0/200 was evidence about the
former only.

These tests go through `boundary_guard.main` — the real entry point, the one a
pre-commit hook calls — rather than through `scan_bytes`. A test that reaches
past the caller cannot detect a caller that fails to pass the argument, which is
exactly how the gap survived a 13-contract suite.

The closing assertion is the one that was missing: for the same candidates and
the same frozen baseline, the production gate and the evaluation scanner must
return the same verdicts.

**Direction of the change.** Wiring the baseline makes the gate more permissive,
so the tests that matter most here are the ones that pin what must STILL block:
a private span appended to a public file, the same leak staged twice, a
candidate that is already in the index, and evidence offered by a repository
this gate does not protect.
"""

import subprocess
from pathlib import Path

import pytest

from scripts import boundary, boundary_guard
from scripts.boundary import gitscan

PRIVATE_DOC = (
    "The quarterly containment review concluded that the residue threshold must "
    "be recalibrated before the next audit cycle begins in earnest, and that the "
    "reviewers should be rotated."
)
OTHER_PRIVATE = (
    "Escalation policy for the retention programme requires two named approvers "
    "before any archived deposition bundle may be reopened, and the second "
    "approver may not be the requesting analyst."
)
PUBLIC_PREAMBLE = (
    "# Project template\n\n"
    "> For agentic workers: this preamble is shared by every generated plan in "
    "the repository and carries no project-specific knowledge whatsoever.\n\n"
    "## Section\n\nOrdinary published documentation follows here.\n"
)
#: No LEADING newline, deliberately. The protected source continues with "\n"
#: after the preamble, so an edit that also starts with "\n" extends the
#: confirmed span one byte past the public blob and is correctly NOT exempt.
#: The first draft of these tests used "\n<!-- ... -->\n" and failed for exactly
#: that reason — the straddling case, which `test_a_benign_edit_that_straddles...`
#: now pins on purpose instead of by accident.
BENIGN_EDIT = "<!-- gate policy tests: a benign appended line -->\n"
STRADDLING_EDIT = "\n<!-- this edit begins with the byte the protected source continues with -->\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _new_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    return path


@pytest.fixture
def store(tmp_path):
    """Protected corpus carrying the shared boilerplate AND two private docs.

    The boilerplate is protected for the same reason it is protected in the real
    corpus: the private documents were ingested with it inside them. A candidate
    reusing that boilerplate therefore anchors against a protected origin, and
    only sink provenance distinguishes it from a genuine leak.
    """
    root = tmp_path / "boundary"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "private-a.md").write_text(
        PUBLIC_PREAMBLE + "\n" + PRIVATE_DOC, encoding="utf-8")
    (root / "sources" / "private-b.md").write_text(OTHER_PRIVATE, encoding="utf-8")
    boundary.build(root)
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """The protected repository: the boilerplate is already published here."""
    r = _new_repo(tmp_path / "repo")
    (r / "public-doc.md").write_text(PUBLIC_PREAMBLE, encoding="utf-8")
    _git(r, "add", "public-doc.md")
    _git(r, "commit", "-qm", "publish the template")
    monkeypatch.setenv("ZURVAN_PROTECTED_REPO", str(r.resolve()))
    return r


def _stage(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", rel)


# ── 1 + 2. the mismatch itself ───────────────────────────────────────────────

def test_a_benign_edit_to_an_already_public_file_is_not_blocked_by_the_gate(
        store, repo, capsys):
    """The exact shape of the six candidates the operational corpus rescued.

    The first assertion documents what the production path used to do: with no
    baseline, this benign edit BLOCKS. The second is what the gate must do now
    that it supplies one. Both are asserted here so the fix cannot be read as a
    weakening of the detector — the detector still matches; the policy decides.
    """
    candidate = PUBLIC_PREAMBLE + BENIGN_EDIT
    without_baseline = boundary.scan_bytes(store, candidate.encode(),
                                           gitscan.sinks_for("public-doc.md"))
    assert without_baseline.blocked, (
        "fixture is not exercising the property: this candidate does not even "
        "match a protected origin, so it could never have been a false positive")

    _stage(repo, "public-doc.md", candidate)
    rc = boundary_guard.main(boundary_root=store, repo=repo)
    out = capsys.readouterr().out

    assert rc == 0, (
        "the production gate blocked bytes that were already in the accepted "
        f"sink; scan-side sink provenance is not reaching the scanner.\n{out}")
    assert "clean" in out.lower()


# ── the limitation this exposed, pinned rather than hidden ───────────────────

def test_a_benign_edit_that_straddles_the_public_boundary_still_blocks(
        store, repo, capsys):
    """A real sharp edge, reachable from a real commit.

    `d1.confirm` expands a match maximally in both directions. If the appended
    bytes begin with the same byte the protected source continues with — a
    trailing blank line, which is ordinary in Markdown — the confirmed span
    reaches ONE byte past the public blob, `contains_span` is false, and the
    benign edit blocks. Measured here: 224-byte span against a 223-byte public
    blob.

    Residual subtraction is the principled fix and is deliberately not built:
    neither frozen corpus produced a straddling case, so building it now would
    be designing against a predicted failure rather than an observed one. What
    is NOT acceptable is leaving it undocumented, so this test exists to make
    the behaviour deliberate and to fail loudly if it ever changes silently.
    """
    _stage(repo, "public-doc.md", PUBLIC_PREAMBLE + STRADDLING_EDIT)
    rc = boundary_guard.main(boundary_root=store, repo=repo)
    assert rc == 1, (
        "the straddling case now passes — if that was intended, residual "
        "subtraction landed and this test should be rewritten, not deleted")
    assert "public-doc.md" in capsys.readouterr().out


# ── 3. a new private span in a public file still blocks ──────────────────────

def test_a_private_span_appended_to_a_public_file_still_blocks(store, repo, capsys):
    """Exemption is per match. If it were per file this would be the bypass."""
    _stage(repo, "public-doc.md", PUBLIC_PREAMBLE + "\n" + PRIVATE_DOC + "\n")
    rc = boundary_guard.main(boundary_root=store, repo=repo)
    out = capsys.readouterr().out

    assert rc == 1, "appending protected material to a published page was allowed"
    assert "public-doc.md" in out
    assert PRIVATE_DOC[:40] not in out, "the guard must not echo the protected span"


def test_a_wholly_new_private_file_still_blocks(store, repo, capsys):
    _stage(repo, "notes/leak.md", "meeting notes\n\n" + OTHER_PRIVATE + "\n")
    rc = boundary_guard.main(boundary_root=store, repo=repo)
    assert rc == 1
    assert "notes/leak.md" in capsys.readouterr().out


# ── 4. intra-batch laundering, through the real gate ─────────────────────────

def test_two_staged_files_carrying_the_same_leak_are_both_reported(
        store, repo, capsys):
    """Neither candidate may become evidence that the other is already public."""
    _stage(repo, "one.md", "intro\n\n" + PRIVATE_DOC + "\n")
    _stage(repo, "two.md", "different intro\n\n" + PRIVATE_DOC + "\n")

    rc = boundary_guard.main(boundary_root=store, repo=repo)
    out = capsys.readouterr().out

    assert rc == 1
    assert "one.md" in out and "two.md" in out, (
        f"only one copy of the leak was reported:\n{out}")


# ── 5. the candidate cannot prove itself ─────────────────────────────────────

def test_a_staged_candidate_is_not_evidence_of_its_own_publicity(
        store, repo, capsys):
    """Staging writes the blob into `.git/objects` and into the index.

    Neither is a publication event: `git add` is local, unshared and reversible.
    The baseline reads tip trees of accepted refs, so the index is excluded by
    construction — this pins that it stays excluded through the real gate.
    """
    leak = "notes\n\n" + PRIVATE_DOC + "\n"
    _stage(repo, "staged-leak.md", leak)
    # The blob genuinely exists in the object database at this point.
    oid = _git(repo, "rev-parse", ":staged-leak.md")
    assert _git(repo, "cat-file", "-t", oid) == "blob"

    rc = boundary_guard.main(boundary_root=store, repo=repo)
    assert rc == 1, "the candidate proved its own bytes public by being staged"
    assert "staged-leak.md" in capsys.readouterr().out


def test_a_leak_on_a_scratch_branch_is_not_accepted_sink_evidence(
        store, repo, capsys):
    """Committed is not the same as committed to accepted history."""
    _git(repo, "checkout", "-q", "-b", "scratch")
    _stage(repo, "scratch-leak.md", "notes\n\n" + PRIVATE_DOC + "\n")
    _git(repo, "commit", "-qm", "on a throwaway branch")
    _git(repo, "checkout", "-q", "main")

    _stage(repo, "now-for-real.md", "notes\n\n" + PRIVATE_DOC + "\n")
    rc = boundary_guard.main(boundary_root=store, repo=repo)
    assert rc == 1, "a scratch branch laundered protected material into the sink"


# ── 6. foreign repositories grant nothing ────────────────────────────────────

def test_a_foreign_repository_grants_no_sink_exemption(store, tmp_path, monkeypatch,
                                                       capsys):
    """The baseline is scoped to the repository this gate protects.

    `git init && git commit` in a scratch directory is not a publication event.
    If the baseline followed whatever repository the gate happened to be pointed
    at, anyone who can run git could manufacture sink evidence.
    """
    protected = _new_repo(tmp_path / "protected")     # nothing published here
    (protected / "README.md").write_text("unrelated\n", encoding="utf-8")
    _git(protected, "add", "README.md")
    _git(protected, "commit", "-qm", "init")
    monkeypatch.setenv("ZURVAN_PROTECTED_REPO", str(protected.resolve()))

    foreign = _new_repo(tmp_path / "foreign")
    (foreign / "public-doc.md").write_text(PUBLIC_PREAMBLE, encoding="utf-8")
    _git(foreign, "add", "public-doc.md")
    _git(foreign, "commit", "-qm", "published over here, allegedly")

    _stage(foreign, "public-doc.md", PUBLIC_PREAMBLE + BENIGN_EDIT)
    rc = boundary_guard.main(boundary_root=store, repo=foreign)
    assert rc == 1, (
        "a foreign repository's history granted a sink exemption; "
        "any scratch repo would then be a laundering primitive")


# ── 7. one baseline, captured before the batch, order-independent ────────────

def test_the_gate_captures_the_baseline_once_for_the_whole_staged_batch(
        store, repo, monkeypatch):
    from scripts.boundary import sink as sink_mod

    calls = []
    real = sink_mod.capture_baseline
    monkeypatch.setattr(sink_mod, "capture_baseline",
                        lambda *a, **kw: (calls.append(a), real(*a, **kw))[1])

    for i in range(6):
        _stage(repo, f"page-{i}.md", f"page {i}\n" * 30)
    boundary_guard.main(boundary_root=store, repo=repo)

    assert len(calls) == 1, (
        f"the baseline was reconstructed {len(calls)} times for one commit")


def test_candidate_order_does_not_change_any_gate_verdict(store, repo, monkeypatch):
    _stage(repo, "a-clean.md", "ordinary prose\n" * 20)
    _stage(repo, "b-leak.md", "notes\n\n" + PRIVATE_DOC + "\n")
    _stage(repo, "c-public.md", PUBLIC_PREAMBLE + BENIGN_EDIT)
    _stage(repo, "d-leak.md", "notes\n\n" + OTHER_PRIVATE + "\n")

    baseline = boundary_guard.gate_baseline()
    forward = gitscan.scan_staged(store, repo, baseline=baseline)

    real = gitscan.staged_blobs
    monkeypatch.setattr(gitscan, "staged_blobs", lambda r: list(reversed(real(r))))
    reverse = gitscan.scan_staged(store, repo, baseline=baseline)

    def shape(rows):
        return sorted((p, v.blocked, tuple(sorted(m.match_id for m in v.matches)))
                      for p, _c, v in rows)

    assert shape(forward) == shape(reverse)
    assert forward, "fixture blocked nothing; the property is not being exercised"


# ── the invariant that was missing ───────────────────────────────────────────

def test_production_gate_verdicts_equal_evaluation_scanner_verdicts(store, repo):
    """`production_commit_gate_verdicts == evaluation_scanner_verdicts`.

    Same candidates, same frozen baseline, both paths. This is the assertion
    whose absence let the gate and the measured model drift apart while every
    lower-level suite stayed green.
    """
    _stage(repo, "a-clean.md", "ordinary engineering prose\n" * 20)
    _stage(repo, "b-public-edit.md", PUBLIC_PREAMBLE + BENIGN_EDIT)
    _stage(repo, "c-leak.md", "notes\n\n" + PRIVATE_DOC + "\n")
    _stage(repo, "d-mixed.md", PUBLIC_PREAMBLE + "\n" + OTHER_PRIVATE + "\n")

    baseline = boundary_guard.gate_baseline()

    production = {path: (v.blocked, tuple(sorted(m.match_id for m in v.matches)))
                  for path, _c, v in gitscan.scan_staged(store, repo, baseline=baseline)}

    evaluation = {}
    for path, _change, data in gitscan.staged_blobs(repo):
        verdict = boundary.scan_bytes(store, data, gitscan.sinks_for(path),
                                      baseline=baseline)
        if verdict.blocked:
            evaluation[path] = (verdict.blocked,
                                tuple(sorted(m.match_id for m in verdict.matches)))

    assert production == evaluation
    assert "c-leak.md" in production and "d-mixed.md" in production
    assert "b-public-edit.md" not in production
    assert "a-clean.md" not in production


def test_the_gate_supplies_a_baseline_rather_than_none(store, repo, monkeypatch):
    """Pinned at the seam that actually broke: the argument the caller passes.

    Every contract below this line held while the caller passed `baseline=None`,
    so the contracts were not the problem — nothing asserted on the call itself.
    """
    seen = {}
    real = gitscan.scan_staged

    def recording(boundary_root, repo=None, baseline=None):
        seen["baseline"] = baseline
        return real(boundary_root, repo, baseline=baseline)

    monkeypatch.setattr(gitscan, "scan_staged", recording)

    _stage(repo, "anything.md", "content\n" * 20)
    boundary_guard.main(boundary_root=store, repo=repo)

    assert seen.get("baseline") is not None, (
        "the production gate still scans with baseline=None")
    assert seen["baseline"].identity["repo"], "the baseline names no repository"


def test_the_gate_never_accedes_what_it_scanned(store, repo):
    """The baseline is transient. A gate that accedes declassifies its own find."""
    from contextlib import closing

    from scripts.boundary import store as bstore

    with closing(bstore.open_db(store)) as conn:
        before = conn.execute("SELECT count(*) FROM public_origins").fetchone()[0]
    _stage(repo, "leak.md", "notes\n\n" + PRIVATE_DOC + "\n")
    _stage(repo, "public.md", PUBLIC_PREAMBLE + BENIGN_EDIT)
    boundary_guard.main(boundary_root=store, repo=repo)
    with closing(bstore.open_db(store)) as conn:
        after = conn.execute("SELECT count(*) FROM public_origins").fetchone()[0]

    assert after == before, "the commit gate wrote into the public corpus"


def test_a_missing_protected_repository_yields_no_exemptions(
        store, tmp_path, monkeypatch, capsys):
    """Fail closed: no accepted history means no sink evidence, not a free pass."""
    monkeypatch.setenv("ZURVAN_PROTECTED_REPO", str(tmp_path / "nowhere"))
    work = _new_repo(tmp_path / "work")
    (work / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "seed.md")
    _git(work, "commit", "-qm", "seed")

    _stage(work, "public-doc.md", PUBLIC_PREAMBLE + BENIGN_EDIT)
    rc = boundary_guard.main(boundary_root=store, repo=work)
    assert rc == 1, "a missing protected repository was treated as 'everything public'"
