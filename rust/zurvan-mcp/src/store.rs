//! Read-only access to the published stores and their metadata, computed once
//! per store GENERATION (the inode an atomic promote replaces).
//!
//! Every open is `mode=ro` without CREATE, so a missing store is an error and
//! never a new 0-byte file. (Python's `graph_query.get_stats` opens read-write
//! and does create one — measured 2026-10-19; IOU-10 in the design.)

use crate::config::Config;
use rusqlite::{Connection, OpenFlags};
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

pub fn open_ro(path: &Path) -> rusqlite::Result<Connection> {
    let p = path.to_string_lossy().replace('%', "%25").replace('?', "%3f").replace('#', "%23");
    Connection::open_with_flags(
        format!("file:{p}?mode=ro"),
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
}

/// What an atomic promote changes: a new inode (and usually size/mtime).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Generation {
    pub dev: u64,
    pub ino: u64,
    pub size: u64,
    pub mtime_ns: i128,
}

pub fn generation(path: &Path) -> std::io::Result<Generation> {
    let m = std::fs::metadata(path)?;
    Ok(Generation {
        dev: m.dev(),
        ino: m.ino(),
        size: m.size(),
        mtime_ns: m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct IndexMeta {
    pub generation: Generation,
    pub chunks: i64,
    pub files: i64,
    pub provider: Option<String>,
    pub model: Option<String>,
    pub max_indexed_at: Option<String>,
    pub built_from_revision: Option<i64>,
    pub built_at: Option<String>,
    pub built_from_manifest: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GraphMeta {
    pub generation: Generation,
    pub nodes: i64,
    pub edges: i64,
    pub built_from_revision: Option<i64>,
}

fn meta_value(conn: &Connection, table: &str, key: &str) -> Option<String> {
    conn.query_row(&format!("SELECT value FROM {table} WHERE key = ?"), [key], |r| r.get(0)).ok()
}

/// One pass over `chunks` (the 1.2 GB table has no index on `indexed_at`, so
/// `MAX(indexed_at)` alone was a 5.5 s full scan in Python's status).
fn compute_index(path: &Path, generation: Generation) -> Result<IndexMeta, String> {
    let conn = open_ro(path).map_err(|e| e.to_string())?;
    let (chunks, files, max_indexed_at): (i64, i64, Option<String>) = conn
        .query_row("SELECT count(*), count(DISTINCT source_path), MAX(indexed_at) FROM chunks", [], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })
        .map_err(|e| e.to_string())?;
    let row: Option<(Option<String>, Option<String>)> = conn
        .query_row("SELECT provider, model FROM embeddings LIMIT 1", [], |r| Ok((r.get(0)?, r.get(1)?)))
        .map(Some)
        .or_else(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => Ok(None),
            other => Err(other.to_string()),
        })?;
    let (provider, model) = row.unwrap_or((None, None));
    Ok(IndexMeta {
        generation,
        chunks,
        files,
        provider,
        model,
        max_indexed_at,
        built_from_revision: meta_value(&conn, "index_meta", "built_from_revision").and_then(|v| v.parse().ok()),
        built_at: meta_value(&conn, "index_meta", "built_at"),
        built_from_manifest: meta_value(&conn, "index_meta", "built_from_manifest"),
    })
}

fn compute_graph(path: &Path, generation: Generation) -> Result<GraphMeta, String> {
    let conn = open_ro(path).map_err(|e| e.to_string())?;
    let nodes: i64 = conn.query_row("SELECT COUNT(*) FROM nodes", [], |r| r.get(0)).map_err(|e| e.to_string())?;
    let edges: i64 = conn.query_row("SELECT COUNT(*) FROM edges", [], |r| r.get(0)).map_err(|e| e.to_string())?;
    Ok(GraphMeta {
        generation,
        nodes,
        edges,
        built_from_revision: meta_value(&conn, "graph_meta", "built_from_revision").and_then(|v| v.parse().ok()),
    })
}

/// Python's messages for a graph store it cannot read, without its side
/// effect: with `data/` present Python creates the file and then finds no
/// table; without `data/` it cannot open the path at all.
fn graph_missing_message(path: &Path) -> String {
    if path.parent().is_some_and(|p| p.is_dir()) {
        "no such table: nodes".into()
    } else {
        "unable to open database file".into()
    }
}

#[derive(Default)]
pub struct MetaCache {
    index: Mutex<Option<Arc<IndexMeta>>>,
    graph: Mutex<Option<Arc<GraphMeta>>>,
    computations: AtomicUsize,
    /// Freshness verdict and when it was taken (status.rs, 5 s reuse).
    pub(crate) freshness: Mutex<Option<(std::time::Instant, crate::status::Freshness)>>,
}

impl MetaCache {
    /// How many times metadata was actually computed (tests and status).
    pub fn computations(&self) -> usize {
        self.computations.load(Ordering::SeqCst)
    }

    pub fn index(&self, cfg: &Config) -> Result<Arc<IndexMeta>, String> {
        self.index_at(&cfg.search_db)
    }

    pub fn index_at(&self, path: &Path) -> Result<Arc<IndexMeta>, String> {
        let generation = generation(path).map_err(|_| "unable to open database file".to_string())?;
        let mut slot = self.index.lock().unwrap();
        if let Some(m) = slot.as_ref().filter(|m| m.generation == generation) {
            return Ok(m.clone());
        }
        self.computations.fetch_add(1, Ordering::SeqCst);
        let meta = Arc::new(compute_index(path, generation)?);
        *slot = Some(meta.clone());
        Ok(meta)
    }

    pub fn graph(&self, cfg: &Config) -> Result<Arc<GraphMeta>, String> {
        let generation = generation(&cfg.graph_db).map_err(|_| graph_missing_message(&cfg.graph_db))?;
        let mut slot = self.graph.lock().unwrap();
        if let Some(m) = slot.as_ref().filter(|m| m.generation == generation) {
            return Ok(m.clone());
        }
        self.computations.fetch_add(1, Ordering::SeqCst);
        let meta = Arc::new(compute_graph(&cfg.graph_db, generation)?);
        *slot = Some(meta.clone());
        Ok(meta)
    }
}

/// The graph store opened read-only, or Python's error text for it.
pub fn open_graph(cfg: &Config) -> Result<Connection, String> {
    if !cfg.graph_db.exists() {
        return Err(graph_missing_message(&cfg.graph_db));
    }
    open_ro(&cfg.graph_db).map_err(|e| e.to_string())
}

/// `knowledge_state` of the revision ledger: (current_revision, state).
pub fn ledger(cfg: &Config) -> Option<(i64, String)> {
    let conn = open_ro(&cfg.registry_db).ok()?;
    conn.query_row("SELECT current_revision, state FROM knowledge_state WHERE id = 1", [], |r| {
        Ok((r.get(0)?, r.get(1)?))
    })
    .ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::testutil::{graph_db, registry_db, search_db, Chunk};

    fn chunk<'a>(id: &'a str, path: &'a str) -> Chunk<'a> {
        Chunk { chunk_id: id, source_path: path, heading: None, text: "t", indexed_at: "2026-10-18T19:19:06.089963", vector: vec![0.0; 4] }
    }

    fn cfg_for(root: &std::path::Path) -> Config {
        Config::resolve_from(Some(root.to_path_buf()), |_| None)
    }

    #[test]
    fn meta_cached_per_generation() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = cfg_for(dir.path());
        search_db(&cfg.search_db, "mock", "deterministic_hash",
                  &[chunk("a", "wiki/x.md"), chunk("b", "wiki/x.md"), chunk("c", "wiki/y.md")],
                  &[("built_from_revision", "7"), ("built_at", "2026-10-18T08:23:19.137571+00:00"), ("built_from_manifest", "abc")]);
        let cache = MetaCache::default();
        let m = cache.index(&cfg).unwrap();
        assert_eq!((m.chunks, m.files), (3, 2));
        assert_eq!(m.provider.as_deref(), Some("mock"));
        assert_eq!(m.built_from_revision, Some(7));
        assert_eq!(m.built_from_manifest.as_deref(), Some("abc"));
        assert_eq!(m.max_indexed_at.as_deref(), Some("2026-10-18T19:19:06.089963"));
        let _ = cache.index(&cfg).unwrap();
        assert_eq!(cache.computations(), 1, "a second call on the same generation must not query");

        // An atomic promote: build elsewhere, rename over the published path.
        let next = dir.path().join("next.sqlite");
        search_db(&next, "sentence_transformers", "all-MiniLM-L6-v2", &[chunk("z", "docs/z.md")], &[("built_from_revision", "8")]);
        std::fs::rename(&next, &cfg.search_db).unwrap();
        let m2 = cache.index(&cfg).unwrap();
        assert_eq!(cache.computations(), 2);
        assert_eq!((m2.chunks, m2.files, m2.built_from_revision), (1, 1, Some(8)));
    }

    #[test]
    fn missing_store_is_an_error_not_a_created_file() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = cfg_for(dir.path());
        assert!(MetaCache::default().index(&cfg).is_err());
        assert!(!cfg.search_db.exists(), "a read-only open must never create the index");
        assert!(MetaCache::default().graph(&cfg).is_err());
        assert!(!cfg.graph_db.exists());
    }

    #[test]
    fn graph_meta_and_ledger() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = cfg_for(dir.path());
        graph_db(&cfg.graph_db, &[("n1", "decision", Some("A"), Some("wiki/a.md")), ("n2", "claim", Some("B"), Some("wiki/b.md"))],
                 &[("n1", "n2", "links_to")]);
        let g = MetaCache::default().graph(&cfg).unwrap();
        assert_eq!((g.nodes, g.edges), (2, 1));
        assert_eq!(ledger(&cfg), None);
        registry_db(&cfg.registry_db, 756, "CLEAN");
        assert_eq!(ledger(&cfg), Some((756, "CLEAN".to_string())));
    }
}
