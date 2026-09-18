"""A matched span is not a NEW disclosure if those bytes were already in the sink.

The v2 diagnostic run blocked four benign artefacts. None was detector
overreach: each shared a span with a protected document, and each of those spans
was **already committed to this repository's canonical branch**. Re-emitting
bytes that are already reachable from `refs/heads/main` cannot disclose anything
that was not disclosed when they were committed.

Three candidate explanations were measured and rejected before landing here —
byte entropy (inverted: boilerplate scored HIGHER than genuine protected prose),
protected-origin frequency (each span was in 1 of 300 origins), and raising `T`
(which would fix neither). The remaining explanation is missing scan-side
provenance, so that is what these tests pin.

**The invariant.** A matched span is exempt when those exact bytes were present
in the accepted protected-repository sink **before the candidate operation
began**.

**Match-level, not file-level.** A changed file carries old public spans and new
private ones in the same blob. Exempting the whole candidate because part of it
is public would make "append a secret to a public file" a bypass.

**The candidate may never prove itself.** Publicity evidence comes from a
baseline captured beforehand, from accepted refs only — never the working tree,
the index, the candidate commit, a stash, a scratch ref or a foreign repository.
That is the same anti-laundering rule the ingestion provenance layer already
enforces, applied to the other side of the boundary.

**The baseline is transient, not an accession.** It is recomputed per operation
and never written into `public_origins`. Global Constraint 7 forbids seeding the
trusted public corpus from "everything currently tracked", because a leak in the
tree would be blessed permanently. A per-operation baseline has the opposite
property: remove the text from the canonical branch and the exemption vanishes
with it.
"""

import subprocess
from pathlib import Path

import pytest

from scripts import boundary
from scripts.boundary import store as bstore
from scripts.boundary.sink import capture_baseline

PRIVATE_DOC = (
    "The quarterly containment review concluded that the residue threshold must "
    "be recalibrated before the next audit cycle begins in earnest, and that the "
    "reviewers should be rotated."
)
PUBLIC_PREAMBLE = (
    "# Project template\n\n"
    "> For agentic workers: this preamble is shared by every generated plan in "
    "the repository and carries no project-specific knowledge whatsoever.\n\n"
    "## Section\n\nOrdinary published documentation follows here.\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "public-doc.md").write_text(PUBLIC_PREAMBLE, encoding="utf-8")
    _git(r, "add", "public-doc.md")
    _git(r, "commit", "-qm", "publish the template")
    monkeypatch.setenv("ZURVAN_PROTECTED_REPO", str(r.resolve()))
    return r


@pytest.fixture
def protected_store(tmp_path):
    """A store whose protected corpus contains BOTH the private document and,
    like the real corpus, a copy of the public preamble."""
    root = tmp_path / "boundary"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "private.md").write_text(
        PUBLIC_PREAMBLE + "\n" + PRIVATE_DOC, encoding="utf-8")
    boundary.build(root)
    return root


# ── 1. an exact blob already in the baseline sink ────────────────────────────

def test_an_exact_baseline_blob_is_not_a_new_disclosure(repo, protected_store):
    baseline = capture_baseline(repo)
    candidate = (repo / "public-doc.md").read_bytes()

    assert baseline.contains_blob(candidate), (
        "the unchanged file's exact blob is already in the accepted sink"
    )
    verdict = boundary.scan_bytes(protected_store, candidate,
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)
    assert not verdict.blocked


# ── 2. a one-byte benign edit: new blob, old spans still public ──────────────

