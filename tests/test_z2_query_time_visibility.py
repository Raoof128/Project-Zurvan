"""IOU-Z2-6 / spec §7.5a: a claim that stops being current leaves search at once.

The chunker skips a non-current claim page, but only when the index is
rebuilt. Until then the index still holds the page's chunks, so search and
`context` kept serving a retracted or superseded claim. Search over a LIVE
store now re-reads each claim hit's frontmatter and drops a non-current one,
then refills the result to `limit`. A search pinned to a snapshot (an explicit
`db_path` with no `hide_root`) is a measurement of frozen bytes and is left
exactly as it was.
"""

import os

import pytest

from scripts.context_export import _search_internal
from scripts.hybrid_search import search_hybrid
from scripts.rebuild_search_index import rebuild_search_index
from scripts.store_handle import StoreHandle

QUERY = "zetaword retrieval citation graph"


def _claim(status):
    return (f"---\ntype: claim\nauthority: structured\nstatus: {status}\n---\n\n"
            "# Claim\nzetaword zetaword zetaword fact about retrieval\n")


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    wiki = root / "wiki"
    (wiki / "claims").mkdir(parents=True)
    (wiki / "claims" / "clm_aaaa.md").write_text(_claim("asserted"), encoding="utf-8")
    (wiki / "citation.md").write_text("# Citation\n\nEvery citation needs a source.\n", encoding="utf-8")
    (wiki / "graph.md").write_text("# Graph\n\nNodes and edges from wikilinks.\n", encoding="utf-8")
    (wiki / "retrieval.md").write_text("# Retrieval\n\nHybrid retrieval fuses scores.\n", encoding="utf-8")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    db = str(tmp_path / "search.sqlite")
    rebuild_search_index(db, root=root)
    return root, db


def _paths(results):
    return [r["source_path"] for r in results]


def _retract(root):
    (root / "wiki" / "claims" / "clm_aaaa.md").write_text(_claim("retracted"), encoding="utf-8")


def test_a_current_claim_is_served(store):
    root, db = store
    assert "wiki/claims/clm_aaaa.md" in _paths(search_hybrid(QUERY, 10, db_path=db, hide_root=root))


@pytest.mark.parametrize("status", ["superseded", "retracted", "orphaned", "erased"])
def test_a_claim_made_noncurrent_after_indexing_is_hidden_at_once(store, status):
    root, db = store
    (root / "wiki" / "claims" / "clm_aaaa.md").write_text(_claim(status), encoding="utf-8")
    assert "wiki/claims/clm_aaaa.md" not in _paths(search_hybrid(QUERY, 10, db_path=db, hide_root=root))


def test_the_result_is_refilled_to_limit(store):
    root, db = store
    before = search_hybrid(QUERY, 10, db_path=db)
    assert _paths(before)[0] == "wiki/claims/clm_aaaa.md", "fixture: the claim must rank first"
    _retract(root)
    after = search_hybrid(QUERY, 2, db_path=db, hide_root=root)
    expected = [r for r in before if r["source_path"] != "wiki/claims/clm_aaaa.md"][:2]
    assert [r["chunk_id"] for r in after] == [r["chunk_id"] for r in expected]
    assert len(after) == 2


def test_a_snapshot_search_is_unchanged(store):
    """No `hide_root`: the eval and Z1b measure frozen bytes, never the live wiki."""
    root, db = store
    before = search_hybrid(QUERY, 10, db_path=db)
    _retract(root)
    assert search_hybrid(QUERY, 10, db_path=db) == before


def test_search_and_context_over_a_live_store_hide_it(store):
    root, db = store
    _retract(root)
    handle = StoreHandle("t", root, db, root / "graph.sqlite")
    assert "wiki/claims/clm_aaaa.md" not in _paths(_search_internal(QUERY, True, 10, store=handle))


def test_keyword_mode_hides_it(store):
    """Keyword mode scans live files: a non-current claim never belonged in it."""
    root, db = store
    handle = StoreHandle("t", root, db, root / "graph.sqlite")
    assert "wiki/claims/clm_aaaa.md" in _paths(_search_internal("zetaword", False, 10, store=handle))
    _retract(root)
    assert "wiki/claims/clm_aaaa.md" not in _paths(_search_internal("zetaword", False, 10, store=handle))


def test_a_legacy_claim_page_is_never_hidden(store):
    root, db = store
    (root / "wiki" / "claims" / "clm_aaaa.md").write_text(
        "---\ntype: claim\nstatus: retracted\n---\n\n# Claim\nzetaword fact\n", encoding="utf-8")
    assert "wiki/claims/clm_aaaa.md" in _paths(search_hybrid(QUERY, 10, db_path=db, hide_root=root))


def test_the_cli_hint_no_longer_says_search_lags_for_a_hidden_claim():
    from scripts.claim_cli import STALE_HINT
    assert "hide a claim that stopped being current at once" in STALE_HINT
    assert "new or restored claim" in STALE_HINT and "graph rebuild" in STALE_HINT
