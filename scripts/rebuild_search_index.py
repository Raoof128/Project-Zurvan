import sqlite3
import os
import json
from scripts.chunk import chunk_all_markdown
from scripts.embed import get_embedding
from scripts.config import PROJECT_ROOT

def rebuild_search_index():
    os.makedirs(str(PROJECT_ROOT / "data"), exist_ok=True)
    db_path = str(PROJECT_ROOT / "data" / "search.sqlite")
    
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
    
    # Create FTS5 table
    cursor.execute("""
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, heading, text, content='chunks', content_rowid='rowid'
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

        # Insert Embedding
        emb = get_embedding(c['text'])
        cursor.execute("""
            INSERT INTO embeddings (chunk_id, provider, model, dimension, vector)
            VALUES (?, ?, ?, ?, ?)
        """, (c['chunk_id'], emb['provider'], emb['model'], emb['dimension'], json.dumps(emb['vector'])))
        
    conn.commit()
    conn.close()
    
    print("Search index successfully rebuilt.")

if __name__ == "__main__":
    rebuild_search_index()
