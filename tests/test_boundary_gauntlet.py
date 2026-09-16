"""Zurvan Boundary — the adversarial test gauntlet.

**These tests are RED by design.** `scripts/boundary.py` does not exist yet, so
every contract below skips with a stated reason and turns live the moment the
module lands. They are written first, deliberately: a guarantee whose tests are
written after the implementation tends to test what the implementation does
rather than what the spec promised.

Spec: `docs/superpowers/specs/2026-09-15-zurvan-boundary-design.md`

**The API contract these tests define** (the implementation must satisfy it, not
the reverse):

    boundary.build(root)                     -> BuildResult(digest, n_sources, n_fingerprints)
    boundary.scan(store, text, sink) -> Verdict
    boundary.scan_blob(store, path, blob, sink)     -> Verdict
    Verdict.blocked                          -> bool
    Verdict.matches                          -> list[Match]
    Match.source_class                       -> "PRIVATE" | "INSTRUMENT"
    Match.detector                           -> "D1" | "D2"
    Match.span_digest                        -> str            (never cleartext)
    Match.sink                               -> str

    sinks: PUBLIC_ARTIFACT | RETRIEVAL_CORPUS | EVAL_OUTPUT | PRIVATE_LOCAL

Every fixture here is SYNTHETIC. No gold query text and no private project name
appears in this file — a test suite for a leak detector must not itself be a
leak, and `tests/` is tracked.
"""
import hashlib
import os
from pathlib import Path

import pytest

boundary = pytest.importorskip(
    "scripts.boundary",
    reason="Zurvan Boundary is specced but not implemented — gauntlet is RED by design",
)

PRIVATE_DOC = (
    "The quarterly containment review concluded that the residue threshold "
    "must be recalibrated before the next audit cycle begins in earnest."
)
INSTRUMENT_ITEM = "how does the ledger reconcile a withdrawn attestation"


@pytest.fixture
def store(tmp_path):
    """A throwaway Boundary store. Never the live one — see tests/conftest.py."""
    root = tmp_path / "boundary"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "private-a.md").write_text(PRIVATE_DOC, encoding="utf-8")
    (root / "instruments").mkdir()
    (root / "instruments" / "gold.jsonl").write_text(
        '{"id": 1, "query": "%s"}\n' % INSTRUMENT_ITEM, encoding="utf-8"
    )
    boundary.build(root)
    return root


# ── row 5 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("delta,expect_block", [(-1, False), (0, True), (1, True)])
def test_threshold_edges_T_minus_1_T_and_T_plus_1(store, delta, expect_block):
    """Proves the >=T guarantee holds exactly at its boundary.

    Fails if the winnowing parameters (k, w) do not actually deliver the claimed
    guarantee — a span of exactly T bytes is the case a k+w-1 mismatch loses.
    """
    T = boundary.config(store).threshold_bytes
    span = PRIVATE_DOC[: T + delta]
    v = boundary.scan(store, f"prefix {span} suffix", sink="PUBLIC_ARTIFACT")
    assert v.blocked is expect_block, f"span of {len(span)}B vs T={T}"


# ── row 6 ─────────────────────────────────────────────────────────────────────
def test_one_fingerprint_with_many_origins_cannot_hide_a_protected_match(store):
    """Proves postings are one-to-many.

    Fails if the index keeps a single (source_id, offset) per fingerprint: the
    duplicated span resolves to the public origin, the protected one is dropped,
    and the leak is reported clean.
    """
    (store / "public").mkdir(exist_ok=True)
    (store / "public" / "benign.md").write_text(PRIVATE_DOC, encoding="utf-8")
    (store / "sources" / "private-b.md").write_text(PRIVATE_DOC, encoding="utf-8")
    boundary.build(store)
    v = boundary.scan(store, PRIVATE_DOC, sink="PUBLIC_ARTIFACT")
    # AMENDED 2026-09-16: `source_id` -> `origin_id`. Review finding: a Verdict
    # is printed into CI logs, and `source_id` was `path.relative_to(root)`, so a
    # Verdict disclosed the protected store's directory layout. The field is now
    # an opaque HMAC. This is a tightening of the contract, not an accommodation.
    origins = {m.origin_id for m in v.matches}
    assert len(origins) >= 2, "all postings must resolve, not just the first"


