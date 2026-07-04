import pytest
import sqlite3
import os
import json
from scripts.hybrid_search import cosine_similarity, search_hybrid
from scripts.rebuild_search_index import rebuild_search_index


@pytest.fixture(scope="module")
def tmp_index(tmp_path_factory):
    """A search index built into a temp DB so tests never touch (or downgrade)
    the real data/search.sqlite.

    The mock provider is pinned for the build regardless of the ambient
    environment: the repo's own `.claude/settings.json` exports
    ``ZURVAN_EMBED_PROVIDER=sentence_transformers`` for agent sessions, which
    would otherwise make this fixture build a real (slow, non-deterministic)
    index and break the provider-follows-index assertion."""
    db_path = str(tmp_path_factory.mktemp("index") / "search.sqlite")
    saved = {k: os.environ.get(k) for k in ("ZURVAN_EMBED_PROVIDER", "ZURVAN_EMBED_MODEL")}
    os.environ["ZURVAN_EMBED_PROVIDER"] = "mock"
    os.environ["ZURVAN_EMBED_MODEL"] = "mock_model"
    try:
        rebuild_search_index(db_path)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return db_path


def _insert_chunk(db_path, chunk_id, heading, text):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (chunk_id, "wiki/test.md", heading, text, "hash", "now"),
    )
    cursor.execute(
        "INSERT INTO chunks_fts (rowid, chunk_id, heading, text) VALUES (last_insert_rowid(), ?, ?, ?)",
        (chunk_id, heading, text),
    )
    from scripts.embed import get_embedding
    vec = get_embedding(text)["vector"]
    cursor.execute(
        "INSERT INTO embeddings (chunk_id, provider, model, dimension, vector) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, "mock", "deterministic_hash", len(vec), json.dumps(vec)),
    )
    conn.commit()
    conn.close()


def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([1, 0], [-1, 0]) == -1.0
    assert cosine_similarity([0, 0], [0, 0]) == 0.0


def test_search_hybrid(tmp_index):
    chunk_id = "test_chunk_unique_999"
    _insert_chunk(tmp_index, chunk_id, "Test", "vector reliability test data XYZ999")

    results = search_hybrid("XYZ999", db_path=tmp_index)

    assert len(results) >= 1
    assert any(r['chunk_id'] == chunk_id for r in results)


def test_search_hybrid_survives_fts5_keywords(tmp_index):
    # Regression: unquoted terms were joined into the MATCH expression, so a
    # query containing a bareword FTS5 keyword (AND/OR/NOT/NEAR) raised
    # sqlite3.OperationalError: fts5 syntax error.
    for query in ("search AND rescue", "NOT this", "what is NEAR the graph", "OR"):
        results = search_hybrid(query, limit=3, db_path=tmp_index)
        assert isinstance(results, list)


def test_search_hybrid_handles_symbol_only_query(tmp_index):
    # No word characters at all — must not crash on an empty term list.
    assert isinstance(search_hybrid("!!! ???", limit=3, db_path=tmp_index), list)


def test_rebuild_reuses_embeddings_for_unchanged_chunks(tmp_path, monkeypatch):
    # Incremental rebuild: embeddings for unchanged chunk_ids come from the
    # previous index; only new/changed chunks (plus one provider probe) are
    # embedded. With a real provider this is the difference between seconds
    # and a full re-embed of the corpus.
    import scripts.rebuild_search_index as rsi
    from scripts.embed import get_embedding as real_get_embedding

    db_path = str(tmp_path / "search.sqlite")
    rebuild_search_index(db_path)  # warm index

    calls = {"n": 0}
    def counting_get_embedding(text):
        calls["n"] += 1
        return real_get_embedding(text)

    monkeypatch.setattr(rsi, "get_embedding", counting_get_embedding)
    rebuild_search_index(db_path)

    assert calls["n"] == 1  # the provider probe only — zero chunk re-embeds


def test_fts_porter_stemming_matches_inflected_terms(tmp_index):
    # R4a: the porter tokenizer stems query and index terms, so a plural
    # query matches singular content (the one genuine lexical miss in the
    # R1B analysis: "citations" vs heading "Citation").
    chunk_id = "stem_test_chunk_777"
    _insert_chunk(
        tmp_index, chunk_id, "Citation Rules ZQSTEM777",
        "## Citation Rules ZQSTEM777\nEvery citation needs a source.",
    )

    # Plural query against singular indexed text: keyword score must be > 0.
    results = search_hybrid("citations ZQSTEM777", limit=5, db_path=tmp_index)
    hit = next((r for r in results if r["chunk_id"] == chunk_id), None)
    assert hit is not None
    assert hit["keyword_score"] > 0.0


def test_query_embedding_follows_index_provider(tmp_index, monkeypatch):
    # The index is the source of truth: even if the env names a different
    # provider, queries embed with the provider/model stored in the index —
    # mixing providers would make cosine scores meaningless.
    import scripts.hybrid_search as hs
    from scripts.embed import get_embedding as real_get_embedding

    captured = {}
    def spying_get_embedding(text, provider=None, model=None):
        captured["provider"] = provider
        captured["model"] = model
        return real_get_embedding(text, provider="mock", model="deterministic_hash")

    monkeypatch.setattr(hs, "get_embedding", spying_get_embedding)
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "sentence_transformers")

    search_hybrid("anything", limit=1, db_path=tmp_index)

    assert captured["provider"] == "mock"              # from the index,
    assert captured["model"] == "deterministic_hash"   # not from the env
