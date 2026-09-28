"""Design §6.2: stale is a normal state, not corruption. Conflating them is
what makes operators reach for a rebuild they do not need."""

import pytest

from scripts import derived_health as dh
from scripts import knowledge_revision as kr


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path, actor="test")
    return tmp_path


def _build_graph(store):
    import scripts.graph_build as gb
    from scripts.build_snapshot import capture_build_inputs
    with capture_build_inputs(store) as bs:
        gb.build_graph(str(store / "data" / "graph.sqlite"), snapshot=bs)


def _write(store, name):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=store):
        write_file_safely(str(store / "wiki" / name), "x\n")


def test_a_fresh_build_is_current(store):
    _build_graph(store)
    assert dh.store_health("graph", store)[0] == dh.CURRENT


def test_a_write_after_the_build_makes_it_stale_not_invalid(store):
    _build_graph(store)
    _write(store, "b.md")
    state, built, now, _ = dh.store_health("graph", store)
    assert state == dh.STALE and int(built) == now - 1


def test_an_unstamped_store_is_invalid(store):
    import scripts.graph_build as gb
    gb.build_graph(str(store / "data" / "graph.sqlite"))       # live, unstamped
    state, _, _, reason = dh.store_health("graph", store)
    assert state == dh.INVALID and "no truthful source snapshot" in reason


def test_a_missing_store_is_invalid(store):
    state, _, _, reason = dh.store_health("search", store)
    assert state == dh.INVALID and "missing" in reason


def test_a_stamp_from_the_future_is_invalid(store):
    """Metadata mismatch (design §6.2): a store claiming a revision the ledger
    has not reached."""
    _build_graph(store)
    import sqlite3, os
    db = store / "data" / "graph.sqlite"
    os.chmod(db, 0o644)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE graph_meta SET value = '999' WHERE key = 'built_from_revision'")
    conn.commit()
    conn.close()
    assert dh.store_health("graph", store)[0] == dh.INVALID


def test_a_store_with_no_ledger_is_invalid_not_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    import scripts.graph_build as gb
    from scripts.build_snapshot import inputs_for_rebuild
    with inputs_for_rebuild(tmp_path) as bs:
        assert bs is None
        gb.build_graph(str(tmp_path / "data" / "graph.sqlite"), snapshot=bs)
    assert dh.store_health("graph", tmp_path)[0] == dh.INVALID


def test_gate_5_reconcile_then_rebuild_is_current(store):
    """Gate item 5, second half, as the design states it: a writer dies
    mid-operation (SUSPECT), reconcile reaches CLEAN WITHOUT a rebuild, and a
    rebuild afterwards makes the derived store CURRENT. The first version of
    this test never reconciled (final review, 2026-09-28)."""
    import os, signal, subprocess, sys
    from scripts import knowledge_reconcile as krec
    _build_graph(store)
    child = subprocess.run([sys.executable, "-c", (
        "import os, signal\n"
        "from scripts import knowledge_revision as kr\n"
        "from scripts.safe_write import write_file_safely\n"
        f"with kr.operation(writer='doomed', root={str(store)!r}):\n"
        f"    write_file_safely({str(store / 'wiki' / 'torn.md')!r}, 'x')\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n")],
        env={**os.environ, "PYTHONPATH": "."})
    assert child.returncode == -signal.SIGKILL
    kr.detect_orphans(store)
    assert kr.current(store).state == kr.SUSPECT
    result = krec.reconcile(root=store, accept=True, actor="test", reason="probe")
    assert result["status"] == "CLEAN" and kr.current(store).state == kr.CLEAN
    assert dh.store_health("graph", store)[0] == dh.STALE
    _build_graph(store)
    assert dh.store_health("graph", store)[0] == dh.CURRENT


# ── final review fixes: health never crashes, and never hides SUSPECT ───────

def _graph_db(store):
    return store / "data" / "graph.sqlite"


def test_a_graph_built_before_stamps_existed_is_invalid_not_a_crash(store):
    import sqlite3
    (store / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(_graph_db(store))
    conn.execute("CREATE TABLE nodes (node_id TEXT)")
    conn.commit()
    conn.close()
    state, _, _, reason = dh.store_health("graph", store)
    assert state == dh.INVALID and "unreadable" in reason


def test_a_non_numeric_stamp_is_invalid_not_a_crash(store):
    import os, sqlite3
    _build_graph(store)
    os.chmod(_graph_db(store), 0o644)
    conn = sqlite3.connect(_graph_db(store))
    conn.execute("UPDATE graph_meta SET value = 'r3' WHERE key = 'built_from_revision'")
    conn.commit()
    conn.close()
    assert dh.store_health("graph", store)[0] == dh.INVALID


def test_a_corrupt_store_is_invalid_not_a_crash(store):
    (store / "data" / "search.sqlite").write_bytes(b"not a database at all")
    state, _, _, reason = dh.store_health("search", store)
    assert state == dh.INVALID and "unreadable" in reason


def test_a_stamp_whose_manifest_disagrees_is_invalid(store):
    """At the current revision, the stamp's manifest must be the ledger's -
    otherwise the store names the right revision number for other bytes."""
    import os, sqlite3
    _build_graph(store)
    os.chmod(_graph_db(store), 0o644)
    conn = sqlite3.connect(_graph_db(store))
    conn.execute("UPDATE graph_meta SET value = 'not-the-ledger-digest'"
                 " WHERE key = 'built_from_manifest'")
    conn.commit()
    conn.close()
    state, _, _, reason = dh.store_health("graph", store)
    assert state == dh.INVALID and "manifest" in reason


def test_health_says_so_when_the_ledger_is_suspect(store):
    _build_graph(store)
    kr._mark_suspect(store)
    state, _, _, reason = dh.store_health("graph", store)
    assert "SUSPECT" in reason


def test_stamp_verdict_is_the_one_definition_of_coherent():
    """store_health and a measurement capture share this, so "coherent"
    cannot come to mean two things (design §6.2, §7.1 step 6)."""
    d = "digest"
    assert dh.stamp_verdict({}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "x"}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "6"}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "5",
                             "built_from_manifest": "other"}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "5",
                             "built_from_manifest": d}, 5, d) == (dh.CURRENT, "")
    assert dh.stamp_verdict({"built_from_revision": "3"}, 5, d) == (
        dh.STALE, "2 revision(s) behind")
