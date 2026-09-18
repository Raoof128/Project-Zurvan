"""One scan session per commit-gate invocation, and what must NOT change.

The gate opened the store, validated it, loaded every origin and re-read every
protected blob once PER STAGED FILE. Measured on a 150-document fixture at 100
staged files: 100 store opens, 100 validations, 4,056 blob reads, 4.13 s.
None of that work depends on which candidate is being scanned.

Batching it is a performance change, so the tests that matter are the ones that
pin what a performance change must not do.

**The batch observes ONE immutable pre-candidate view.** Protected state, public
accessions and the accepted sink baseline are read once, before the first
candidate, and never re-read inside the batch.

**Intra-batch anti-laundering.** The baseline must never be updated with
candidates as they are scanned. If it were, committing a leak alongside a file
containing the same span would let the first candidate make the second one
"already public" — a laundering primitive created by an optimisation, which is
exactly how optimisations become vulnerabilities.

**Per-candidate verdicts stay per-candidate.** Match de-duplication is scoped to
one candidate. Hoisting the `seen` set to the session would silently drop a real
leak from the second file that shares a span with the first.
"""

import hashlib
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts import boundary
from scripts.boundary import gitscan, store as bstore
from scripts.boundary.session import ScanSession, batch_scan
from scripts.boundary.sink import capture_baseline

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
    """Protected corpus holding two private documents AND the public preamble.

    The preamble is there for the same reason it is there in the real corpus:
    the protected material was ingested from documents that quote shared
    boilerplate, so a candidate reusing that boilerplate anchors against a
    protected origin and only sink provenance tells the two cases apart.
    """
    root = tmp_path / "boundary"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "private-a.md").write_text(
        PUBLIC_PREAMBLE + "\n" + PRIVATE_DOC, encoding="utf-8")
    (root / "sources" / "private-b.md").write_text(OTHER_PRIVATE, encoding="utf-8")
    boundary.build(root)
    return root


def _verdicts(results):
    """(key, blocked, sorted match ids) — the observable part of a result set."""
    return [(key, v.blocked, tuple(sorted(m.match_id for m in v.matches)))
            for key, v in results]


# ── 1. batch equivalence ──────────────────────────────────────────────────────

def test_batch_scan_matches_isolated_scans_against_the_same_baseline(
        repo, protected_store):
    """N candidates through one session == N independent scans. Same baseline."""
    baseline = capture_baseline(repo)
    items = [
        ("clean.md", b"Nothing in here resembles anything protected at all.\n",
         ["PUBLIC_ARTIFACT"]),
        ("leak.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"]),
        ("boiler.md", PUBLIC_PREAMBLE.encode(), ["PUBLIC_ARTIFACT"]),
        ("both.md", (PUBLIC_PREAMBLE + "\n" + OTHER_PRIVATE).encode(),
         ["PUBLIC_ARTIFACT", "RETRIEVAL_CORPUS"]),
    ]

    batched = batch_scan(protected_store, items, baseline=baseline)
    isolated = [(key, boundary.scan_bytes(protected_store, data, sink,
                                          baseline=baseline))
                for key, data, sink in items]

    assert _verdicts(batched) == _verdicts(isolated)


def test_batch_scan_with_no_baseline_matches_isolated_scans(repo, protected_store):
    """Equivalence must not depend on a baseline being supplied."""
    items = [("leak.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"]),
             ("boiler.md", PUBLIC_PREAMBLE.encode(), ["PUBLIC_ARTIFACT"])]
    batched = batch_scan(protected_store, items)
    isolated = [(key, boundary.scan_bytes(protected_store, data, sink))
                for key, data, sink in items]
    assert _verdicts(batched) == _verdicts(isolated)


# ── 2. candidate ordering ─────────────────────────────────────────────────────

def test_reversing_candidate_order_changes_no_verdict(repo, protected_store):
    """A batch is a set of independent questions, not a sequence.

    The failure this catches: hoisting the per-candidate `seen` set into the
    session. The second file sharing a span with the first would then come back
    clean, and reversing the order would move the block from one file to the
    other.
    """
    baseline = capture_baseline(repo)
    items = [("a.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"]),
             ("b.md", (PRIVATE_DOC + "\ntail\n").encode(), ["PUBLIC_ARTIFACT"]),
             ("c.md", OTHER_PRIVATE.encode(), ["PUBLIC_ARTIFACT"])]

    forward = dict((k, (v.blocked, len(v.matches)))
                   for k, v in batch_scan(protected_store, items, baseline=baseline))
    reverse = dict((k, (v.blocked, len(v.matches)))
                   for k, v in batch_scan(protected_store, list(reversed(items)),
                                          baseline=baseline))

    assert forward == reverse
    assert all(blocked for blocked, _ in forward.values()), (
        "fixture is not exercising the property: nothing was blocked either way")


