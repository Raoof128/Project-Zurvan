"""Task 1 — content-addressed store, canonical manifest, validation that can fail.

Each test here is a reproduction of a false-clean that rejected the plan's first
draft. They are written against `store` alone; the end-to-end versions arrive in
Task 6 once `build()` exists.
"""
import sqlite3

import pytest

from scripts.boundary import store
import scripts.boundary as boundary

K, W = 8, 9
PROT = b"PROTECTED-SENTENCE-DEFINITELY-LONGER-THAN-THE-THRESHOLD-HERE-OK"


def _store(tmp_path):
    root = tmp_path / "b"
    store.init(root, k=K, w=W)
    (root / "sources" / "a.md").write_bytes(PROT)
    return root


def _commit_manifest(root, n_fingerprints=3, origin="origin-1"):
    conn = store.open_db(root)
    conn.execute("INSERT OR REPLACE INTO origins VALUES (?,?,?,?)",
                 (origin, "PRIVATE", store.put_blob(root, PROT), 0))
    conn.executemany("INSERT INTO fingerprints VALUES (?,?,?)",
                     [(bytes([i]) * 8, origin, i) for i in range(n_fingerprints)])
    conn.execute("UPDATE origins SET n_fingerprints=? WHERE origin_id=?",
                 (n_fingerprints, origin))
    conn.commit()
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('manifest_digest', ?)",
                 (store.manifest_digest(conn, root),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('total_fingerprints', ?)",
                 (str(n_fingerprints),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('stat_digest', ?)",
                 (store.stat_digest(root),))
    conn.commit()
    return conn


def test_threshold_is_derived_never_set(tmp_path):
    cfg = store.Config(k=32, w=33)
    assert cfg.threshold_bytes == cfg.k + cfg.w - 1 == 64
    with pytest.raises(TypeError):
        store.Config(k=32, w=33, threshold_bytes=99)


def test_invalid_winnowing_config_is_refused(tmp_path):
    for k, w in ((0, 5), (5, 0), (-1, 5)):
        with pytest.raises(store.StateIntegrityError):
            store.Config(k=k, w=w)


def test_deleting_every_fingerprint_is_detected(tmp_path):
    """The attack that rejected the previous draft: validate() reported CLEAN."""
    root = _store(tmp_path)
    conn = _commit_manifest(root)
    store.validate(conn, root)                       # healthy
    conn.execute("DELETE FROM fingerprints")
    conn.commit()
    with pytest.raises(store.StateIntegrityError):
        store.validate(conn, root)


def test_orphan_fingerprints_are_detected(tmp_path):
    root = _store(tmp_path)
    conn = _commit_manifest(root)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO fingerprints VALUES (?,?,?)", (b"z" * 8, "ghost", 0))
    conn.commit()
    with pytest.raises(store.StateIntegrityError):
        store.validate(conn, root)


def test_init_refuses_a_built_store(tmp_path):
    """init() previously rewrote k/w on a built store and left validate() clean."""
    root = _store(tmp_path)
    _commit_manifest(root).close()
    with pytest.raises(store.StateIntegrityError):
        store.init(root, k=32, w=33)


def test_instrument_tree_is_covered_by_the_manifest(tmp_path):
    """The previous manifest walked sources/ only."""
    root = _store(tmp_path)
    conn = _commit_manifest(root)
    store.validate(conn, root)
    (root / "instruments" / "gold.jsonl").write_text("added after build", encoding="utf-8")
    with pytest.raises(store.StaleStateError):
        store.validate(conn, root)


def test_manifest_framing_is_unambiguous(tmp_path):
    """sources/a+b'bc' and sources/ab+b'c' previously hashed identically."""
    a = store.canonical_manifest_bytes([("sources/a", b"bc")])
    b = store.canonical_manifest_bytes([("sources/ab", b"c")])
    assert a != b


def test_blobs_are_reverified_on_read(tmp_path):
    root = _store(tmp_path)
    digest = store.put_blob(root, PROT)
    assert store.get_blob(root, digest) == PROT
    store.blob_path(root, digest).write_bytes(b"tampered")
    with pytest.raises(store.StateIntegrityError):
        store.get_blob(root, digest)


def test_corrupt_database_is_integrity_error(tmp_path):
    root = _store(tmp_path)
    (root / store.DB_NAME).write_bytes(b"\x00\x01corrupt")
    with pytest.raises(store.StateIntegrityError):
        store.open_db(root)


def test_incompatible_detector_version_is_refused(tmp_path):
    root = _store(tmp_path)
    conn = store.open_db(root)
    conn.execute("UPDATE meta SET value='d1-from-the-future' WHERE key='d1_version'")
    conn.commit()
    conn.close()
    with pytest.raises(store.StateIntegrityError):
        store.open_db(root)


def test_malformed_meta_raises_integrity_not_valueerror(tmp_path):
    root = _store(tmp_path)
    conn = store.open_db(root)
    conn.execute("UPDATE meta SET value='corrupt' WHERE key='k'")
    conn.commit()
    with pytest.raises(store.StateIntegrityError):
        store.read_config(conn)


def test_atomic_replace_refuses_a_build_with_no_manifest(tmp_path):
    root = _store(tmp_path)

    def bad_build(tmp_db):
        sqlite3.connect(tmp_db).executescript(store.SCHEMA)

    with pytest.raises(store.StateIntegrityError):
        store.atomic_replace(root, bad_build)


def test_deque_winnow_matches_the_naive_definition():
    """The O(n) rewrite must select EXACTLY the fingerprints the naive definition
    does. A faster winnow that picks different minima silently invalidates every
    fingerprint already stored."""
    import random
    from scripts.boundary import d1

    def naive(hashes, w):
        if len(hashes) < w:
            return []
        out, prev = [], -1
        for start in range(len(hashes) - w + 1):
            win = hashes[start:start + w]
            m = min(win)
            idx = start + (len(win) - 1 - win[::-1].index(m))
            if idx != prev:
                out.append((m, idx)); prev = idx
        return out

    random.seed(11)
    for trial in range(30):
        n = random.randrange(0, 400)
        # a small alphabet forces frequent ties, which is where tie-breaking bites
        hs = [random.randrange(6) for _ in range(n)]
        w = random.randrange(1, 12)
        assert d1.winnow(hs, w) == naive(hs, w), f"trial {trial}: n={n} w={w}"


def test_validation_does_not_rehash_the_corpus_on_every_scan(tmp_path):
    """Measured 2026-09-16: re-hashing the protected tree per scan cost ~14.6 s
    on a 13 MB corpus, flat regardless of input size — 36x the 400 ms budget.

    A cheap stat probe (path+size+mtime) gates the expensive content walk, so an
    unchanged corpus validates without reading 13 MB. Any stat change escalates
    to the full content comparison, so correctness is unchanged for the careless
    agent this defends against.
    """
    root = _store(tmp_path)
    conn = _commit_manifest(root)
    store.validate(conn, root)
    import time
    t = time.perf_counter()
    for _ in range(50):
        store.validate(conn, root)
    per_call_ms = (time.perf_counter() - t) / 50 * 1000
    assert per_call_ms < 20, f"validation costs {per_call_ms:.1f} ms per scan"


def test_a_content_change_that_preserves_stat_is_still_caught(tmp_path):
    """The stat probe is an OPTIMISATION, not the authority.

    Honest bound: it assumes an agent that edits a file changes its size or
    mtime. That holds for the careless-agent threat model and NOT for a hostile
    one, which is out of scope for v1 — stated here rather than discovered later.
    """
    root = _store(tmp_path)
    conn = _commit_manifest(root)
    store.validate(conn, root)
    p = root / "sources" / "a.md"
    st = p.stat()
    p.write_bytes(b"X" * len(PROT))              # same length
    import os
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))   # same mtime
    with pytest.raises((store.StaleStateError, store.StateIntegrityError)):
        store.validate(conn, root, deep=True)


