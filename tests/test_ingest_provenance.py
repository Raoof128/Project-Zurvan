"""Origin class is recorded when material is acceded, from evidence available then.

`ingest.py` recorded `title`, `hash`, `figures`, `extraction_status` and
`ingested_at` — nothing about where the bytes came from. A public document
ingested into `wiki/sources/` therefore became PRIVATE **by location alone**,
after which Boundary correctly refused to let the original public file be
committed. That provenance inversion is what makes the false-positive gate
unmeasurable: 11 of 11 blocked artefacts read as ingestion round-trips, and the
metric cannot separate a coincidence from a document that was public before
Zurvan ever saw it (spec §8, recorded invalid-as-measured).

Three classes, because "public" is not one thing:

    PRIVATE  ->  PUBLIC_DECLARED  ->  PUBLIC_VERIFIED

* **PUBLIC_VERIFIED, and only it, earns a D3 exemption.** It requires the exact
  bytes to be REACHABLE FROM A REF in the repository this gate protects. An
  earlier version of this module asked `git cat-file -e` instead — object
  existence, which `git hash-object -w` satisfies with no commit, no ref and no
  intent. That would have let anyone who can write a file manufacture public
  provenance for its contents; the attack is pinned below.
* **PUBLIC_DECLARED is remembered, never honoured.** A staged-only file, a
  sibling repository's commit, a URL nobody fetched: real information, recorded
  for a later audit to promote, but an unverified claim that grants an exemption
  IS the laundering primitive.
* **Never retroactively.** Absence of a recorded class is PRIVATE, forever.
  Seeding the public corpus from "everything currently tracked" is the
  laundering hole (Global Constraint 7): if the tree already holds an accidental
  leak, blessing it makes yesterday's mistake tomorrow's known-public origin.
"""

import subprocess
from pathlib import Path

import pytest

