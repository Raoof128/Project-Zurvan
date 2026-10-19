//! Test-only fixture builders: throwaway stores shaped like the real ones
//! (`scripts/rebuild_search_index.py` / `graph_schema.py` / the ledger), so no
//! test ever opens the live store. These are the only write-mode SQLite opens
//! in the crate, and they exist only under `cfg(test)`.

use rusqlite::{params, Connection};
use std::path::Path;

pub struct Chunk<'a> {
    pub chunk_id: &'a str,
    pub source_path: &'a str,
    pub heading: Option<&'a str>,
    pub text: &'a str,
    pub indexed_at: &'a str,
    pub vector: Vec<f32>,
}

pub fn search_db(path: &Path, provider: &str, model: &str, chunks: &[Chunk], meta: &[(&str, &str)]) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let c = Connection::open(path).unwrap();
    c.execute_batch(
        "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, source_path TEXT, heading TEXT, text TEXT, content_hash TEXT, indexed_at TEXT);
         CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id, heading, text, content='chunks', content_rowid='rowid', tokenize='porter unicode61');
         CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, model TEXT, dimension INTEGER, vector BLOB);
         CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    )
    .unwrap();
    for ch in chunks {
        c.execute(
            "INSERT INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at) VALUES (?,?,?,?,?,?)",
            params![ch.chunk_id, ch.source_path, ch.heading, ch.text, "h", ch.indexed_at],
        )
        .unwrap();
        let blob: Vec<u8> = ch.vector.iter().flat_map(|f| f.to_le_bytes()).collect();
        c.execute(
            "INSERT INTO embeddings (chunk_id, provider, model, dimension, vector) VALUES (?,?,?,?,?)",
            params![ch.chunk_id, provider, model, ch.vector.len() as i64, blob],
        )
        .unwrap();
    }
    c.execute_batch("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild');").unwrap();
    for (k, v) in meta {
        c.execute("INSERT INTO index_meta (key, value) VALUES (?,?)", params![k, v]).unwrap();
    }
}

pub fn graph_db(path: &Path, nodes: &[(&str, &str, Option<&str>, Option<&str>)], edges: &[(&str, &str, &str)]) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let c = Connection::open(path).unwrap();
    c.execute_batch(
        "CREATE TABLE nodes (node_id TEXT PRIMARY KEY, node_type TEXT, title TEXT, path TEXT, slug TEXT, content_hash TEXT, tags TEXT, created_at TEXT, updated_at TEXT);
         CREATE TABLE edges (edge_id TEXT PRIMARY KEY, from_node_id TEXT, to_node_id TEXT, edge_type TEXT, evidence TEXT, source_path TEXT, created_at TEXT);
         CREATE INDEX idx_from_node ON edges(from_node_id); CREATE INDEX idx_to_node ON edges(to_node_id); CREATE INDEX idx_node_path ON nodes(path);
         CREATE TABLE graph_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    )
    .unwrap();
    for (id, ty, title, p) in nodes {
        c.execute(
            "INSERT INTO nodes (node_id, node_type, title, path, slug) VALUES (?,?,?,?,?)",
            params![id, ty, title, p, id],
        )
        .unwrap();
    }
    for (i, (from, to, ty)) in edges.iter().enumerate() {
        c.execute(
            "INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type) VALUES (?,?,?,?)",
            params![format!("e{i}"), from, to, ty],
        )
        .unwrap();
    }
}

pub fn registry_db(path: &Path, revision: i64, state: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let c = Connection::open(path).unwrap();
    c.execute_batch(
        "CREATE TABLE knowledge_state (id INTEGER PRIMARY KEY CHECK (id = 1), current_revision INTEGER NOT NULL, state TEXT NOT NULL, atomicity TEXT NOT NULL, manifest_digest TEXT NOT NULL);",
    )
    .unwrap();
    c.execute(
        "INSERT INTO knowledge_state VALUES (1, ?, ?, 'MANAGED', 'd')",
        params![revision, state],
    )
    .unwrap();
}