# ── 3. intra-batch laundering ─────────────────────────────────────────────────

def test_an_earlier_candidate_cannot_make_a_later_one_already_public(
        repo, protected_store):
    """Candidate A must not become evidence that candidate B is public.

    This is the failure mode a naive batch optimisation introduces: carry the
    baseline forward, add each candidate to it as it is scanned, and a commit
    containing the leak twice blocks once.
    """
    baseline = capture_baseline(repo)
    items = [("first.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"]),
             ("second.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"])]

    results = dict(batch_scan(protected_store, items, baseline=baseline))

    assert results["first.md"].blocked, "the leak itself must block"
    assert results["second.md"].blocked, (
        "the second copy came back clean: the first candidate was admitted as "
        "sink evidence inside the batch")


def test_the_session_baseline_is_not_mutated_by_scanning(repo, protected_store):
    """The frozen view is frozen — pinned structurally, not just behaviourally."""
    baseline = capture_baseline(repo)
    before = dict(baseline.blobs)
    with ScanSession(protected_store, baseline=baseline) as session:
        session.scan(PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"])
        session.scan(OTHER_PRIVATE.encode(), ["PUBLIC_ARTIFACT"])
    assert baseline.blobs == before


# ── 4. mixed public / private candidate ───────────────────────────────────────

def test_baseline_public_spans_stay_exempt_while_new_protected_spans_block(
        repo, protected_store):
    """One candidate, both halves. Exemption is per match, never per file."""
    baseline = capture_baseline(repo)
    candidate = (PUBLIC_PREAMBLE + "\n" + OTHER_PRIVATE).encode()

    with ScanSession(protected_store, baseline=baseline) as session:
        mixed = session.scan(candidate, ["PUBLIC_ARTIFACT"])
        boiler_only = session.scan(PUBLIC_PREAMBLE.encode(), ["PUBLIC_ARTIFACT"])

    assert not boiler_only.blocked, (
        "the preamble is already in the sink; re-emitting it discloses nothing")
    assert mixed.blocked, (
        "appending private content to a public file must still block, or "
        "'append a secret to a published page' is a bypass")


# ── 5. one blocked candidate does not abort the rest ──────────────────────────

def test_a_blocked_candidate_does_not_stop_later_candidates_being_analysed(
        repo, protected_store):
    """No fail-fast: the gate reports every offending path in one run.

    A gate that stops at the first block turns one commit into N commits of
    whack-a-mole, and the author never sees the shape of what they staged.
    """
    baseline = capture_baseline(repo)
    items = [("blocked-first.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"]),
             ("clean-middle.md", b"ordinary unrelated prose, nothing to see\n",
              ["PUBLIC_ARTIFACT"]),
             ("blocked-last.md", OTHER_PRIVATE.encode(), ["PUBLIC_ARTIFACT"])]

    results = dict(batch_scan(protected_store, items, baseline=baseline))

    assert len(results) == 3, "analysis stopped early"
    assert results["blocked-first.md"].blocked
    assert not results["clean-middle.md"].blocked
    assert results["blocked-last.md"].blocked, (
        "the last candidate was never scanned after an earlier block")


# ── 6. snapshot consistency ───────────────────────────────────────────────────

def test_a_protected_tree_mutation_after_the_session_opens_changes_no_verdict(
        repo, protected_store):
    """The batch answers against the state it validated at the start.

    Isolated scanning re-validates per candidate, so a mid-commit edit to the
    protected tree raises StaleStateError partway through and the same staged
    set gets two different answers depending on where the edit landed. A session
    gives the whole batch one consistent view; the NEXT gate invocation sees the
    change and refuses until a rebuild.
    """
    candidate = PRIVATE_DOC.encode()
    with ScanSession(protected_store) as session:
        before = session.scan(candidate, ["PUBLIC_ARTIFACT"])
        (protected_store / "sources" / "injected.md").write_text(
            "Material added mid-batch.\n" * 20, encoding="utf-8")
        after = session.scan(candidate, ["PUBLIC_ARTIFACT"])

    assert before.blocked == after.blocked
    assert ([m.match_id for m in before.matches]
            == [m.match_id for m in after.matches])

    # ...and the staleness is not swallowed: the next session refuses.
    with pytest.raises(bstore.StaleStateError):
        ScanSession(protected_store)