from scripts.provenance import (
    ORIGIN_PRIVATE,
    ORIGIN_PUBLIC_DECLARED,
    ORIGIN_PUBLIC_VERIFIED,
    classify_origin,
    may_accede,
    origin_class_of_page,
    parse_provenance,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git repository, standing in as the PROTECTED one.

    `ZURVAN_PROTECTED_REPO` is what makes the foreign-repository rule testable:
    evidence is verified only against the repo whose sink Boundary guards.
    """
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / ".gitignore").write_text("private/\n", encoding="utf-8")
    (r / "README.md").write_text("# Public\n\nThis is committed.\n", encoding="utf-8")
    _git(r, "add", ".gitignore", "README.md")
    _git(r, "commit", "-qm", "initial")
    (r / "private").mkdir()
    monkeypatch.setenv("ZURVAN_PROTECTED_REPO", str(r.resolve()))
    return r


# ── PUBLIC requires evidence that the bytes already reached the sink ──────────

def test_a_committed_file_is_public_with_its_blob_as_provenance(repo):
    origin_class, provenance = classify_origin(repo / "README.md")

    assert origin_class == ORIGIN_PUBLIC_VERIFIED
    parsed = parse_provenance(provenance)
    assert parsed["scheme"] == "git-commit"
    # The blob id identifies the evidence; the CLASS is what records that a
    # commit reaches it. Existence of the object alone proves nothing.
    assert parsed["value"] == _git(repo, "hash-object", "README.md")


def test_an_untracked_file_inside_the_repo_is_private(repo):
    draft = repo / "draft.md"
    draft.write_text("not committed yet\n", encoding="utf-8")

    origin_class, _ = classify_origin(draft)

    assert origin_class == ORIGIN_PRIVATE


def test_a_gitignored_file_is_private_however_deep_in_the_repo(repo):
    secret = repo / "private" / "statutory-declaration.md"
    secret.write_text("a real private document\n", encoding="utf-8")

    origin_class, _ = classify_origin(secret)

    assert origin_class == ORIGIN_PRIVATE


def test_a_file_outside_any_repository_is_private(tmp_path):
    loose = tmp_path / "elsewhere.md"
    loose.write_text("from a USB stick\n", encoding="utf-8")

    assert classify_origin(loose)[0] == ORIGIN_PRIVATE


def test_modified_tracked_content_is_private_until_those_bytes_are_committed(repo):
    """The claim is about BYTES, not about the path.

    A tracked file with uncommitted edits has a path that is "in git" and
    content that is not. Classifying by path would mark the edit public — and
    the edit is exactly the part that never reached the sink.
    """
    readme = repo / "README.md"
    readme.write_text("# Public\n\nThis is committed.\nAND THIS IS NOT.\n", encoding="utf-8")

    origin_class, _ = classify_origin(readme)

    assert origin_class == ORIGIN_PRIVATE, "uncommitted bytes are not public"


def test_a_side_branch_is_not_the_sink_but_the_canonical_branch_is(repo):
    """Inverted on purpose when the ref allowlist landed.

    This test previously asserted that a commit on ANY branch verified, because
    the check was `git log --all`. That is too broad: `--all` also covers
    `refs/stash`, `refs/original/*` and every scratch branch, so stashing a
    private file laundered it. Reaching the sink now means reaching the
    repository's canonical branch — which a merge does, and a side branch does
    not until it lands.
    """
    _git(repo, "checkout", "-q", "-b", "side")
    other = repo / "side-note.md"
    other.write_text("committed on a side branch\n", encoding="utf-8")
    _git(repo, "add", "side-note.md")
    _git(repo, "commit", "-qm", "side")
    _git(repo, "checkout", "-q", "main")
    other.write_text("committed on a side branch\n", encoding="utf-8")

    assert classify_origin(other)[0] != ORIGIN_PUBLIC_VERIFIED, "not yet on main"

    other.unlink()          # the merge would refuse to clobber an untracked copy
    _git(repo, "merge", "-q", "--no-edit", "side")

    assert classify_origin(other)[0] == ORIGIN_PUBLIC_VERIFIED, "merged to main"


# ── a declared URL is an assertion, and is recorded as one ───────────────────

def test_a_declared_public_url_is_recorded_as_declared_not_as_evidence(repo):
    draft = repo / "draft.md"
    draft.write_text("fetched from the web by hand\n", encoding="utf-8")

    origin_class, provenance = classify_origin(
        draft, declared_public_url="https://example.org/paper.pdf")

    assert origin_class == ORIGIN_PUBLIC_DECLARED
    parsed = parse_provenance(provenance)
    assert parsed["scheme"] == "url-declared", (
        "a human assertion must never be recorded in the same scheme as git "
        "evidence — an audit has to be able to tell them apart"
    )
    assert parsed["value"] == "https://example.org/paper.pdf"


def test_git_evidence_outranks_a_declared_url(repo):
    """Both present: record the verifiable one."""
    _, provenance = classify_origin(
        repo / "README.md", declared_public_url="https://example.org/whatever")

    assert parse_provenance(provenance)["scheme"] == "git-commit"


def test_an_empty_declared_url_is_refused(repo):
    draft = repo / "draft.md"
    draft.write_text("x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="provenance"):
        classify_origin(draft, declared_public_url="   ")


# ── absence is PRIVATE, forever ──────────────────────────────────────────────

def test_a_page_without_a_recorded_class_is_private(tmp_path):
    """Every one of the ~4,400 pages ingested before today is in this state."""
    page = tmp_path / "legacy.md"
    page.write_text(
        "---\ntitle: old.pdf\nhash: abc123\ningested_at: 2026-06-01T00:00:00\n---\n\n# old.pdf\n",
        encoding="utf-8",
    )

    assert origin_class_of_page(page) == ORIGIN_PRIVATE


def test_a_page_with_a_recorded_public_class_reads_back_as_public(tmp_path):
    page = tmp_path / "new.md"
    page.write_text(
        "---\ntitle: README.md\nhash: abc123\n"
        "origin_class: PUBLIC_VERIFIED\norigin_provenance: git-commit:deadbeef@repo\n---\n\n# README.md\n",
        encoding="utf-8",
    )

    assert origin_class_of_page(page) == ORIGIN_PUBLIC_VERIFIED


def test_an_unrecognised_class_is_private(tmp_path):
    """Fail closed on anything that is not exactly the two known values."""
    page = tmp_path / "weird.md"
    page.write_text(
        "---\norigin_class: PUBLIC-ISH\norigin_provenance: vibes\n---\n", encoding="utf-8")

    assert origin_class_of_page(page) == ORIGIN_PRIVATE


def test_public_without_provenance_is_private(tmp_path):
    """PUBLIC is a claim; a claim with no evidence recorded is not honoured."""
    page = tmp_path / "bare.md"
    page.write_text("---\norigin_class: PUBLIC_VERIFIED\n---\n", encoding="utf-8")

    assert origin_class_of_page(page) == ORIGIN_PRIVATE


def test_a_missing_page_is_private(tmp_path):
    assert origin_class_of_page(tmp_path / "nope.md") == ORIGIN_PRIVATE


# ── the laundering hole stays shut ───────────────────────────────────────────

def test_there_is_no_api_that_classifies_an_existing_page_from_the_tree():
    """Global Constraint 7, checked rather than trusted.

    The forbidden operation is "walk the repository, mark what is tracked as
    public". It is forbidden because a leak already sitting in the tree would be
    blessed permanently. `classify_origin` takes the file being acceded, one at
    a time, and records what was true at that moment; nothing may offer the bulk
    form.
    """
    import inspect

    from scripts import provenance

    source = inspect.getsource(provenance)
    for forbidden in ("rglob", "glob(", "walk("):
        assert forbidden not in source, (
            f"{forbidden!r} in provenance.py suggests a bulk tree scan — the "
            "public corpus is never seeded from 'everything currently tracked'"
        )


def test_classification_reads_the_bytes_it_is_given_not_the_path_on_disk(repo, tmp_path):
    """A copy of public bytes is public; a path that merely looks familiar is not.

    Guards the substitution attack: point the classifier at a private file that
    happens to live where a tracked one used to.
    """
    impostor = tmp_path / "README.md"          # same basename, different repo, private bytes
    impostor.write_text("# Public\n\nSECRET INSERTED HERE\n", encoding="utf-8")

    assert classify_origin(impostor)[0] == ORIGIN_PRIVATE


def test_evidence_from_a_different_repository_is_declared_never_verified(repo, tmp_path):
    """`git init && git add && git commit` must not cross the boundary.

    Bytes committed in a sibling project's checkout have reached THAT repo's
    sink, not the one this gate protects, and creating a scratch repository is
    not a publication event. Recorded as real evidence — the commit exists — but
    classed DECLARED, so it can never confer a D3 exemption.
    """
    other = tmp_path / "sibling"
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "T")
    doc = other / "their-doc.md"
    doc.write_text("committed in a different project\n", encoding="utf-8")
    _git(other, "add", "their-doc.md")
    _git(other, "commit", "-qm", "theirs")

    origin_class, provenance = classify_origin(doc)

    assert origin_class == ORIGIN_PUBLIC_DECLARED
    assert parse_provenance(provenance)["scheme"] == "git-commit", (
        "the evidence type is still a commit — what changed is whose repo it is"
    )
    assert not may_accede(origin_class), "a foreign repo must not exempt anything"


# ── the ingestion path records it, and accedes at the moment of entry ─────────

def _patch_roots(monkeypatch, tmp_path):
    from scripts import ingest as _ingest
    from scripts import wiki_merge as _wiki_merge
    monkeypatch.setattr(_wiki_merge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_ingest, "PROJECT_ROOT", tmp_path)


def _page_text(store_root: Path, filename: str) -> str:
    return (store_root / "wiki" / "sources" / filename).read_text(encoding="utf-8")


def test_ingesting_committed_bytes_records_public_with_git_evidence(
    repo, tmp_path, monkeypatch
):
    from scripts.ingest import create_source_page

    _patch_roots(monkeypatch, tmp_path / "store")
    filename = create_source_page(str(repo / "README.md"), "deadbeef", "# Public\n")

    page = _page_text(tmp_path / "store", filename)
    assert "origin_class: PUBLIC_VERIFIED" in page
    assert "origin_provenance: git-commit:" in page


def test_ingesting_a_private_document_records_private(repo, tmp_path, monkeypatch):
    from scripts.ingest import create_source_page

    secret = repo / "private" / "statutory-declaration.md"
    secret.write_text("a real private document\n", encoding="utf-8")
    _patch_roots(monkeypatch, tmp_path / "store")

    filename = create_source_page(str(secret), "cafe1234", "a real private document\n")

    page = _page_text(tmp_path / "store", filename)
    assert "origin_class: PRIVATE" in page
    assert "PUBLIC" not in page.split("---")[1]


def test_public_material_is_acceded_to_the_boundary_store_at_ingest(
    repo, tmp_path, monkeypatch
):
    """Accession happens when the document enters, not when a gate runs later.

    This is the whole point of the spec's open item: 'recording what was already
    public AT THE MOMENT IT ENTERED, with its provenance' — the opposite of
    seeding D3 from whatever happens to be tracked at build time.
    """
    from scripts.boundary import store
    from scripts.ingest import create_source_page

    boundary_root = tmp_path / "boundary"
    store.init(boundary_root, k=store.DEFAULT_K, w=store.DEFAULT_W)
    monkeypatch.setenv("ZURVAN_BOUNDARY_ROOT", str(boundary_root))
    _patch_roots(monkeypatch, tmp_path / "store")

    text = (repo / "README.md").read_text(encoding="utf-8")
    create_source_page(str(repo / "README.md"), "deadbeef", text)

    import sqlite3
    conn = sqlite3.connect(boundary_root / store.DB_NAME)
    rows = conn.execute("SELECT provenance FROM public_origins").fetchall()
    conn.close()
    assert len(rows) == 1, "the public document should be acceded exactly once"
    assert rows[0][0].startswith("git-commit:")


def test_private_material_is_never_acceded(repo, tmp_path, monkeypatch):
    from scripts.boundary import store
    from scripts.ingest import create_source_page

    boundary_root = tmp_path / "boundary"
    store.init(boundary_root, k=store.DEFAULT_K, w=store.DEFAULT_W)
    monkeypatch.setenv("ZURVAN_BOUNDARY_ROOT", str(boundary_root))
    _patch_roots(monkeypatch, tmp_path / "store")

    secret = repo / "private" / "secret.md"
    secret.write_text("confidential\n", encoding="utf-8")
    create_source_page(str(secret), "cafe1234", "confidential\n")

    import sqlite3
    conn = sqlite3.connect(boundary_root / store.DB_NAME)
    rows = conn.execute("SELECT COUNT(*) FROM public_origins").fetchone()[0]
    conn.close()
    assert rows == 0


def test_ingestion_still_works_with_no_boundary_store(repo, tmp_path, monkeypatch):
    """Boundary is opt-in; a missing store must not break ingestion."""
    from scripts.ingest import create_source_page

    monkeypatch.setenv("ZURVAN_BOUNDARY_ROOT", str(tmp_path / "does-not-exist"))
    _patch_roots(monkeypatch, tmp_path / "store")

    filename = create_source_page(str(repo / "README.md"), "deadbeef", "# Public\n")

    assert "origin_class: PUBLIC_VERIFIED" in _page_text(tmp_path / "store", filename)


# ── the false-positive class, reproduced and then resolved ───────────────────

ROUND_TRIP_DOC = (
    "The migration plan requires that every tenant boundary be verified against "
    "the pool group rather than the file modes alone, because a permissive mode "
    "on a shared directory grants exactly the access the pool separation exists "
    "to deny, and the verification gate must assert that relationship directly."
)


def _ingest_into(repo_file: Path, boundary_root: Path, store_root: Path, monkeypatch):
    """Ingest one file exactly as `scripts/ingest.py` would."""
    from scripts.ingest import create_source_page

    monkeypatch.setenv("ZURVAN_BOUNDARY_ROOT", str(boundary_root))
    _patch_roots(monkeypatch, store_root)
    text = repo_file.read_text(encoding="utf-8")
    return create_source_page(str(repo_file), "hash-" + repo_file.stem, text), text


def test_an_ingestion_round_trip_still_blocks_without_provenance(repo, tmp_path, monkeypatch):
    """The 2026-09-16 measurement, reproduced: 11 of 11 blocked artefacts were
    a tracked file colliding with its own ingested copy. The detector is right —
    that text really is in the protected corpus — and the corpus is wrong."""
    from scripts import boundary
    from scripts.boundary import store as bstore

    tracked = repo / "plan.md"
    tracked.write_text(ROUND_TRIP_DOC, encoding="utf-8")
    _git(repo, "add", "plan.md")
    _git(repo, "commit", "-qm", "plan")

    boundary_root = tmp_path / "boundary"
    (boundary_root / "sources").mkdir(parents=True)
    # The ingested copy, as it exists today: PRIVATE purely by where it landed.
    (boundary_root / "sources" / "ingested-copy.md").write_text(
        ROUND_TRIP_DOC, encoding="utf-8")
    bstore.init(boundary_root, k=bstore.DEFAULT_K, w=bstore.DEFAULT_W)
    boundary.build(boundary_root)

    verdict = boundary.scan(boundary_root, ROUND_TRIP_DOC, sink="PUBLIC_ARTIFACT")

    assert verdict.blocked, (
        "control: without recorded provenance the tracked file is blocked for "
        "quoting its own ingested copy"
    )


def test_provenance_at_ingest_clears_the_round_trip(repo, tmp_path, monkeypatch):
    """The same scan, after the SAME document was acceded when it entered.

    Nothing here scans the tree for tracked files. The exemption exists because
    at the moment of ingestion git already held those exact bytes, and that fact
    was recorded then — which is the spec's open item, and the difference
    between provenance and laundering.
    """
    from scripts import boundary
    from scripts.boundary import store as bstore

    tracked = repo / "plan.md"
    tracked.write_text(ROUND_TRIP_DOC, encoding="utf-8")
    _git(repo, "add", "plan.md")
    _git(repo, "commit", "-qm", "plan")

    boundary_root = tmp_path / "boundary"
    bstore.init(boundary_root, k=bstore.DEFAULT_K, w=bstore.DEFAULT_W)

    # Ingest it — this is where the class is decided and the accession happens.
    _ingest_into(tracked, boundary_root, tmp_path / "store", monkeypatch)

    # The protected corpus still holds the ingested copy, exactly as before.
    (boundary_root / "sources").mkdir(parents=True, exist_ok=True)
    (boundary_root / "sources" / "ingested-copy.md").write_text(
        ROUND_TRIP_DOC, encoding="utf-8")
    boundary.build(boundary_root)

    verdict = boundary.scan(boundary_root, ROUND_TRIP_DOC, sink="PUBLIC_ARTIFACT")

    assert not verdict.blocked, (
        "a document that was already in git when Zurvan ingested it must not be "
        "blocked for appearing in git"
    )


def test_a_private_document_still_blocks_after_the_change(repo, tmp_path, monkeypatch):
    """The exemption must not generalise. Same machinery, uncommitted source."""
    from scripts import boundary
    from scripts.boundary import store as bstore

    secret = repo / "private" / "confidential.md"
    secret.write_text(ROUND_TRIP_DOC, encoding="utf-8")   # never committed

    boundary_root = tmp_path / "boundary"
    bstore.init(boundary_root, k=bstore.DEFAULT_K, w=bstore.DEFAULT_W)
    _ingest_into(secret, boundary_root, tmp_path / "store", monkeypatch)

    (boundary_root / "sources").mkdir(parents=True, exist_ok=True)
    (boundary_root / "sources" / "ingested-copy.md").write_text(
        ROUND_TRIP_DOC, encoding="utf-8")
    boundary.build(boundary_root)

    verdict = boundary.scan(boundary_root, ROUND_TRIP_DOC, sink="PUBLIC_ARTIFACT")

    assert verdict.blocked, "private material must still block after provenance lands"


# ── object existence is not evidence ─────────────────────────────────────────

def test_a_dangling_blob_in_the_object_database_is_not_public(repo):
    """The loophole in the first implementation, reproduced as an attack.

    `git cat-file -e` answers "does this object exist", and `git hash-object -w`
    satisfies it with no commit, no ref, no staging and no intent. Measured on a
    real repository: the dangling blob reports EXISTS while `git log --all
    --find-object` correctly finds nothing. Anyone able to write a file could
    have manufactured PUBLIC provenance for its contents.
    """
    secret = repo / "private" / "exfiltrate-me.md"
    secret.write_text("the private text someone wants exempted\n", encoding="utf-8")

    assert classify_origin(secret)[0] == ORIGIN_PRIVATE, "control"

    # The attack: put the exact bytes into .git/objects without committing them.
    oid = _git(repo, "hash-object", "-w", "--", str(secret))
    assert oid, "the blob must actually be written for this test to mean anything"
    assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{oid}^{{blob}}"],
                          capture_output=True).returncode == 0, (
        "precondition: the weak predicate accepts this object"
    )

    origin_class, _ = classify_origin(secret)

    assert origin_class == ORIGIN_PRIVATE, (
        "a blob nothing references is not evidence that anything was published"
    )


def test_staging_alone_is_declared_and_never_exempts(repo):
    """`git add` is local, unshared and reversible.

    Staging a private document to have it acceded and then unstaging it would
    leave the text exempt forever, so the index is recorded as evidence and
    classed DECLARED.
    """
    draft = repo / "staged-only.md"
    draft.write_text("staged but never committed\n", encoding="utf-8")
    _git(repo, "add", "staged-only.md")

    origin_class, provenance = classify_origin(draft)

    assert origin_class == ORIGIN_PUBLIC_DECLARED
    assert parse_provenance(provenance)["scheme"] == "git-index"
    assert not may_accede(origin_class)


def test_committing_those_same_bytes_then_makes_them_verified(repo):
    """Part 3 of the sequence: the intended classification still works."""
    draft = repo / "eventually.md"
    draft.write_text("this will be committed\n", encoding="utf-8")
    assert classify_origin(draft)[0] == ORIGIN_PRIVATE

    _git(repo, "add", "eventually.md")
    assert classify_origin(draft)[0] == ORIGIN_PUBLIC_DECLARED

    _git(repo, "commit", "-qm", "now committed")
    origin_class, provenance = classify_origin(draft)

    assert origin_class == ORIGIN_PUBLIC_VERIFIED
    assert parse_provenance(provenance)["scheme"] == "git-commit"
    assert may_accede(origin_class)


def test_provenance_records_the_repository_identity(repo):
    """An exemption must be auditable: which blob, in which repository."""
    _, provenance = classify_origin(repo / "README.md")
    parsed = parse_provenance(provenance)

    assert parsed["value"] == _git(repo, "hash-object", "README.md")
    assert parsed["repo"] == _git(repo, "rev-list", "--max-parents=0", "--all").splitlines()[-1]
    # A filesystem path would name a private project inside a stored page.
    assert "/" not in parsed["repo"]


def test_a_declared_origin_is_never_acceded(repo, tmp_path, monkeypatch):
    """End to end: a URL declaration must not buy a D3 exemption."""
    from scripts.boundary import store
    from scripts.ingest import create_source_page

    boundary_root = tmp_path / "boundary"
    store.init(boundary_root, k=store.DEFAULT_K, w=store.DEFAULT_W)
    monkeypatch.setenv("ZURVAN_BOUNDARY_ROOT", str(boundary_root))
    _patch_roots(monkeypatch, tmp_path / "store")

    draft = repo / "claimed.md"
    draft.write_text("I promise this came from the web\n", encoding="utf-8")
    create_source_page(str(draft), "hash1", draft.read_text(encoding="utf-8"),
                       declared_public_url="https://example.org/paper.pdf")

    import sqlite3
    conn = sqlite3.connect(boundary_root / store.DB_NAME)
    acceded = conn.execute("SELECT COUNT(*) FROM public_origins").fetchone()[0]
    conn.close()

    assert acceded == 0, "an unverified declaration must not enter the public corpus"


# ── which refs count as the sink: an allowlist, never "all refs" ─────────────

def test_a_stashed_file_is_not_verified(repo):
    """`git stash` writes a commit reachable from `refs/stash`.

    Measured before this rule existed: `git log --all --find-object` finds a
    stashed blob, so stashing a private document would have laundered it into
    PUBLIC_VERIFIED and acceded it permanently. Stashing is a local, unshared,
    routinely-reverted operation — the opposite of publication.
    """
    secret = repo / "private" / "wip.md"
    secret.parent.mkdir(exist_ok=True)
    secret.write_text("work in progress, confidential\n", encoding="utf-8")
    _git(repo, "add", "-f", "private/wip.md")
    _git(repo, "stash", "-q")
    secret.parent.mkdir(exist_ok=True)      # `git stash` removed the empty dir
    secret.write_text("work in progress, confidential\n", encoding="utf-8")

    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/stash"), (
        "precondition: the stash ref must exist for this test to mean anything"
    )
    assert classify_origin(secret)[0] != ORIGIN_PUBLIC_VERIFIED


def test_a_throwaway_local_branch_is_not_verified(repo):
    """A scratch branch is not the protected sink, and creating one is free."""
    scratch = repo / "scratch.md"
    scratch.write_text("committed only on a scratch branch\n", encoding="utf-8")
    _git(repo, "checkout", "-q", "-b", "wip/temp")
    _git(repo, "add", "scratch.md")
    _git(repo, "commit", "-qm", "scratch")
    _git(repo, "checkout", "-q", "main")
    scratch.write_text("committed only on a scratch branch\n", encoding="utf-8")

    assert classify_origin(scratch)[0] != ORIGIN_PUBLIC_VERIFIED


def test_a_blob_reachable_only_from_refs_original_is_not_verified(repo):
    """`refs/original/*` is filter-branch's backup of REWRITTEN history.

    Its whole purpose is to hold what was deliberately removed — which is
    frequently the thing someone rewrote history to un-publish.
    """
    removed = repo / "removed.md"
    removed.write_text("this was rewritten out of history\n", encoding="utf-8")
    _git(repo, "add", "removed.md")
    _git(repo, "commit", "-qm", "to be rewritten")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/original/refs/heads/main", head)
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    removed.write_text("this was rewritten out of history\n", encoding="utf-8")

    assert _git(repo, "for-each-ref", "--format=%(refname)", "refs/original"), (
        "precondition: the backup ref must exist"
    )
    assert classify_origin(removed)[0] != ORIGIN_PUBLIC_VERIFIED


def test_a_manufactured_ref_outside_the_allowlist_is_not_verified(repo):
    """`git commit-tree` + `git update-ref` builds history with no workflow.

    The allowlist is what makes this inert: an attacker can create refs all day
    without naming one the policy accepts.
    """
    secret = repo / "private" / "manufactured.md"
    secret.parent.mkdir(exist_ok=True)
    secret.write_text("manufactured history\n", encoding="utf-8")
    blob = _git(repo, "hash-object", "-w", "--", str(secret))
    tree_spec = f"100644 blob {blob}\tmanufactured.md"
    tree = subprocess.run(["git", "-C", str(repo), "mktree"], input=tree_spec,
                          capture_output=True, text=True, check=True).stdout.strip()
    commit = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-m", "x"],
                            capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "update-ref", "refs/heads/attacker", commit)

    assert classify_origin(secret)[0] != ORIGIN_PUBLIC_VERIFIED


def test_the_configured_protected_branch_verifies(repo, monkeypatch):
    """The allowlist is configurable — a project with release refs declares them."""
    release = repo / "release-note.md"
    release.write_text("shipped in the release\n", encoding="utf-8")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "add", "release-note.md")
    _git(repo, "commit", "-qm", "release")
    _git(repo, "checkout", "-q", "main")
    release.write_text("shipped in the release\n", encoding="utf-8")

    assert classify_origin(release)[0] != ORIGIN_PUBLIC_VERIFIED, "not by default"

    monkeypatch.setenv("ZURVAN_PROTECTED_REFS", "refs/heads/main,refs/heads/release")
    assert classify_origin(release)[0] == ORIGIN_PUBLIC_VERIFIED


def test_protected_refs_default_to_the_canonical_branch_only(repo):
    from scripts.provenance import protected_refs

    _git(repo, "branch", "wip/scratch")
    refs = protected_refs(str(repo))

    assert "refs/heads/main" in refs
    assert not any("scratch" in r or "stash" in r for r in refs), refs


# ── what happens when history moves after a valid accession ──────────────────

def test_accession_is_historical_evidence_and_survives_a_history_rewrite(
    repo, tmp_path, monkeypatch
):
    """DECIDED MODEL: an accession records what was true when it was made.

    A blob validly acceded from the canonical branch stays exempt even if the
    branch is later reset, rebased or deleted. Three reasons, and the choice is
    deliberate rather than incidental:

    * **The disclosure already happened.** Those bytes were on the canonical
      branch; anyone with access to the repository could have taken them.
      Rewriting history does not un-disclose them, and pretending otherwise
      would be the same mistake as treating a deleted file as never having
      existed.
    * **Re-validating would make routine work re-block.** Requiring CURRENT
      reachability on every scan means an ordinary rebase silently turns
      previously-clean content into a blocked commit, with no edit to explain
      it. Worse, D3 would have to re-resolve every accession on every scan.
    * **Revocation stays a deliberate act.** The design already carries a
      declassification ledger for exactly this; removing an exemption should be
      something a human did, recorded, not a side effect of `git rebase`.

    The cost, stated: bytes acceded from a commit that was later rewritten out
    of history remain exempt until someone revokes the accession. That is why
    the accession records the blob id and the repository — so an audit can find
    and challenge it.
    """
    import sqlite3

    from scripts import boundary
    from scripts.boundary import store as bstore

    doc = repo / "published.md"
    doc.write_text(ROUND_TRIP_DOC, encoding="utf-8")
    _git(repo, "add", "published.md")
    _git(repo, "commit", "-qm", "publish")
    assert classify_origin(doc)[0] == ORIGIN_PUBLIC_VERIFIED

    boundary_root = tmp_path / "boundary"
    bstore.init(boundary_root, k=bstore.DEFAULT_K, w=bstore.DEFAULT_W)
    _ingest_into(doc, boundary_root, tmp_path / "store", monkeypatch)

    conn = sqlite3.connect(boundary_root / bstore.DB_NAME)
    acceded_before = conn.execute("SELECT COUNT(*) FROM public_origins").fetchone()[0]
    conn.close()
    assert acceded_before == 1

    # History moves: the commit is rewritten out of the canonical branch.
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    doc.write_text(ROUND_TRIP_DOC, encoding="utf-8")

    assert classify_origin(doc)[0] != ORIGIN_PUBLIC_VERIFIED, (
        "current evidence is gone, so a NEW ingest would not verify"
    )

    # ...but the existing accession stands, and still exempts.
    conn = sqlite3.connect(boundary_root / bstore.DB_NAME)
    acceded_after = conn.execute("SELECT COUNT(*) FROM public_origins").fetchone()[0]
    conn.close()
    assert acceded_after == 1, "a rebase must not silently revoke an exemption"

    (boundary_root / "sources").mkdir(parents=True, exist_ok=True)
    (boundary_root / "sources" / "ingested-copy.md").write_text(
        ROUND_TRIP_DOC, encoding="utf-8")
    boundary.build(boundary_root)
    verdict = boundary.scan(boundary_root, ROUND_TRIP_DOC, sink="PUBLIC_ARTIFACT")

    assert not verdict.blocked, "the exemption survives until it is revoked"
