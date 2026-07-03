import sqlite3
import json
import os
import math
from typing import List, Dict
from scripts.embed import get_embedding

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return dot / (norm_v1 * norm_v2)

def _index_embedding_config(cursor) -> tuple:
    """Return the (provider, model) the index was built with, or (None, None).

    The index is the source of truth for query-time embedding: mixing an index
    built with one provider and queries embedded with another produces
    meaningless similarity scores, so the query always follows the index.
    """
    try:
        row = cursor.execute("SELECT provider, model FROM embeddings LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (None, None)
    except sqlite3.Error:
        return (None, None)


def search_hybrid(query: str, limit: int = 10, db_path: str | None = None) -> List[Dict]:
    from scripts.config import PROJECT_ROOT
    if db_path is None:
        db_path = str(PROJECT_ROOT / "data" / "search.sqlite")
    if not os.path.exists(db_path):
        print("Error: Search index not found. Run 'zurvan index rebuild' first.")
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Keyword search using FTS5 (BM25 rank)
    import re
    terms = [t for t in re.split(r'\W+', query) if t]
    if terms:
        # Each term is quoted as an FTS5 string: identical matching for plain
        # tokens, but bareword keywords (AND/OR/NOT/NEAR) no longer raise
        # "fts5: syntax error".
        fts_query = " OR ".join(f'"{t}"' for t in terms)
        cursor.execute("""
            SELECT chunk_id, bm25(chunks_fts) as bm25_score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
        """, (fts_query,))
        fts_results = cursor.fetchall()
    else:
        fts_results = []
    
    # Normalize BM25 scores (lower bm25 value is better in SQLite FTS5)
    # Actually SQLite bm25 returns negative values. Let's invert and normalize.
    fts_scores = {}
    if fts_results:
        min_score = min(row[1] for row in fts_results)
        max_score = max(row[1] for row in fts_results)
        for chunk_id, score in fts_results:
            # Normalize between 0 and 1
            if max_score == min_score:
                fts_scores[chunk_id] = 1.0
            else:
                # since scores are typically negative, smaller is better.
                # mapping max_score (worst) -> 0, min_score (best) -> 1
                norm_score = (max_score - score) / (max_score - min_score)
                fts_scores[chunk_id] = norm_score

    # Semantic search — embed the query with the SAME provider/model the
    # index was built with (never the env), so scores stay meaningful.
    index_provider, index_model = _index_embedding_config(cursor)
    query_emb = get_embedding(query, provider=index_provider, model=index_model)['vector']
    
    cursor.execute("SELECT c.chunk_id, c.source_path, c.heading, c.text, e.vector FROM chunks c JOIN embeddings e ON c.chunk_id = e.chunk_id")
    all_chunks = cursor.fetchall()
    
    conn.close()
    
    results = []
    for chunk_id, source_path, heading, text, vector_json in all_chunks:
        chunk_vec = json.loads(vector_json)
        semantic_score = cosine_similarity(query_emb, chunk_vec)
        
        # Base semantic score mapped [0,1], cosine is usually [-1, 1], adjust safely
        semantic_score = max(0.0, (semantic_score + 1.0) / 2.0)
        
        keyword_score = fts_scores.get(chunk_id, 0.0)
        
        hybrid_score = (0.6 * keyword_score) + (0.4 * semantic_score)
        
        # Only include if there is some relevance
        if hybrid_score > 0.2:
            results.append({
                "chunk_id": chunk_id,
                "source_path": source_path,
                "heading": heading,
                "text": text,
                "keyword_score": keyword_score,
                "semantic_score": semantic_score,
                "hybrid_score": hybrid_score
            })
            
    # Sort descending
    results.sort(key=lambda x: x['hybrid_score'], reverse=True)
    return results[:limit]