def test_a_benign_edit_keeps_old_public_spans_exempt_and_scans_the_rest(
    repo, protected_store
):
    baseline = capture_baseline(repo)
    # A real edit: append to the file as it stands, byte for byte.
    edited = (PUBLIC_PREAMBLE + "One more ordinary published sentence.\n").encode()

    assert not baseline.contains_blob(edited), "the edited blob is NOT baseline-public"

    verdict = boundary.scan_bytes(protected_store, edited,
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert not verdict.blocked, (
        "the shared preamble is already public; the new sentence is benign"
    )


def test_a_span_straddling_the_public_boundary_is_not_exempt(repo, protected_store):
    """STATED BOUND, found by this suite rather than reasoned about.

    The invariant is EXACT bytes. A matched span that reaches one byte past the
    public content — here an edit that introduces a blank line, so the span is
    the preamble plus an extra newline — is not byte-identical to anything in
    the sink, and is therefore not exempt.

    The principled fix is residual subtraction: remove the public parts of a
    span and block only if what remains still reaches T. D3 makes the same
    promise at span level and has the same edge (its partial-overlap test pins
    the residual). That is a larger change than this rule, deliberately not
    smuggled in with it, and it is the reason a benign reformat can still block.
    """
    baseline = capture_baseline(repo)
    reformatted = (PUBLIC_PREAMBLE + "\nOne more ordinary published sentence.\n").encode()

    verdict = boundary.scan_bytes(protected_store, reformatted,
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, (
        "documented limitation: the span extends past the public bytes, so the "
        "exact-byte rule does not fire"
    )


# ── 3. the same edit plus a genuine private span still blocks ───────────────

def test_a_newly_introduced_private_span_still_blocks_inside_a_public_file(
    repo, protected_store
):
    """The bypass this rule must not open: append a secret to a public file."""
    baseline = capture_baseline(repo)
    candidate = (PUBLIC_PREAMBLE + "\n" + PRIVATE_DOC + "\n").encode()

    verdict = boundary.scan_bytes(protected_store, candidate,
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, (
        "public spans are exempt one by one; a NEW protected span still blocks"
    )


# ── 4. staging is not publicity ──────────────────────────────────────────────

def test_staging_the_private_span_first_does_not_make_it_public(repo, protected_store):
    leak = repo / "leak.md"
    leak.write_text(PRIVATE_DOC, encoding="utf-8")
    _git(repo, "add", "leak.md")           # staged, not committed

    baseline = capture_baseline(repo)
    verdict = boundary.scan_bytes(protected_store, PRIVATE_DOC.encode(),
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, "the index is not the sink"


# ── 5. the candidate may not prove itself ───────────────────────────────────

def test_committing_the_candidate_first_does_not_let_it_prove_itself(
    repo, protected_store
):
    """Baseline BEFORE the candidate operation is the whole guarantee.

    If publicity were evaluated against the current refs, a leak would become
    self-justifying the instant it was committed — the candidate proving its own
    bytes public. The baseline is captured first, so it cannot contain them.
    """
    baseline = capture_baseline(repo)          # captured BEFORE

    leak = repo / "leak.md"
    leak.write_text(PRIVATE_DOC, encoding="utf-8")
    _git(repo, "add", "leak.md")
    _git(repo, "commit", "-qm", "the candidate commit itself")

    verdict = boundary.scan_bytes(protected_store, PRIVATE_DOC.encode(),
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, "a candidate must never be its own publicity evidence"


def test_a_scratch_ref_is_not_the_sink(repo, protected_store):
    leak = repo / "leak.md"
    leak.write_text(PRIVATE_DOC, encoding="utf-8")
    _git(repo, "checkout", "-q", "-b", "wip/leak")
    _git(repo, "add", "leak.md")
    _git(repo, "commit", "-qm", "on a scratch branch")
    _git(repo, "checkout", "-q", "main")

    baseline = capture_baseline(repo)
    verdict = boundary.scan_bytes(protected_store, PRIVATE_DOC.encode(),
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, "only accepted refs count"


def test_the_stash_is_not_the_sink(repo, protected_store):
    leak = repo / "leak.md"
    leak.write_text(PRIVATE_DOC, encoding="utf-8")
    _git(repo, "add", "leak.md")
    _git(repo, "stash", "-q")

    baseline = capture_baseline(repo)
    verdict = boundary.scan_bytes(protected_store, PRIVATE_DOC.encode(),
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked


# ── 6. a foreign repository is not the sink ─────────────────────────────────

def test_a_foreign_repository_commit_is_not_publicity_evidence(
    repo, protected_store, tmp_path
):
    other = tmp_path / "sibling"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "T")
    (other / "leak.md").write_text(PRIVATE_DOC, encoding="utf-8")
    _git(other, "add", "leak.md")
    _git(other, "commit", "-qm", "committed elsewhere")

    baseline = capture_baseline(repo)
    verdict = boundary.scan_bytes(protected_store, PRIVATE_DOC.encode(),
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, "another repo's sink is not this one's"


# ── 7. a span whose public file was later deleted ───────────────────────────

def test_a_deleted_public_file_stops_being_publicity_evidence(repo, protected_store):
    """STATED POLICY, deliberately conservative and deliberately different from
    the accession rule.

    A recorded D3 accession survives a history rewrite: it is evidence of what
    was true when it was made. This baseline is not an accession — it is a
    question asked fresh on every operation ("is this text in the sink NOW?"),
    so content removed from the canonical branch stops answering yes.

    The two rules differ because their failure modes differ. A permanent
    accession that survived deletion would bless a leak forever, which Global
    Constraint 7 exists to prevent. A transient baseline that forgets deleted
    content merely blocks something that was once public — recoverable by an
    explicit, recorded accession, which is the auditable path.
    """
    (repo / "public-doc.md").unlink()
    _git(repo, "rm", "-q", "--cached", "public-doc.md")
    _git(repo, "commit", "-qm", "remove the template")

    baseline = capture_baseline(repo)
    candidate = (PUBLIC_PREAMBLE + "\nnew line\n").encode()

    verdict = boundary.scan_bytes(protected_store, candidate,
                                  sink="PUBLIC_ARTIFACT", baseline=baseline)

    assert verdict.blocked, (
        "content deleted from the canonical branch is no longer in the sink"
    )


# ── the rule must be general ────────────────────────────────────────────────

def test_no_hard_coded_boilerplate_list():
    """A list of known-annoying strings is memorisation, not a rule."""
    import inspect

    from scripts.boundary import sink

    source = inspect.getsource(sink).lower()
    for forbidden in ("viewport", "sub-skill", "superpower", "width=device-width"):
        assert forbidden not in source, (
            f"{forbidden!r} appears in sink.py — the exemption must be general "
            "exact-byte sink provenance, never a special case for the artefacts "
            "that happened to fail the v2 diagnostic run"
        )


def test_without_a_baseline_the_scan_is_unchanged(protected_store):
    """The parameter is additive: existing callers keep their behaviour."""
    verdict = boundary.scan_bytes(protected_store, PRIVATE_DOC.encode(),
                                  sink="PUBLIC_ARTIFACT")
    assert verdict.blocked


def test_the_baseline_records_what_it_was_captured_from(repo):
    baseline = capture_baseline(repo)

    assert baseline.identity["refs"], "the accepted refs must be recorded"
    assert baseline.identity["blobs"] >= 1
    assert baseline.identity["commits"], "the baseline commit must be recorded"
    assert all(len(c) == 40 for c in baseline.identity["commits"].values())


# ── capturing the baseline is a per-commit cost, so it must not be per-blob ───

def test_the_baseline_reads_every_blob_without_a_subprocess_per_blob(
        tmp_path, monkeypatch):
    """One `git cat-file` per blob made the gate cost ~1s before it scanned.

    Measured on the benchmark fixture: 0.92 s to capture 100 sink blobs, and
    3.16 s for this repository's 399 — a fixed cost paid on every commit,
    dwarfing the 0.075 s it takes to scan a single staged file. The number of
    git invocations must scale with the number of REFS, not with the size of
    the sink.
    """
    import subprocess as sp

    def repo_with(n_files: int, name: str) -> Path:
        r = tmp_path / name
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        _git(r, "config", "user.email", "t@example.com")
        _git(r, "config", "user.name", "T")
        for i in range(n_files):
            (r / f"f{i:03d}.txt").write_text(f"content number {i}\n", encoding="utf-8")
        _git(r, "add", "-A")
        _git(r, "commit", "-qm", "files")
        return r

    small, large = repo_with(4, "small"), repo_with(40, "large")

    def count(repo) -> int:
        calls = []
        real_run = sp.run
        monkeypatch.setattr(sp, "run",
                            lambda *a, **kw: (calls.append(a[0]), real_run(*a, **kw))[1])
        baseline = capture_baseline(repo)
        monkeypatch.undo()
        return len(calls), baseline

    small_calls, small_baseline = count(small)
    large_calls, large_baseline = count(large)

    assert small_baseline.identity["blobs"] == 4
    assert large_baseline.identity["blobs"] == 40
    # The constant itself is not the contract — INDEPENDENCE from sink size is.
    # Asserting a magic number here would just be a second guess to maintain.
    assert small_calls == large_calls, (
        f"{small_calls} git invocations for 4 blobs but {large_calls} for 40: "
        "capture cost scales with the sink, and it is paid on every commit")


def test_the_baseline_reproduces_awkward_blob_bytes_exactly(tmp_path):
    """Batch reading is only safe if it is byte-exact.

    `git cat-file --batch` frames each object by a declared length, so anything
    that parses on newlines corrupts blobs containing NULs, CRLF, or no trailing
    newline. Exact bytes are the whole point: the baseline decides whether a
    span is already public by `in`, and a single wrong byte flips that.
    """
    r = tmp_path / "awkward"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    cases = {
        "no-trailing-newline.txt": b"ends without a newline",
        "embedded-nul.bin": b"before\x00after\x00\x00end",
        "crlf.txt": b"line one\r\nline two\r\n",
        "empty.txt": b"",
        "binaryish.bin": bytes(range(256)) * 3,
        "newline-heavy.txt": b"\n\n\n\n" + b"text\n" * 50,
    }
    for name, data in cases.items():
        (r / name).write_bytes(data)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "awkward bytes")

    baseline = capture_baseline(r)
    held = set(baseline.blobs.values())
    for name, data in cases.items():
        if data == b"":
            continue                # git stores it; containment of b"" is never asked
        assert data in held, f"{name} did not survive the batch read intact"
        assert baseline.contains_blob(data), f"{name} is not recognised as sink-public"
