"""Z2 Task 2: the claim store - schema, erasable content, hash chain, lock.

Spec §7.1; plan Task 2. Every path is under the test's own root.
"""

import re
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from scripts import claim_store as cs
from scripts import knowledge_revision as kr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def _rev(conn, claim_id, body="a claim", mutation="m000000000001", **extra):
    cid = cs.put_content(conn, "text", body)
    row = {"claim_id": claim_id, "event": "assert", "status": "asserted",
           "text_cid": cid, "confidence": "high", "confidence_basis": ["user_assertion"],
           "valid_time_basis": "unknown", "actor": "local:test", "method": "human",
           "mutation_id": mutation}
    row.update(extra)
    return cs.append_revision(conn, row)


def _three(conn):
    ids = []
    for n in range(3):
        claim_id = cs.new_claim_id()
        cs.insert_claim(conn, claim_id, "m000000000001")
        _rev(conn, claim_id, body=f"claim {n}")
        ids.append(claim_id)
    return ids


def test_pragmas_on_every_connection(root):
    conn = cs.connect(root)
    _three(conn)
    conn.close()
    for readonly in (False, True):
        c = cs.connect(root, readonly=readonly)
        try:
            assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert c.execute("PRAGMA secure_delete").fetchone()[0] == 1
            assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        finally:
            c.close()
    for suffix in ("-wal", "-shm", "-journal"):
        assert not (root / "data" / f"claims.sqlite{suffix}").exists()


def test_readonly_connect_refuses_a_missing_store(root):
    with pytest.raises(sqlite3.OperationalError):
        cs.connect(root, readonly=True)
    assert not cs.db_path(root).exists()


def test_ids_are_random_and_prefixed(root):
    conn = cs.connect(root)
    claim_ids = {cs.new_claim_id() for _ in range(1000)}
    cids = {cs.put_content(conn, "text", "same body") for _ in range(1000)}
    assert len(claim_ids) == 1000 and len(cids) == 1000      # same body, distinct cids
    assert all(re.fullmatch(r"clm_[0-9a-f]{16}", c) for c in claim_ids)
    assert all(re.fullmatch(r"cnt_[0-9a-f]{16}", c) for c in cids)


def test_rev_is_allocated_per_claim(root):
    conn = cs.connect(root)
    claim_id = cs.new_claim_id()
    cs.insert_claim(conn, claim_id, "m000000000001")
    assert _rev(conn, claim_id)["rev"] == 1
    assert _rev(conn, claim_id, event="revise")["rev"] == 2


def test_chain_detects_reordered_or_edited_row(root):
    conn = cs.connect(root)
    ids = _three(conn)
    assert cs.chain_ok(conn) == (True, None)
    conn.execute("UPDATE revisions SET status = 'retracted' WHERE claim_id = ?", (ids[1],))
    ok, problem = cs.chain_ok(conn)
    assert ok is False and ids[1] in problem and "rev 1" in problem


def test_chain_detects_an_edited_evidence_row(root):
    conn = cs.connect(root)
    claim_id = cs.new_claim_id()
    cs.insert_claim(conn, claim_id, "m000000000001")
    ev = {"source_ref_cid": cs.put_content(conn, "source_ref", "wiki/sources/a.md"),
          "selector_cid": cs.put_content(conn, "selector", "{}"),
          "capsule_cid": cs.put_content(conn, "capsule", "ctx"), "reader": "utf8"}
    _rev(conn, claim_id, evidence=[ev])
    assert cs.chain_ok(conn) == (True, None)
    other = cs.put_content(conn, "source_ref", "/elsewhere.md")
    conn.execute("UPDATE evidence SET source_ref_cid = ? WHERE claim_id = ?", (other, claim_id))
    assert cs.chain_ok(conn)[0] is False


def test_chain_survives_content_erasure(root):
    conn = cs.connect(root)
    _three(conn)
    conn.execute("UPDATE content SET body = NULL, erased_at = '2026-11-14T00:00:00Z',"
                 " erase_reason_class = 'client_request'")
    assert cs.chain_ok(conn) == (True, None)


def test_row_hash_never_contains_body(root):
    conn = cs.connect(root)
    claim_id = cs.new_claim_id()
    cs.insert_claim(conn, claim_id, "m000000000001")
    stored = _rev(conn, claim_id, body="SECRET-XYZ", tags=["acme"],
                  extraction_key="deadbeef:claim-1")
    for row in conn.execute("SELECT * FROM revisions"):
        assert not any("SECRET-XYZ" in str(v) or "acme" in str(v) or "deadbeef" in str(v)
                       for v in row)
    before = stored["row_hash"]
    conn.execute("UPDATE content SET body = 'EDITED' WHERE cid = ?", (stored["text_cid"],))
    assert conn.execute("SELECT row_hash FROM revisions WHERE claim_id = ?",
                        (claim_id,)).fetchone()[0] == before
    assert cs.chain_ok(conn) == (True, None)


def test_head_tracks_the_last_row(root):
    conn = cs.connect(root)
    ids = _three(conn)
    last = conn.execute("SELECT row_hash FROM revisions ORDER BY seq DESC LIMIT 1").fetchone()[0]
    assert conn.execute("SELECT last_rev_hash FROM head WHERE id = 1").fetchone()[0] == last
    assert ids


def test_write_refused_before_migration(root):
    (root / "wiki" / "claims" / "claim-0001.md").write_text(
        "---\ntype: claim\nconfidence: \"high\"\n---\n\n# Claim\nlegacy\n", encoding="utf-8")
    with pytest.raises(cs.ClaimRefused) as ei:
        cs.require_migrated(root)
    assert ei.value.code == "MIGRATION_REQUIRED"
    assert not cs.db_path(root).exists()


def test_generated_pages_or_an_existing_store_pass_the_migration_guard(root):
    (root / "wiki" / "claims" / "clm_x.md").write_text(
        "---\ntype: claim\nauthority: structured\nstatus: asserted\n---\n\n# Claim\nnew\n",
        encoding="utf-8")
    cs.require_migrated(root)                     # only structured pages: fine
    (root / "wiki" / "claims" / "claim-0001.md").write_text("---\ntype: claim\n---\nlegacy\n",
                                                            encoding="utf-8")
    cs.connect(root).close()                      # a store exists: migration has run
    cs.require_migrated(root)


def test_lock_requires_operation(root):
    with pytest.raises(kr.KnowledgeOperationRequired):
        cs.acquire_for_operation(root)


def test_lock_is_released_when_the_operation_ends(root):
    with kr.operation(writer="test", root=root):
        cs.acquire_for_operation(root)
        cs.acquire_for_operation(root)            # idempotent within one operation
    with kr.operation(writer="test", root=root):
        cs.acquire_for_operation(root, timeout_s=0.5)   # free again


def test_second_writer_blocks_then_busy(root):
    holder = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[1])!r})
        from scripts import knowledge_revision as kr, claim_store as cs
        with kr.operation(writer="holder", root={str(root)!r}):
            cs.acquire_for_operation({str(root)!r})
            print("held", flush=True)
            time.sleep(5)
    """)
    proc = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with kr.operation(writer="test", root=root):
            with pytest.raises(cs.ClaimRefused) as ei:
                cs.acquire_for_operation(root, timeout_s=0.5)
        assert ei.value.code == "CLAIMS_BUSY"
    finally:
        proc.kill()
        proc.wait()
