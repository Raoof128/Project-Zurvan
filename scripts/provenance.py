#!/usr/bin/env python3
"""Where a document came from, recorded at the moment it is acceded.

Ingestion used to record `title`, `hash`, `figures`, `extraction_status` and
`ingested_at`. None of those say anything about origin, so a document's class
was decided by the directory it landed in: everything under `wiki/sources/`
became PRIVATE, including the project's own README. Boundary then did its job
correctly and refused to let the original public file be committed, and the
false-positive rate became unmeasurable — 11 of 11 blocked artefacts were
ingestion round-trips rather than coincidences (spec §8).

## Three classes, because "public" is not one thing

    PRIVATE  →  PUBLIC_DECLARED  →  PUBLIC_VERIFIED

Only `PUBLIC_VERIFIED` is ever acceded to D3's trusted public corpus, and
therefore only `PUBLIC_VERIFIED` can exempt a span from a block.
`PUBLIC_DECLARED` is remembered — it carries real information, and an audit can
promote it later — but a claim nobody checked must not become an exemption, or
the claim itself is the laundering primitive.

## What counts as verified, and what does not

The sink being protected is `PUBLIC_ARTIFACT`: "anything tracked, pushed,
exported or published". Bytes **reachable from a ref in the protected
repository** have already reached that sink, so emitting them again into that
same repository discloses nothing new. That is the whole claim, and it is
checkable.

Three near-misses, each of which looked like evidence and is not:

* **Object existence is not reachability.** The first version of this module
  asked `git cat-file -e`, which answers "does this object exist". `git
  hash-object -w` satisfies it without any commit, ref, staging or intent —
  measured: a dangling blob reports EXISTS while `git log --all --find-object`
  correctly finds nothing. Writing bytes into `.git/objects` would have
  manufactured PUBLIC provenance. Reachability is now proved with
  `git log --all --find-object`.

* **The index is not a sink.** A staged file is "tracked", and the sink
  definition says tracked — but `git add` is local, unshared, trivially
  reversible, and entirely attacker-controlled. Staging a private document to
  have it acceded, then unstaging it, would leave the text exempt forever. Index
  evidence is recorded as `git-index` and classes as PUBLIC_DECLARED.

* **Another repository is not this repository.** Bytes committed in a sibling
  project's checkout have reached *that* repo's sink, not this one, and
  `git init && git add && git commit` in a scratch directory is not a
  publication event. Foreign-repo evidence classes as PUBLIC_DECLARED. Treating
  it as verified would mean anyone who can run git can cross the boundary.

## Never retroactively

A page with no recorded class is PRIVATE, and nothing here can walk the tree and
upgrade it. Seeding the public corpus from "everything currently tracked" is the
laundering hole (Global Constraint 7): a leak already in the tree would be
blessed as a known-public origin forever. Provenance is recorded forward, never
inferred backward.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ORIGIN_PRIVATE = "PRIVATE"
ORIGIN_PUBLIC_DECLARED = "PUBLIC_DECLARED"
ORIGIN_PUBLIC_VERIFIED = "PUBLIC_VERIFIED"

#: The only class D3 accession accepts.
EXEMPTING_CLASSES = frozenset({ORIGIN_PUBLIC_VERIFIED})
_KNOWN_CLASSES = frozenset(
    {ORIGIN_PRIVATE, ORIGIN_PUBLIC_DECLARED, ORIGIN_PUBLIC_VERIFIED})

#: Frontmatter keys written by `ingest.create_source_page`.
CLASS_KEY = "origin_class"
PROVENANCE_KEY = "origin_provenance"

#: Evidence types, strongest first. Recorded separately from the class so a
#: later policy change can re-read what was actually observed without re-ingest.
SCHEME_GIT_COMMIT = "git-commit"     # reachable from a ref
SCHEME_GIT_INDEX = "git-index"       # staged only
SCHEME_URL_DECLARED = "url-declared"  # a human assertion; nothing checks it
SCHEME_LOCAL = "local-file"

_GIT_TIMEOUT = 60


def protected_repo_root() -> Path | None:
    """The repository whose `PUBLIC_ARTIFACT` sink Boundary protects.

    Defaults to this checkout. `ZURVAN_PROTECTED_REPO` overrides it, which is
    how the tests exercise the foreign-repository rule without inventing a
    second notion of "the repo".
    """
    override = os.environ.get("ZURVAN_PROTECTED_REPO")
    if override:
        return Path(override).resolve()
    from scripts.config import PROJECT_ROOT
    return Path(PROJECT_ROOT).resolve()


def parse_provenance(provenance: str) -> dict:
    """`scheme:value[@repo]` -> {"scheme", "value", "repo"}."""
    text = (provenance or "").strip()
    scheme, sep, rest = text.partition(":")
    if not sep or not scheme.strip() or not rest.strip():
        return {"scheme": "", "value": "", "repo": ""}
    value, _, repo = rest.partition("@")
    return {"scheme": scheme.strip(), "value": value.strip(), "repo": repo.strip()}


def _git(repo_dir: str, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", repo_dir, *args],
                                capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _repo_identity(repo_dir: str) -> str:
    """A stable, non-identifying id for a repository: its root commit.

    A filesystem path would name a project, and these strings are written into
    pages. The root commit is checkable by anyone holding the repo and says
    nothing to anyone who does not.
    """
    roots = _git(repo_dir, "rev-list", "--max-parents=0", "--all")
    if not roots:
        return "unknown"
    return roots.splitlines()[-1].strip()


def _default_ref_patterns(repo_dir: str) -> list[str]:
    """The repository's canonical branch, and nothing else.

    Resolved from git rather than assumed: `origin/HEAD` when the remote
    declares one, else whichever of `main`/`master` exists. A repository with
    neither yields an EMPTY list, so nothing verifies — fail closed, because a
    sink nobody can name is a sink nobody is protecting.
    """
    head = _git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:                                    # e.g. "origin/main"
        branch = head.split("/", 1)[-1]
        return [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]
    for branch in ("main", "master"):
        if _git(repo_dir, "for-each-ref", "--format=%(refname)", f"refs/heads/{branch}"):
            return [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]
    return []


def protected_refs(repo_dir: str) -> list[str]:
    """Which refs count as the protected sink. An allowlist, never "all refs".

    `git log --all` means "every ref under refs/", which is far broader than
    accepted history: it includes `refs/stash`, `refs/original/*` left by a
    filter-branch, and any throwaway local branch. Measured on a real
    repository — a file stashed with `git stash` and a file on a scratch branch
    were BOTH found by `--all`, so stashing a private document would have
    laundered it into PUBLIC_VERIFIED.

    `ZURVAN_PROTECTED_REFS` overrides the default with a comma-separated list of
    `for-each-ref` patterns, which is how a project with release refs declares
    them.
    """
    override = os.environ.get("ZURVAN_PROTECTED_REFS")
    patterns = ([p.strip() for p in override.split(",") if p.strip()]
                if override else _default_ref_patterns(repo_dir))
    refs: list[str] = []
    for pattern in patterns:
        found = _git(repo_dir, "for-each-ref", "--format=%(refname)", pattern)
        if found:
            refs.extend(line.strip() for line in found.splitlines() if line.strip())
    return refs


def _git_evidence(path: Path) -> dict | None:
    """Strongest observable evidence that these exact bytes reached a git sink.

    Returns {"scheme", "blob", "repo", "toplevel"} or None.
    """
    repo_dir = str(path.parent)
    toplevel = _git(repo_dir, "rev-parse", "--show-toplevel")
    if not toplevel:
        return None                                   # not inside a repository

    blob = _git(repo_dir, "hash-object", "--", str(path))
    if not blob:
        return None

    # Reachability from ACCEPTED refs. Two distinct weakenings are refused here:
    # `cat-file -e` (existence — a dangling blob from `git hash-object -w`
    # satisfies it) and `--all` (every ref, including the stash and scratch
    # branches). Only the allowlist counts.
    refs = protected_refs(repo_dir)
    found = _git(repo_dir, "log", *refs, "--format=%H",
                 "--find-object=" + blob, "--max-count=1") if refs else None
    if found:
        return {"scheme": SCHEME_GIT_COMMIT, "blob": blob,
                "repo": _repo_identity(repo_dir), "toplevel": toplevel}

    staged = _git(repo_dir, "diff", "--cached", "--name-only", "--find-object=" + blob)
    if staged:
        return {"scheme": SCHEME_GIT_INDEX, "blob": blob,
                "repo": _repo_identity(repo_dir), "toplevel": toplevel}

    return None


def classify_origin(path, declared_public_url: str | None = None) -> tuple[str, str]:
    """(origin_class, provenance) for ONE file, decided from evidence now."""
    path = Path(path)

    if declared_public_url is not None and not declared_public_url.strip():
        raise ValueError(
            "a declared public origin needs a non-empty provenance; the public "
            "corpus is never seeded from an empty claim (Global Constraint 7)"
        )

    evidence = _git_evidence(path)
    if evidence:
        provenance = f"{evidence['scheme']}:{evidence['blob']}@{evidence['repo']}"
        protected = protected_repo_root()
        in_protected_repo = (
            protected is not None
            and Path(evidence["toplevel"]).resolve() == protected
        )
        if evidence["scheme"] == SCHEME_GIT_COMMIT and in_protected_repo:
            return ORIGIN_PUBLIC_VERIFIED, provenance
        # Staged-only, or committed somewhere that is not the repository this
        # gate protects: real information, recorded, but never an exemption.
        return ORIGIN_PUBLIC_DECLARED, provenance

    if declared_public_url:
        return ORIGIN_PUBLIC_DECLARED, f"{SCHEME_URL_DECLARED}:{declared_public_url.strip()}"

    return ORIGIN_PRIVATE, f"{SCHEME_LOCAL}:{path.name}"


def _frontmatter(path: Path) -> dict:
    """Line-based frontmatter read, matching `graph_build`'s approach."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict = {}
    for line in lines[1:200]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def origin_class_of_page(path) -> str:
    """The class recorded on a source page — PRIVATE unless proved otherwise.

    Fail-closed on every ambiguity: no frontmatter, no key, an unrecognised
    value, or a public class with no provenance recorded beside it. The last
    case matters most: public is a claim, and a claim whose evidence was not
    written down is not one anyone can audit later.
    """
    front = _frontmatter(Path(path))
    declared = front.get(CLASS_KEY, "").strip().upper()
    if declared not in _KNOWN_CLASSES or declared == ORIGIN_PRIVATE:
        return ORIGIN_PRIVATE
    if not parse_provenance(front.get(PROVENANCE_KEY, ""))["scheme"]:
        return ORIGIN_PRIVATE
    return declared


def may_accede(origin_class: str) -> bool:
    """Only verified evidence earns a D3 exemption."""
    return origin_class in EXEMPTING_CLASSES
