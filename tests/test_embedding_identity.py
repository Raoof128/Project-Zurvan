"""F4 (audit 2026-10-22): a query-time model failure returned deterministic
hash vectors as a successful result, and the hybrid caller scored them against
a MiniLM index without complaint. Failure and identity are typed now."""
import sqlite3
import sys

import pytest

import scripts.embed as embed
from scripts.embed import EmbeddingIdentity, EmbeddingSpaceMismatch, EmbeddingUnavailable, get_embedding, require_space


def _model_unloadable(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # `import` raises
    embed._MODEL_CACHE.clear()


def test_requested_real_provider_failure_raises_not_mock(monkeypatch):
    _model_unloadable(monkeypatch)
    with pytest.raises(EmbeddingUnavailable, match="sentence_transformers"):
        get_embedding("q", provider="sentence_transformers", model="all-MiniLM-L6-v2")


def test_unset_provider_still_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("ZURVAN_EMBED_PROVIDER", raising=False)
    monkeypatch.delenv("ZURVAN_EMBED_MODEL", raising=False)
    r = get_embedding("q")
    assert r["provider"] == "mock"
    assert r["identity"] == EmbeddingIdentity("mock", "deterministic_hash", 384)


def test_identity_round_trips_through_json():
    i = EmbeddingIdentity("sentence_transformers", "all-MiniLM-L6-v2", 384, 256, "mean", True)
    assert EmbeddingIdentity.from_json(i.to_json()) == i


def test_require_space_rejects_wrong_space_and_accepts_missing_optional_fields():
    r = get_embedding("q", provider="mock")
    require_space(r, EmbeddingIdentity("mock", "deterministic_hash", 384))          # optional fields absent: ok
    with pytest.raises(EmbeddingSpaceMismatch):
        require_space(r, EmbeddingIdentity("sentence_transformers", "all-MiniLM-L6-v2", 384))
    with pytest.raises(EmbeddingSpaceMismatch):
        require_space(r, EmbeddingIdentity("mock", "deterministic_hash", 768))


def test_require_space_checks_optional_fields_when_the_index_pins_them():
    r = {"provider": "mock", "model": "deterministic_hash", "dimension": 384,
         "identity": EmbeddingIdentity("mock", "deterministic_hash", 384, 256, "mean", True)}
    require_space(r, EmbeddingIdentity("mock", "deterministic_hash", 384, 256, "mean", True))
    with pytest.raises(EmbeddingSpaceMismatch, match="pooling"):
        require_space(r, EmbeddingIdentity("mock", "deterministic_hash", 384, 256, "cls", True))


def _minilm_labelled_mock_index(tmp_path, monkeypatch):
    """A tmp index built with mock vectors, then RELABELLED as the MiniLM space
    (fixture only — never a live store), with the real model made unloadable."""
    from scripts.rebuild_search_index import rebuild_search_index
    corpus = tmp_path / "corpus"
    (corpus / "wiki").mkdir(parents=True)
    (corpus / "wiki" / "citation.md").write_text("# Citation Rules\n\nEvery citation needs a verbatim quote.\n")
    (corpus / "wiki" / "graph.md").write_text("# Graph\n\nNodes and edges from wikilinks.\n")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    db = str(tmp_path / "search.sqlite")
    rebuild_search_index(db_path=db, root=corpus)
    import os
    os.chmod(db, 0o644)   # publication leaves it 0444; this is a throwaway fixture
    conn = sqlite3.connect(db)
    conn.execute("UPDATE embeddings SET provider='sentence_transformers', model='all-MiniLM-L6-v2'")
    conn.execute("UPDATE index_meta SET value=? WHERE key='embedding_identity'",
                 (EmbeddingIdentity("sentence_transformers", "all-MiniLM-L6-v2", 384).to_json(),))
    conn.commit()
    conn.close()
    _model_unloadable(monkeypatch)
    return db, corpus


def test_hybrid_query_refuses_a_mock_vector_against_a_minilm_index(tmp_path, monkeypatch):
    from scripts.hybrid_search import search_hybrid
    db, _ = _minilm_labelled_mock_index(tmp_path, monkeypatch)
    with pytest.raises(EmbeddingUnavailable):
        search_hybrid("citation", limit=3, db_path=db)


def test_hybrid_query_refuses_a_vector_from_the_wrong_space(tmp_path, monkeypatch):
    # The model loads but is not the index's model: a mismatch, not a fallback.
    from scripts.hybrid_search import search_hybrid
    db, _ = _minilm_labelled_mock_index(tmp_path, monkeypatch)
    monkeypatch.setattr(embed, "get_embedding", lambda text, provider=None, model=None: {
        "provider": "mock", "model": "deterministic_hash", "dimension": 384,
        "vector": embed.generate_mock_embedding(text),
        "identity": EmbeddingIdentity("mock", "deterministic_hash", 384)})
    import scripts.hybrid_search as hs
    monkeypatch.setattr(hs, "get_embedding", embed.get_embedding)
    with pytest.raises(EmbeddingSpaceMismatch):
        search_hybrid("citation", limit=3, db_path=db)


def test_mcp_search_labels_the_keyword_fallback(tmp_path, monkeypatch):
    from scripts.mcp_tools import tool_zurvan_search
    db, corpus = _minilm_labelled_mock_index(tmp_path, monkeypatch)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", db)
    monkeypatch.setattr("scripts.context_export.PROJECT_ROOT", corpus)   # keyword fallback scans the fixture wiki
    out = tool_zurvan_search("citation", hybrid=True, limit=3)
    assert out.startswith("Semantic search unavailable:"), out
    assert "keyword results only" in out
    assert "citation.md" in out


def test_rebuild_stamps_embedding_identity(tmp_path, monkeypatch):
    from scripts.rebuild_search_index import read_index_meta, rebuild_search_index
    corpus = tmp_path / "corpus"
    (corpus / "wiki").mkdir(parents=True)
    (corpus / "wiki" / "a.md").write_text("# A\n\nalpha beta\n")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    db = str(tmp_path / "search.sqlite")
    rebuild_search_index(db_path=db, root=corpus)
    ident = EmbeddingIdentity.from_json(read_index_meta(db)["embedding_identity"])
    assert (ident.provider, ident.model, ident.dimension) == ("mock", "deterministic_hash", 384)
