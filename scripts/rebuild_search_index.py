import sqlite3
import os
import json
from scripts.chunk import chunk_all_markdown
from scripts.embed import get_embedding
from scripts.config import PROJECT_ROOT

def _harvest_reusable_embeddings(db_path: str) -> dict:
    """Return {chunk_id: (provider, model, dimension, vector_json)} from an
    existing index, or {} when there is none. chunk_id is a hash of
    path+heading+text, so an unchanged chunk_id implies identical content and
    its stored embedding can be reused instead of recomputed."""
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT chunk_id, provider, model, dimension, vector FROM embeddings"
        ).fetchall()
        conn.close()
        return {row[0]: row[1:] for row in rows}
    except sqlite3.Error:
        return {}


def rebuild_search_index(db_path: str | None = None):
    if db_path is None:
        os.makedirs(str(PROJECT_ROOT / "data"), exist_ok=True)
        db_path = str(PROJECT_ROOT / "data" / "search.sqlite")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    # One probe identifies the active provider/model so stored embeddings are
    # only reused when they match the current configuration.
    probe = get_embedding("zurvan embedding provider probe")
    current_provider_model = (probe["provider"], probe["model"])
    reusable = _harvest_reusable_embeddings(db_path)

    # Reset DB
    if os.path.exists(db_path):
        os.remove(db_path)
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create chunks table
    cursor.execute("""
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            source_path TEXT,
            heading TEXT,
            text TEXT,
            content_hash TEXT,
            indexed_at TEXT
        )
    """)
    
    # Create FTS5 table. Porter stemming closes the documented lexical gap
    # (R1B miss analysis: query "citations" scored kw=0.000 against heading
    # "Citation") — plural/inflected query terms now match their stems.
    # R4a ranking change, documented with before/after evals in CHANGELOG.md.
    cursor.execute("""
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, heading, text, content='chunks', content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    
    # Create embeddings table
    cursor.execute("""
        CREATE TABLE embeddings (
            chunk_id TEXT PRIMARY KEY,
            provider TEXT,
            model TEXT,
            dimension INTEGER,
            vector TEXT,
            FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
        )
    """)
    
    chunks = chunk_all_markdown()
    print(f"Found {len(chunks)} chunks to index.")

    reused = 0
    computed = 0
    for c in chunks:
        # Insert chunk — OR IGNORE handles identical content in multiple wiki files
        cursor.execute("""
            INSERT OR IGNORE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (c['chunk_id'], c['source_path'], c['heading'], c['text'], c['content_hash'], c['indexed_at']))
        if cursor.rowcount == 0:
            continue

        # Insert FTS
        cursor.execute("""
            INSERT INTO chunks_fts (rowid, chunk_id, heading, text)
            VALUES (last_insert_rowid(), ?, ?, ?)
        """, (c['chunk_id'], c['heading'], c['text']))

        # Insert Embedding — reuse the stored vector for unchanged chunks
        # (embedding is the expensive step with real providers).
        prior = reusable.get(c['chunk_id'])
        if prior is not None and (prior[0], prior[1]) == current_provider_model:
            provider, model, dimension, vector_json = prior
            reused += 1
        else:
            emb = get_embedding(c['text'])
            provider, model, dimension, vector_json = (
                emb['provider'], emb['model'], emb['dimension'], json.dumps(emb['vector'])
            )
            computed += 1
        cursor.execute("""
            INSERT INTO embeddings (chunk_id, provider, model, dimension, vector)
            VALUES (?, ?, ?, ?, ?)
        """, (c['chunk_id'], provider, model, dimension, vector_json))

    conn.commit()
    conn.close()

    print(f"Search index successfully rebuilt. Embeddings reused: {reused}, computed: {computed}.")

if __name__ == "__main__":
    rebuild_search_index()