# ── row 7 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("form", ["lower", "upper", "title", "mixed"])
def test_d2_literals_match_under_every_declared_casefold(store, form):
    """Proves D2 uses Unicode casefold, not `lower()`.

    This is not hypothetical: on 2026-09-15 a redaction pass covering lower and
    upper case missed a title-cased occurrence, which survived into a later
    commit.
    """
    lit = "Vantablack"
    boundary.register_literal(store, lit, source_class="PRIVATE",
                              forbidden_sinks=["PUBLIC_ARTIFACT"])
    variants = {"lower": lit.lower(), "upper": lit.upper(),
                "title": lit.title(), "mixed": "vAnTaBlAcK"}
    v = boundary.scan(store, f"note about {variants[form]} here", sink="PUBLIC_ARTIFACT")
    assert v.blocked, f"{form} form escaped D2"


# ── row 8 ─────────────────────────────────────────────────────────────────────
def test_protected_text_in_an_unapproved_public_file_stays_blocked(store):
    """Proves D3 cannot be used as a laundering oracle.

    Fails if `public.sqlite` is seeded from "everything currently tracked": an
    accidental leak already in the tree would become a known-public origin and be
    blessed permanently.
    """
    stray = store.parent / "repo" / "accidental-leak.md"
    stray.parent.mkdir(exist_ok=True)
    stray.write_text(PRIVATE_DOC, encoding="utf-8")
    boundary.build(store)  # must NOT accede unapproved files
    v = boundary.scan(store, PRIVATE_DOC, sink="PUBLIC_ARTIFACT")
    assert v.blocked, "an unapproved file must never confer public origin"


# ── row 9 ─────────────────────────────────────────────────────────────────────
def test_a_public_subspan_does_not_exempt_the_longer_protected_residual(store):
    """Proves D3 subtracts at span level, not at source level.

    The classic partial-overlap escape: quote one genuinely public sentence, then
    append protected text, and claim the whole block is exempt.
    """
    public_part = "The quarterly containment review concluded that"
    boundary.accede_public(store, public_part, reason="reviewed release")
    boundary.build(store)
    v = boundary.scan(store, PRIVATE_DOC, sink="PUBLIC_ARTIFACT")
    assert v.blocked, "the residual beyond the public subspan must still block"


# ── row 10 ────────────────────────────────────────────────────────────────────
def test_multi_label_sink_uses_deny_overrides_allow(store):
    """Proves overlapping sink labels resolve to the strictest verdict.

    `docs/foo.md` is both PUBLIC_ARTIFACT and RETRIEVAL_CORPUS. For PRIVATE the
    matrix says block for one and allow for the other; without precedence the
    verdict is undefined and an implementation may pick either.
    """
    v = boundary.scan(store, PRIVATE_DOC, sink=["RETRIEVAL_CORPUS", "PUBLIC_ARTIFACT"])
    assert v.blocked, "any applicable BLOCK must win"


# ── row 11 ────────────────────────────────────────────────────────────────────
def test_stale_index_fails_closed_and_never_reports_clean(store):
    """Proves staleness is a hard failure, not a silent false negative.

    A protected source edited after the last build is the quietest way to lose
    the guarantee: the scan simply finds nothing.
    """
    (store / "sources" / "private-c.md").write_text(
        "Newly protected sentence added after the index was built.", encoding="utf-8"
    )
    with pytest.raises(boundary.StaleStateError):
        boundary.scan(store, "Newly protected sentence added after the index was built.",
                      sink="PUBLIC_ARTIFACT")


# ── row 12 ────────────────────────────────────────────────────────────────────
def test_span_assembled_across_two_changed_hunks_still_blocks(store):
    """Proves commit gating scans the staged POST-IMAGE, not diff hunks.

    Split a protected span across two distant hunks and no single hunk contains
    it; only the whole resulting blob does.
    """
    half = len(PRIVATE_DOC) // 2
    # AMENDED 2026-09-16: blob passed as BYTES. Review finding: a git blob is not
    # necessarily valid UTF-8, so a `str` signature cannot represent one.
    blob = (PRIVATE_DOC[:half] + ("\nfiller\n" * 40) + PRIVATE_DOC[half:]).encode("utf-8")
    v = boundary.scan_blob(store, "docs/note.md", blob, sink="PUBLIC_ARTIFACT")
    assert v.blocked, "hunk-local scanning misses cross-hunk assembly"


# ── row 13 ────────────────────────────────────────────────────────────────────
def test_protected_blob_renamed_without_textual_diff_still_blocks(store):
    """Proves rename/copy detection does not create a hole.

    A pure rename produces no textual diff, so a diff-driven scanner sees nothing
    while the protected bytes move into a tracked path.
    """
    # AMENDED 2026-09-16: bytes, as above.
    v = boundary.scan_blob(store, "docs/renamed.md", PRIVATE_DOC.encode("utf-8"),
                           sink="PUBLIC_ARTIFACT", change_type="rename")
    assert v.blocked, "a rename still places protected bytes in a public sink"


