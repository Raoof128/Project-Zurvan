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
    """Gate item 5, second half: reconcile reaches CLEAN without a rebuild,
    and a rebuild afterwards makes the derived store CURRENT."""
    _build_graph(store)
    _write(store, "b.md")
    assert dh.store_health("graph", store)[0] == dh.STALE
    _build_graph(store)
    assert dh.store_health("graph", store)[0] == dh.CURRENT