def test_a_missing_committed_total_fails_closed(tmp_path):
    """Deleting one meta row must not disable the integrity check.

    The per-scan guard compares a committed fingerprint total. Treating an absent
    total as "nothing to check" would make `DELETE FROM meta WHERE
    key='total_fingerprints'` a one-row bypass.
    """
    root = _store(tmp_path)
    conn = _commit_manifest(root)
    store.validate(conn, root)
    conn.execute("DELETE FROM meta WHERE key='total_fingerprints'")
    conn.commit()
    with pytest.raises(store.StateIntegrityError):
        store.validate(conn, root)


def test_a_release_granted_under_an_older_detector_does_not_carry_over(tmp_path):
    """Declassification is scoped to the detector semantics it was granted under.

    D1 changed on 2026-09-16 (degenerate k-grams dropped, T measured against
    informative length). A release recorded before that decided a DIFFERENT
    question, so it must not silently authorise the same match afterwards.
    """
    root = tmp_path / "b"
    (root / "sources").mkdir(parents=True)
    secret = ("The quarterly containment review concluded that the residue "
              "threshold must be recalibrated before the next audit cycle.")
    (root / "sources" / "a.md").write_text(secret, encoding="utf-8")
    boundary.build(root)

    v = boundary.scan(root, secret, sink="PUBLIC_ARTIFACT")
    assert v.blocked
    boundary.release(root, v.matches[0].match_id, ["PUBLIC_ARTIFACT"],
                     reason="approved for publication")
    assert not boundary.scan(root, secret, sink="PUBLIC_ARTIFACT").blocked

    # Rewrite the grant as if it had been issued by the previous detector.
    conn = sqlite3.connect(root / boundary.store.DB_NAME)
    conn.execute("UPDATE releases SET normalisation='d1-winnow-v1'")
    conn.commit()
    conn.close()
    assert boundary.scan(root, secret, sink="PUBLIC_ARTIFACT").blocked, (
        "a release from an older detector version must not still declassify")