# ── 7. resource cleanup ───────────────────────────────────────────────────────

def test_the_session_closes_its_connection_on_success(protected_store):
    with ScanSession(protected_store) as session:
        session.scan(b"anything at all\n", ["PUBLIC_ARTIFACT"])
        conn = session._conn
        conn.execute("SELECT 1")                     # open inside the block
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_the_session_closes_its_connection_when_the_body_raises(protected_store):
    session = None
    with pytest.raises(ValueError):
        with ScanSession(protected_store) as s:
            session = s
            raise ValueError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        session._conn.execute("SELECT 1")


def test_closing_twice_is_not_an_error(protected_store):
    session = ScanSession(protected_store)
    session.close()
    session.close()


def test_scanning_a_closed_session_is_refused(protected_store):
    session = ScanSession(protected_store)
    session.close()
    with pytest.raises(RuntimeError):
        session.scan(b"anything\n", ["PUBLIC_ARTIFACT"])


# ── 8. the commit gate mutates nothing ────────────────────────────────────────

def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in Path(root).rglob("*") if x.is_file()):
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def test_a_batch_scan_leaves_the_boundary_store_byte_identical(
        repo, protected_store):
    """Read-only with respect to the store, the accessions and the baseline.

    A gate that writes while it reads cannot be run concurrently with anything,
    and a gate that accedes what it scans declassifies the leak it just found.
    """
    before = _tree_digest(protected_store)
    baseline = capture_baseline(repo)
    batch_scan(protected_store,
               [("leak.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"]),
                ("clean.md", b"unrelated\n" * 30, ["PUBLIC_ARTIFACT"])],
               baseline=baseline)
    assert _tree_digest(protected_store) == before


def test_a_blocked_span_is_never_acceded_by_scanning_it(repo, protected_store):
    """The scan must not turn a discovered match into a public origin."""
    from contextlib import closing
    with closing(bstore.open_db(protected_store)) as conn:
        before = conn.execute("SELECT count(*) FROM public_origins").fetchone()[0]
    batch_scan(protected_store,
               [("leak.md", PRIVATE_DOC.encode(), ["PUBLIC_ARTIFACT"])],
               baseline=capture_baseline(repo))
    with closing(bstore.open_db(protected_store)) as conn:
        after = conn.execute("SELECT count(*) FROM public_origins").fetchone()[0]
    assert after == before


# ── the performance invariant itself ──────────────────────────────────────────

def test_a_batch_opens_the_store_once_regardless_of_candidate_count(
        protected_store, monkeypatch):
    """The whole point, pinned so a later refactor cannot quietly undo it."""
    opens = []
    real = bstore.open_db
    monkeypatch.setattr(bstore, "open_db", lambda root: (opens.append(root), real(root))[1])

    items = [(f"f{i}.md", f"candidate number {i}\n".encode() * 20,
              ["PUBLIC_ARTIFACT"]) for i in range(25)]
    batch_scan(protected_store, items)

    assert len(opens) == 1, f"opened the store {len(opens)} times for 25 candidates"


def test_a_protected_blob_is_read_once_per_batch_not_once_per_candidate(
        protected_store, monkeypatch):
    """Blob reads dominated the old cost model: 4,056 for 100 staged files."""
    reads = []
    real = bstore.get_blob
    monkeypatch.setattr(bstore, "get_blob",
                        lambda root, d: (reads.append(d), real(root, d))[1])

    leak = PRIVATE_DOC.encode()
    batch_scan(protected_store, [(f"f{i}.md", leak, ["PUBLIC_ARTIFACT"])
                                 for i in range(10)])

    assert len(reads) == len(set(reads)), (
        f"{len(reads)} blob reads for {len(set(reads))} distinct blobs: the "
        "content-addressed cache is not shared across candidates")


def test_the_commit_gate_uses_one_session_for_every_staged_file(
        tmp_path, protected_store, monkeypatch):
    """`scan_staged` is the gate's real entry point, so pin it there too."""
    r = tmp_path / "gate-repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(r, "add", "seed.md")
    _git(r, "commit", "-qm", "seed")
    for i in range(12):
        (r / f"page-{i}.md").write_text(f"page {i}\n" * 40, encoding="utf-8")
    _git(r, "add", "-A")

    opens = []
    real = bstore.open_db
    monkeypatch.setattr(bstore, "open_db", lambda root: (opens.append(root), real(root))[1])

    gitscan.scan_staged(protected_store, r)

    assert len(opens) == 1, f"gate opened the store {len(opens)} times for 12 files"
