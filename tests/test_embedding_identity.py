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


# --- review follow-up (a): vector reuse must require the FULL identity -------------------------------------------
# The index stamps the probe's full EmbeddingIdentity (F4), but rebuild reuse compared only provider+model, so a
# prior index whose pooling / normalisation / sequence length / dimension differed had its vectors carried into a
# new index stamped as the new space. Reuse is now gated on the same comparison `require_space` applies.

_POOLED = {"max_seq_length": 256, "pooling": "mean", "normalize": True}


def _two_page_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "wiki").mkdir(parents=True)
    (corpus / "wiki" / "a.md").write_text("# A\n\nalpha beta\n")
    (corpus / "wiki" / "b.md").write_text("# B\n\ngamma delta\n")
    return corpus


def _prior_mock_index(tmp_path, monkeypatch, *, stamp=None, corrupt_stamp=None, row_dimension=None):
    """A throwaway mock index (never a live store), optionally relabelled afterwards."""
    import os

    from scripts.rebuild_search_index import rebuild_search_index
    corpus = _two_page_corpus(tmp_path)
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    db = str(tmp_path / "search.sqlite")
    rebuild_search_index(db_path=db, root=corpus)
    os.chmod(db, 0o644)   # publication leaves it 0444; this is a throwaway fixture
    conn = sqlite3.connect(db)
    if stamp is not None:
        conn.execute("UPDATE index_meta SET value=? WHERE key='embedding_identity'", (stamp.to_json(),))
    if corrupt_stamp is not None:
        conn.execute("UPDATE index_meta SET value=? WHERE key='embedding_identity'", (corrupt_stamp,))
    if row_dimension is not None:
        conn.execute("UPDATE embeddings SET dimension=?", (row_dimension,))
    conn.commit()
    conn.close()
    return db, corpus


def _probe_reports(monkeypatch, identity):
    """Make the rebuild's embedder report `identity` (the vectors stay mock)."""
    import scripts.rebuild_search_index as rsi
    real = embed.get_embedding

    def fake(text, provider=None, model=None):
        return dict(real(text, provider=provider, model=model), identity=identity)

    monkeypatch.setattr(rsi, "get_embedding", fake)


def test_rebuild_reuses_every_vector_when_the_full_identity_matches(tmp_path, monkeypatch):
    # Positive control: without it the next tests could pass because reuse is simply broken.
    from scripts.rebuild_search_index import rebuild_search_index
    db, corpus = _prior_mock_index(tmp_path, monkeypatch)
    out = rebuild_search_index(db_path=db, root=corpus)
    assert out["reused"] == out["chunks"] > 0 and out["computed"] == 0


def test_rebuild_re_embeds_when_a_pinned_identity_field_differs(tmp_path, monkeypatch):
    from scripts.rebuild_search_index import rebuild_search_index
    prior = EmbeddingIdentity("mock", "deterministic_hash", 384, 256, "cls", True)
    db, corpus = _prior_mock_index(tmp_path, monkeypatch, stamp=prior)
    _probe_reports(monkeypatch, EmbeddingIdentity("mock", "deterministic_hash", 384, 256, "mean", True))
    out = rebuild_search_index(db_path=db, root=corpus)
    assert out["reused"] == 0 and out["computed"] == out["chunks"] > 0, out


def test_rebuild_reuses_when_only_one_side_pins_an_optional_field(tmp_path, monkeypatch):
    # The same rule require_space applies: an optional field is compared only when BOTH sides have it, so an index
    # stamped before the field was recorded keeps its vectors.
    from scripts.rebuild_search_index import rebuild_search_index
    db, corpus = _prior_mock_index(tmp_path, monkeypatch)       # stamp has no pooling etc.
    _probe_reports(monkeypatch, EmbeddingIdentity("mock", "deterministic_hash", 384, **_POOLED))
    out = rebuild_search_index(db_path=db, root=corpus)
    assert out["reused"] == out["chunks"] > 0


def test_rebuild_re_embeds_rows_whose_stored_dimension_differs(tmp_path, monkeypatch):
    # Reuse compared provider+model only; a row labelled with another dimension was carried over and then tripped
    # the width guard (ValueError) mid-build instead of being recomputed.
    from scripts.rebuild_search_index import rebuild_search_index
    db, corpus = _prior_mock_index(tmp_path, monkeypatch, row_dimension=768)
    out = rebuild_search_index(db_path=db, root=corpus)
    assert out["reused"] == 0 and out["computed"] == out["chunks"] > 0, out


def test_rebuild_with_a_corrupt_prior_identity_stamp_re_embeds_and_says_why(tmp_path, monkeypatch, capsys):
    from scripts.rebuild_search_index import read_index_meta, rebuild_search_index
    db, corpus = _prior_mock_index(tmp_path, monkeypatch, corrupt_stamp="{not json")
    out = rebuild_search_index(db_path=db, root=corpus)
    assert out["reused"] == 0 and out["computed"] == out["chunks"] > 0, out
    assert "embedding_identity" in capsys.readouterr().err
    # ...and the replacement carries a good stamp again.
    assert EmbeddingIdentity.from_json(read_index_meta(db)["embedding_identity"]).dimension == 384


@pytest.mark.parametrize("bad", ["{not json", "[]", "null", '{"provider": "mock"}', '{"nope": 1}', ""])
def test_corrupt_identity_json_is_a_typed_error(bad):
    from scripts.embed import EmbeddingIdentityCorrupt
    with pytest.raises(EmbeddingIdentityCorrupt, match="embedding_identity"):
        EmbeddingIdentity.from_json(bad)
    assert issubclass(EmbeddingIdentityCorrupt, EmbeddingSpaceMismatch)   # the CLI and MCP already handle that


def test_hybrid_query_on_a_corrupt_identity_stamp_raises_the_typed_error_not_json_decode(tmp_path, monkeypatch):
    from scripts.embed import EmbeddingIdentityCorrupt
    from scripts.hybrid_search import search_hybrid
    db, _ = _prior_mock_index(tmp_path, monkeypatch, corrupt_stamp="{not json")
    with pytest.raises(EmbeddingIdentityCorrupt):
        search_hybrid("alpha", limit=3, db_path=db)