# ── row 14 ────────────────────────────────────────────────────────────────────
def test_a_release_for_one_sink_does_not_declassify_another(store):
    """Proves a release binds to ONE (match, sink) pair, not to bytes globally.

    Uses INSTRUMENT deliberately: it is the only class with TWO blocked sinks, so
    isolation is observable. An earlier version released PRIVATE for EVAL_OUTPUT
    and asserted it became unblocked — but PRIVATE -> EVAL_OUTPUT is not a blocked
    pair, so that assertion held whether or not the release existed. A test that
    passes for the wrong reason is worse than no test.
    """
    text = f"as discussed, {INSTRUMENT_ITEM} remains open"
    before = boundary.scan(store, text, sink="RETRIEVAL_CORPUS")
    assert before.blocked, "precondition: this must block before any release"

    boundary.release(store, match_id=before.matches[0].match_id,
                     sinks=["RETRIEVAL_CORPUS"], reason="author-approved quotation")

    assert not boundary.scan(store, text, sink="RETRIEVAL_CORPUS").blocked
    assert boundary.scan(store, text, sink="PUBLIC_ARTIFACT").blocked, \
        "a release for one sink leaked into another"


# ── row 15 ────────────────────────────────────────────────────────────────────
def test_instrument_text_can_never_reach_an_indexed_or_public_artefact(store):
    """Proves the INSTRUMENT class, both blocked pairs.

    Leak #1 of 2026-09-15: an instrument item quoted into a page that was never
    tracked and never pushed still outranked that item's own answer once indexed.
    Contamination with zero publication.
    """
    for sink in ("RETRIEVAL_CORPUS", "PUBLIC_ARTIFACT"):
        v = boundary.scan(store, f"as discussed, {INSTRUMENT_ITEM} remains open", sink=sink)
        assert v.blocked, f"instrument text reached {sink}"
        assert all(m.source_class == "INSTRUMENT" for m in v.matches)


# ── row 16 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("victim", ["database", "blob", "index_counts"])
def test_corrupt_boundary_state_fails_closed_not_clean(store, victim):
    """Proves the gate protects itself.

    A scanner that answers "clean" when its own state is damaged is worse than no
    scanner: it produces confident false assurance.

    AMENDED 2026-09-16, and STRENGTHENED. The original corrupted three separate
    SQLite files; the reviewed design uses one database plus a content-addressed
    blob store, because atomic replacement of five files is not achievable with
    `os.replace`. Corrupting files that do not exist tested nothing. These three
    victims are the real corruption surfaces, and `index_counts` is the LOGICAL
    corruption that the first draft of the store could not detect at all.
    """
    import sqlite3
    if victim == "database":
        (store / "boundary.sqlite").write_bytes(b"\x00\x01corrupt")
    elif victim == "blob":
        blob = next(p for p in (store / "blobs").rglob("*") if p.is_file())
        blob.write_bytes(b"tampered")
    else:
        conn = sqlite3.connect(store / "boundary.sqlite")
        conn.execute("UPDATE origins SET n_fingerprints = n_fingerprints + 999")
        conn.commit()
        conn.close()
    with pytest.raises(boundary.StateIntegrityError):
        boundary.scan(store, PRIVATE_DOC, sink="PUBLIC_ARTIFACT")


# ── row 17 ────────────────────────────────────────────────────────────────────
def test_clean_corpus_stays_under_the_predeclared_false_positive_ceiling(store):
    """Proves the FP ceiling, which is what keeps the gate from being disabled.

    Threshold predeclared in the spec: <= 1 blocked artefact per 200 clean. A
    scanner that cries wolf becomes decorative furniture, and its guarantee dies
    with it.
    """
    clean = [f"Ordinary engineering prose number {i}, nothing protected here."
             for i in range(200)]
    blocked = sum(boundary.scan(store, c, sink="PUBLIC_ARTIFACT").blocked for c in clean)
    assert blocked <= 1, f"{blocked}/200 false positives exceeds the ceiling"


# ── leak hygiene ──────────────────────────────────────────────────────────────
def test_a_verdict_never_carries_protected_cleartext(store):
    """Proves the detector is not itself an exfiltration path.

    A Verdict is printed into CI logs and error messages. If it echoes the
    matched span, the scanner leaks exactly what it exists to contain.
    """
    v = boundary.scan(store, PRIVATE_DOC, sink="PUBLIC_ARTIFACT")
    blob = repr(v) + "".join(repr(m) for m in v.matches)
    assert PRIVATE_DOC[:64] not in blob, "verdict echoed protected cleartext"
    assert all(len(m.span_digest) == 64 for m in v.matches)
