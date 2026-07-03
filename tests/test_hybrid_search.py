import pytest
import sqlite3
import os
import json
from scripts.hybrid_search import cosine_similarity, search_hybrid
from scripts.rebuild_search_index import rebuild_search_index

def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([1, 0], [-1, 0]) == -1.0
    assert cosine_similarity([0, 0], [0, 0]) == 0.0

def test_search_hybrid(monkeypatch):
    # 1. First rebuild index to ensure table exists
    rebuild_search_index()
    
    # Insert dummy data into SQLite directly for reliable testing
    db_path = "data/search.sqlite"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    chunk_id = "test_chunk_unique_999"
    text = "vector reliability test data XYZ999"
    
    cursor.execute("INSERT OR REPLACE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (chunk_id, "wiki/test.md", "Test", text, "hash", "now"))
    cursor.execute("INSERT INTO chunks_fts (rowid, chunk_id, heading, text) VALUES (last_insert_rowid(), ?, ?, ?)",
                   (chunk_id, "Test", text))
                   
    from scripts.embed import get_embedding
    vec = get_embedding(text)['vector']
    cursor.execute("INSERT INTO embeddings (chunk_id, provider, model, dimension, vector) VALUES (?, ?, ?, ?, ?)",
                   (chunk_id, "mock", "mock", len(vec), json.dumps(vec)))
    conn.commit()
    conn.close()
    
    # 3. Test search
    results = search_hybrid("XYZ999")

    assert len(results) >= 1
    assert any(r['chunk_id'] == chunk_id for r in results)


def test_search_hybrid_survives_fts5_keywords():
    # Regression: unquoted terms were joined into the MATCH expression, so a
    # query containing a bareword FTS5 keyword (AND/OR/NOT/NEAR) raised
    # sqlite3.OperationalError: fts5 syntax error.
    for query in ("search AND rescue", "NOT this", "what is NEAR the graph", "OR"):
        results = search_hybrid(query, limit=3)
        assert isinstance(results, list)


def test_search_hybrid_handles_symbol_only_query():
    # No word characters at all — must not crash on an empty term list.
    assert isinstance(search_hybrid("!!! ???", limit=3), list)


def test_rebuild_reuses_embeddings_for_unchanged_chunks(monkeypatch):
    # Incremental rebuild: embeddings for unchanged chunk_ids come from the
    # previous index; only new/changed chunks (plus one provider probe) are
    # embedded. With a real provider this is the difference between seconds
    # and a full re-embed of the corpus.
    import scripts.rebuild_search_index as rsi
    from scripts.embed import get_embedding as real_get_embedding

    rebuild_search_index()  # warm index

    calls = {"n": 0}
    def counting_get_embedding(text):
        calls["n"] += 1
        return real_get_embedding(text)

    monkeypatch.setattr(rsi, "get_embedding", counting_get_embedding)
    rebuild_search_index()

    assert calls["n"] == 1  # the provider probe only — zero chunk re-embeds


def test_fts_porter_stemming_matches_inflected_terms():
    # R4a: the porter tokenizer stems query and index terms, so a plural
    # query matches singular content (the one genuine lexical miss in the
    # R1B analysis: "citations" vs heading "Citation").
    rebuild_search_index()

    conn = sqlite3.connect("data/search.sqlite")
    cursor = conn.cursor()
    chunk_id = "stem_test_chunk_777"
    text = "## Citation Rules ZQSTEM777\nEvery citation needs a source."
    cursor.execute(
        "INSERT OR REPLACE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (chunk_id, "wiki/stem-test.md", "Citation Rules ZQSTEM777", text, "hash", "now"),
    )
    cursor.execute(
        "INSERT INTO chunks_fts (rowid, chunk_id, heading, text) VALUES (last_insert_rowid(), ?, ?, ?)",
        (chunk_id, "Citation Rules ZQSTEM777", text),
    )
    from scripts.embed import get_embedding
    vec = get_embedding(text)["vector"]
    cursor.execute(
        "INSERT INTO embeddings (chunk_id, provider, model, dimension, vector) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, "mock", "deterministic_hash", len(vec), json.dumps(vec)),
    )
    conn.commit()
    conn.close()

    # Plural query against singular indexed text: keyword score must be > 0.
    results = search_hybrid("citations ZQSTEM777", limit=5)
    hit = next((r for r in results if r["chunk_id"] == chunk_id), None)
    assert hit is not None
    assert hit["keyword_score"] > 0.0
