"""Task 1 — content-addressed store, canonical manifest, validation that can fail.

Each test here is a reproduction of a false-clean that rejected the plan's first
draft. They are written against `store` alone; the end-to-end versions arrive in
Task 6 once `build()` exists.
"""
import sqlite3

import pytest

from scripts.boundary import store

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
