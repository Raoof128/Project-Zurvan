//! `zurvan_search`: hybrid (FTS5 BM25 + full-scan cosine, fused) and the
//! keyword live scan — ports of `hybrid_search.search_hybrid`,
//! `context_export._search_internal` and `tool_zurvan_search`.

use crate::config::Config;
use crate::corpus::{glob_md, rel};
use crate::embed::{locate_model, mock, Embedder, Space};
use crate::store::{open_at, MetaCache, GENERATION_CHANGED, GENERATION_RETRIES};
use crate::text::{budget, read_universal, snippet, RESULT_CHAR_BUDGET};
use crate::vectors::{Loader, VectorCache, KEYWORD_WEIGHT};
use rayon::prelude::*;
use rusqlite::Connection;
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex, OnceLock};

/// `hybrid_search.MIN_HYBRID_SCORE`; inclusion needs strictly more.
pub const MIN_HYBRID_SCORE: f64 = 0.2;

pub fn passes_floor(score: f64) -> bool {
    score > MIN_HYBRID_SCORE
}

#[derive(Debug, Clone, PartialEq)]
pub enum Score {
    F(f64),
    I(i64),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Hit {
    pub chunk_id: String,
    pub source_path: String,
    pub heading: Option<String>,
    pub text: String,
    pub keyword: f64,
    pub semantic: f64,
    pub score: Score,
}

impl Hit {
    pub fn score_f64(&self) -> f64 {
        match self.score {
            Score::F(f) => f,
            Score::I(i) => i as f64,
        }
    }
}

/// What a search answered from (audit 2026-10-22 F6). Hybrid hits are
/// hydrated from ONE index generation and carry its metadata; keyword hits
/// are read from the live files and carry no index generation at all.
#[derive(Debug, Clone)]
pub enum Served {
    Index(Arc<crate::store::IndexMeta>),
    LiveFiles,
    /// No index at the path: nothing was served (Python prints to stderr
    /// and returns []).
    NoIndex,
}

/// `Ok(Ok((hits, meta)))`: hits and the generation they were hydrated from;
/// `Ok(Err(why))`: semantic search unavailable; `Err(e)`: the call failed.
pub type HybridOutcome = Result<Result<(Vec<Hit>, Arc<crate::store::IndexMeta>), String>, String>;

/// A search response bound to what served it. The stamp is derived from
/// `served`, never from a fresh read of the index path, so a promote landing
/// after hydration cannot label this generation's text as the next one's.
#[derive(Debug, Clone)]
pub struct SearchResult {
    pub hits: Vec<Hit>,
    pub notice: Option<String>,
    pub served: Served,
}

/// Keyword scores by matrix row. Selects `rowid`, never `chunk_id`: on the
/// external-content table the latter costs one `chunks` lookup per matching
/// row (spike: 4.79 s cold vs 0.18 s, identical score set). Min-max is taken
/// over EVERY matched row, as Python does.
pub fn fts_scores(conn: &Connection, cache: &VectorCache, query: &str) -> rusqlite::Result<HashMap<u32, f64>> {
    let Some(m) = fts_match(query) else { return Ok(HashMap::new()) };
    let mut st = conn.prepare_cached("SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ?")?;
    let rows: Vec<(i64, f64)> = st.query_map([m], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
    Ok(normalise_bm25(rows, cache))
}

/// `"t1" OR "t2" ...` from the query's word runs, or None when it has none.
fn fts_match(query: &str) -> Option<String> {
    static TERMS: OnceLock<regex::Regex> = OnceLock::new();
    let re = TERMS.get_or_init(|| regex::Regex::new(r"\W+").unwrap());
    let terms: Vec<String> = re.split(query).filter(|t| !t.is_empty()).map(|t| format!("\"{t}\"")).collect();
    (!terms.is_empty()).then(|| terms.join(" OR "))
}

/// Min-max over EVERY matched row (order-independent), keyed by cache row.
fn normalise_bm25(rows: Vec<(i64, f64)>, cache: &VectorCache) -> HashMap<u32, f64> {
    let mut out = HashMap::new();
    if rows.is_empty() {
        return out;
    }
    let (min, max) = rows.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |a, r| (a.0.min(r.1), a.1.max(r.1)));
    for (rowid, score) in rows {
        let norm = if max == min { 1.0 } else { (max - score) / (max - min) };
        if let Some(row) = cache.row_for_rowid(rowid) {
            out.insert(row, norm);
        }
    }
    out
}

/// How many rowid ranges the hybrid FTS stage runs in parallel. Measured on
/// the live store (70 queries, 10.6M scored rows, all bit-identical):
/// 1 → 87/134 ms p50/p95, 2 → 49/75, 3 → 37/58, 4 → 30/46.
pub const FTS_PARTS: usize = 4;
/// SQLite memory-mapped I/O for the pool's read-only connections
/// (sqlite.org/mmap.html). Safe here: a published index is never written in
/// place (promotes are `os.replace`, the file is 0444), so the mapped inode
/// cannot change; bundled SQLite clamps it to its compiled maximum.
const FTS_MMAP: &str = "PRAGMA mmap_size=2147418112;";

/// Persistent read-only connections to ONE index generation, each owning a
/// rowid range, so the FTS5 query runs in parallel. Every row's bm25 is
/// identical to the unpartitioned query: FTS5's bm25 takes IDF from
/// `xQueryPhrase` and avgdl from `xRowCount`/`xColumnTotalSize`, all of which
/// cover the whole table regardless of the outer query's rowid constraint
/// (sqlite.org/fts5.html §custom auxiliary functions; ext/fts5/fts5_aux.c).
pub struct FtsPool {
    pub generation: crate::store::Generation,
    parts: Vec<(Mutex<Connection>, i64, i64)>,
}

impl FtsPool {
    pub fn open(path: &Path, generation: crate::store::Generation, parts: usize) -> Result<FtsPool, String> {
        let parts = parts.max(1);
        let first = open_at(path, generation)?;
        first.execute_batch(FTS_MMAP).map_err(|e| e.to_string())?;
        let (lo, hi): (Option<i64>, Option<i64>) = first
            .query_row("SELECT min(rowid), max(rowid) FROM chunks", [], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(|e| e.to_string())?;
        // The outer bounds are open-ended, so every rowid lands in a range.
        let bounds: Vec<(i64, i64)> = match (lo, hi) {
            (Some(lo), Some(hi)) if parts > 1 && hi > lo => {
                let span = (hi - lo + 1) as i128;
                (0..parts as i128)
                    .map(|i| {
                        let a = if i == 0 { i64::MIN } else { lo + (span * i / parts as i128) as i64 };
                        let b = if i == parts as i128 - 1 { i64::MAX } else { lo + (span * (i + 1) / parts as i128) as i64 - 1 };
                        (a, b)
                    })
                    .collect()
            }
            _ => vec![(i64::MIN, i64::MAX)],
        };
        let mut conns = vec![first];
        for _ in 1..bounds.len() {
            let c = open_at(path, generation)?;
            c.execute_batch(FTS_MMAP).map_err(|e| e.to_string())?;
            conns.push(c);
        }
        Ok(FtsPool { generation, parts: conns.into_iter().zip(bounds).map(|(c, (a, b))| (Mutex::new(c), a, b)).collect() })
    }

    /// `fts_scores` for this generation, the ranges queried in parallel.
    pub fn scores(&self, cache: &VectorCache, query: &str) -> Result<HashMap<u32, f64>, String> {
        let Some(m) = fts_match(query) else { return Ok(HashMap::new()) };
        let chunks: Vec<Vec<(i64, f64)>> = self
            .parts
            .par_iter()
            .map(|(c, a, b)| -> rusqlite::Result<Vec<(i64, f64)>> {
                let c = c.lock().unwrap();
                let mut st = c.prepare_cached(
                    "SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ?1 AND rowid BETWEEN ?2 AND ?3",
                )?;
                let rows = st.query_map(rusqlite::params![m, a, b], |r| Ok((r.get(0)?, r.get(1)?)))?.collect();
                rows
            })
            .collect::<rusqlite::Result<_>>()
            .map_err(|e| e.to_string())?;
        Ok(normalise_bm25(chunks.into_iter().flatten().collect(), cache))
    }

    /// The first connection, for hydrating the top hits (same generation).
    pub fn with_conn<T>(&self, f: impl FnOnce(&Connection) -> T) -> T {
        f(&self.parts[0].0.lock().unwrap())
    }
}

/// `context_export._search_internal(hybrid=False)`: a live scan of wiki/ and
/// docs/ in glob order, skipping `traces` segments and undecodable files;
/// score = how many of the query's whitespace tokens (a LIST: repeats count)
/// occur in the lower-cased file; stable sort by score, descending.
pub fn keyword(root: &Path, query: &str, limit: usize) -> Vec<Hit> {
    let tokens: Vec<String> = query.to_lowercase().split(crate::text::py_isspace).filter(|t| !t.is_empty()).map(String::from).collect();
    let files: Vec<std::path::PathBuf> = ["wiki", "docs"].iter().flat_map(|d| glob_md(&root.join(d))).collect();
    let mut hits: Vec<Hit> = files
        .par_iter()
        .filter_map(|p| {
            let r = rel(root, p);
            if r.split('/').any(|seg| seg == "traces") {
                return None;
            }
            // The walk follows symlinked directories, as glob does; content
            // is returned, so every file passes zurvan_read_page's gate and is
            // read at its canonical path (security review 2026-10-19).
            let target = crate::security::open_target(root, &r, false).ok()?;
            let content = read_universal(&target).ok()?;
            if crate::claims::text_is_hidden(&r, &content) {
                return None; // Z2 §7.5a: a claim page that is no longer current
            }
            let lower = content.to_lowercase();
            let score = tokens.iter().filter(|t| lower.contains(t.as_str())).count() as i64;
            (score > 0).then(|| Hit {
                chunk_id: String::new(),
                source_path: r,
                heading: None,
                text: content,
                keyword: 0.0,
                semantic: 0.0,
                score: Score::I(score),
            })
        })
        .collect();
    hits.sort_by(|a, b| b.score_f64().partial_cmp(&a.score_f64()).unwrap());
    hits.truncate(limit);
    hits
}

/// `tool_zurvan_search`'s rendering of `_search_internal` results, budgeted.
pub fn format_results(hits: &[Hit]) -> String {
    if hits.is_empty() {
        return "No matches found.".into();
    }
    budget(&format_results_raw(hits), RESULT_CHAR_BUDGET)
}

/// `mcp_tools._format_search_results`: the same list, unbudgeted (orient
/// budgets the whole response instead).
pub fn format_results_raw(hits: &[Hit]) -> String {
    if hits.is_empty() {
        return "No matches found.".into();
    }
    let out: Vec<String> = hits
        .iter()
        .enumerate()
        .map(|(i, h)| {
            let score = match h.score {
                Score::F(f) => format!("{f:.2}"),
                Score::I(n) => n.to_string(),
            };
            let mut line = format!("{}. {} (score {score})", i + 1, h.source_path);
            if let Some(head) = h.heading.as_deref().filter(|s| !s.is_empty()) {
                line.push_str(&format!("\n   heading: {head}"));
            }
            let snip = snippet(&h.text, 200);
            if !snip.is_empty() {
                line.push_str(&format!("\n   {snip}"));
            }
            line
        })
        .collect();
    out.join("\n")
}

/// Everything a hybrid search needs, loaded lazily and kept for the life of
/// the process: store metadata, the vector cache, and the embedder.
pub struct SearchState {
    pub cfg: Arc<Config>,
    pub meta: Arc<MetaCache>,
    pub loader: Loader,
    /// Keyword mode's accelerator (IOU-13): same results, cached text.
    pub keywords: crate::keyword::KeywordCache,
    /// The FTS connections for the generation being served.
    fts: Mutex<Option<Arc<FtsPool>>>,
    embedder: Mutex<ModelSlot>,
    /// How long a failed model load is reported before it is tried again.
    model_retry: std::time::Duration,
    load_model: Arc<dyn Fn() -> Result<Arc<Embedder>, String> + Send + Sync>,
    /// Deterministic barrier for the F6 interleaving tests: runs once per
    /// hybrid call, after hydration and before the result is returned.
    #[cfg(test)]
    pub(crate) after_hydrate: Mutex<Option<Box<dyn Fn() + Send>>>,
    /// Deterministic barrier, run once, between scoring and hydration.
    #[cfg(test)]
    pub(crate) before_hydrate: Mutex<Option<Box<dyn Fn() + Send>>>,
}

/// The query model: not tried yet, loaded, or failed at a time (and retried
/// after `model_retry`, so restoring the model needs no server restart).
enum ModelSlot {
    Untried,
    Loaded(Arc<Embedder>),
    Failed(std::time::Instant, String),
}

/// Final review minor 6: a failure used to be cached for the process's life.
const MODEL_RETRY: std::time::Duration = std::time::Duration::from_secs(60);

impl SearchState {
    pub fn new(cfg: Config) -> Self {
        SearchState::with(Arc::new(cfg), Arc::new(MetaCache::default()))
    }

    pub fn with(cfg: Arc<Config>, meta: Arc<MetaCache>) -> Self {
        let keyword_dirs = vec![cfg.cache_dir.clone(), crate::vectors::default_fallback_dir()];
        SearchState {
            cfg,
            meta,
            keywords: crate::keyword::KeywordCache::new(keyword_dirs),
            fts: Mutex::new(None),
            loader: Loader::default(),
            embedder: Mutex::new(ModelSlot::Untried),
            model_retry: MODEL_RETRY,
            load_model: Arc::new(|| locate_model().and_then(|d| Embedder::load(&d)).map(Arc::new)),
            #[cfg(test)]
            after_hydrate: Mutex::new(None),
            #[cfg(test)]
            before_hydrate: Mutex::new(None),
        }
    }

    /// A state whose model comes from `load` and whose failures are retried
    /// after `retry` (tests; the server uses `new`).
    pub fn with_model_loader(
        cfg: Config,
        retry: std::time::Duration,
        load: impl Fn() -> Result<Arc<Embedder>, String> + Send + Sync + 'static,
    ) -> Self {
        let mut st = SearchState::new(cfg);
        st.model_retry = retry;
        st.load_model = Arc::new(load);
        st
    }

    /// The pool for `meta`'s generation, reopened when the index is promoted.
    fn fts_pool(&self, meta: &crate::store::IndexMeta) -> Result<Arc<FtsPool>, String> {
        let mut slot = self.fts.lock().unwrap();
        if let Some(p) = slot.as_ref().filter(|p| p.generation == meta.generation) {
            return Ok(p.clone());
        }
        let p = Arc::new(FtsPool::open(&self.cfg.search_db, meta.generation, FTS_PARTS)?);
        *slot = Some(p.clone());
        Ok(p)
    }

    /// The generation the FTS pool currently serves (tests; doctor).
    pub fn fts_generation(&self) -> Option<crate::store::Generation> {
        self.fts.lock().unwrap().as_ref().map(|p| p.generation)
    }

    pub fn embedder(&self) -> Result<Arc<Embedder>, String> {
        let mut slot = self.embedder.lock().unwrap();
        match &*slot {
            ModelSlot::Loaded(e) => return Ok(e.clone()),
            ModelSlot::Failed(at, why) if at.elapsed() < self.model_retry => return Err(why.clone()),
            _ => {}
        }
        let loaded = (self.load_model)();
        *slot = match &loaded {
            Ok(e) => ModelSlot::Loaded(e.clone()),
            Err(why) => ModelSlot::Failed(std::time::Instant::now(), why.clone()),
        };
        loaded
    }

    fn model_loaded(&self) -> bool {
        matches!(*self.embedder.lock().unwrap(), ModelSlot::Loaded(_))
    }

    /// The model's state for `zurvan_status` without loading it.
    pub fn model_status(&self) -> String {
        match &*self.embedder.lock().unwrap() {
            ModelSlot::Loaded(_) => "Model: loaded (all-MiniLM-L6-v2, candle CPU)".into(),
            ModelSlot::Failed(_, e) => format!("Model: unavailable ({e})"),
            ModelSlot::Untried => match locate_model() {
                Ok(_) => "Model: available, loads on first hybrid search".into(),
                Err(e) => format!("Model: unavailable ({e})"),
            },
        }
    }

    pub fn cache_status(&self) -> String {
        match self.loader.current() {
            Some(c) => format!("Vector cache: ready ({} rows)", crate::text::thousands(c.rows() as i64)),
            None => "Vector cache: not loaded (built or opened on first hybrid search)".into(),
        }
    }

    /// The query vector the INDEX calls for, or why there is none.
    fn query_vector(&self, space: &Space, query: &str, on_progress: &dyn Fn(u64, u64)) -> Result<Vec<f32>, String> {
        match space {
            Space::Mock => Ok(mock(query)),
            Space::Real => {
                // A first query may wait for the model: say so (0/1 then 1/1).
                let cold = !self.model_loaded();
                if cold {
                    on_progress(0, 1);
                }
                let e = self.embedder()?;
                if cold {
                    on_progress(1, 1);
                }
                e.embed(query)
            }
            Space::Unsupported(why) => Err(why.clone()),
        }
    }

    /// `search_hybrid(query, limit)`. `Ok(None)` = semantic search is not
    /// available (the caller falls back to keyword results and says so).
    pub fn hybrid(&self, query: &str, limit: usize) -> Result<Vec<Hit>, String> {
        if !self.cfg.search_db.exists() {
            return Ok(Vec::new());
        }
        match self.hybrid_or_unavailable(query, limit, &|_, _| {})? {
            Ok((hits, _)) => Ok(hits),
            Err(why) => Err(format!("semantic search unavailable: {why}")),
        }
    }

    /// `Ok(Ok((hits, meta)))`: the hits AND the generation they were
    /// hydrated from. `Ok(Err(why))`: semantic search is unavailable.
    pub fn hybrid_or_unavailable(
        &self,
        query: &str,
        limit: usize,
        on_progress: &dyn Fn(u64, u64),
    ) -> HybridOutcome {
        // The metadata, the vector cache and the connection that scores and
        // hydrates must be ONE generation. A promote landing between them is
        // detected (`open_at`) and the call starts again on the new one.
        let changed = |r: &HybridOutcome| match r {
            Err(e) | Ok(Err(e)) => e.contains(GENERATION_CHANGED),
            Ok(Ok(_)) => false,
        };
        let mut out = self.hybrid_once(query, limit, on_progress);
        for _ in 0..GENERATION_RETRIES {
            if !changed(&out) {
                break;
            }
            out = self.hybrid_once(query, limit, on_progress);
        }
        out
    }

    fn hybrid_once(
        &self,
        query: &str,
        limit: usize,
        on_progress: &dyn Fn(u64, u64),
    ) -> HybridOutcome {
        if !self.cfg.search_db.exists() {
            return Err("unable to open database file".into());
        }
        let meta = self.meta.index(&self.cfg)?;
        let space = Space::for_index(meta.provider.as_deref(), meta.model.as_deref());
        let qv = match self.query_vector(&space, query, on_progress) {
            Ok(v) => v,
            Err(why) => return Ok(Err(why)),
        };
        let cache = match self.loader.get(&self.cfg, &meta, on_progress) {
            Ok(c) => c,
            Err(why) => return Ok(Err(format!("vector cache: {why}"))),
        };
        // Persistent, generation-pinned connections (opened with `open_at`):
        // the FTS stage runs over FTS_PARTS rowid ranges in parallel.
        let pool = self.fts_pool(&meta)?;
        let kw = pool.scores(&cache, query)?;
        // IOU-Z2-6 / spec §7.5a: the index forgets a claim only when it is
        // rebuilt, so a hit on a claim page that is no longer current is
        // dropped here and the result refilled to `limit` from a deeper top-k.
        // No claim hit went non-current (the common case): one pass, and a
        // frontmatter read per claim hit.
        let mut k = limit;
        let hits = loop {
            let top = cache.top_k(&qv, &kw, k);
            #[cfg(test)]
            if let Some(barrier) = self.before_hydrate.lock().unwrap().take() {
                barrier();
            }
            let hits = if top.is_empty() { Ok(Vec::new()) } else { pool.with_conn(|conn| hydrate(conn, &cache, &top))? };
            let Ok(hits) = hits else { break hits };
            let hidden: Vec<bool> = hits.iter().map(|h| crate::claims::page_is_hidden(&self.cfg.root, &h.source_path)).collect();
            let n = hidden.iter().filter(|&&x| x).count();
            if n == 0 || top.len() < k || limit + n <= k {
                break Ok(hits.into_iter().zip(hidden).filter(|(_, x)| !x).map(|(h, _)| h).take(limit).collect());
            }
            k = limit + n;
        };
        #[cfg(test)]
        if let Some(barrier) = self.after_hydrate.lock().unwrap().as_ref() {
            barrier();
        }
        // `meta` is the generation the pool and cache were pinned to: the
        // hits were hydrated from it, by construction.
        Ok(hits.map(|h| (h, meta)))
    }
}

/// The top hits' rows from the same generation, fused scores, Python's floor.
fn hydrate(conn: &Connection, cache: &VectorCache, top: &[crate::vectors::Ranked]) -> Result<Result<Vec<Hit>, String>, String> {
    {
        let ids: Vec<&str> = top.iter().map(|r| cache.chunk_id(r.row)).collect();
        let placeholders = vec!["?"; ids.len()].join(",");
        let mut st = conn
            .prepare(&format!("SELECT chunk_id, source_path, heading, text FROM chunks WHERE chunk_id IN ({placeholders})"))
            .map_err(|e| e.to_string())?;
        type Row = (String, Option<String>, Option<String>, Option<String>);
        let rows: HashMap<String, Row> = st
            .query_map(rusqlite::params_from_iter(ids.iter()), |r| Ok((r.get::<_, String>(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .map(|row| (row.0.clone(), row))
            .collect();
        let hits = top
            .iter()
            .filter_map(|r| {
                let (chunk_id, source_path, heading, text) = rows.get(cache.chunk_id(r.row))?.clone();
                // _fuse recomputes the score from its parts; identical formula.
                let score = KEYWORD_WEIGHT * r.keyword + (1.0 - KEYWORD_WEIGHT) * r.semantic;
                passes_floor(score).then(|| Hit {
                    chunk_id,
                    source_path: source_path.unwrap_or_else(|| "None".into()),
                    heading,
                    text: text.unwrap_or_default(),
                    keyword: r.keyword,
                    semantic: r.semantic,
                    score: Score::F(score),
                })
            })
            .collect();
        Ok(Ok(hits))
    }
}

impl SearchState {
    /// `_search_internal(query, hybrid, limit)`, with Rust's explicit
    /// fallback notice when semantic search cannot run (design §7A).
    pub fn search(&self, query: &str, hybrid: bool, limit: usize, on_progress: &dyn Fn(u64, u64)) -> Result<SearchResult, String> {
        if !hybrid {
            return Ok(SearchResult { hits: self.keywords.search(&self.cfg.root, query, limit), notice: None, served: Served::LiveFiles });
        }
        if !self.cfg.search_db.exists() {
            return Ok(SearchResult { hits: Vec::new(), notice: None, served: Served::NoIndex });
        }
        match self.hybrid_or_unavailable(query, limit, on_progress)? {
            Ok((hits, meta)) => Ok(SearchResult { hits, notice: None, served: Served::Index(meta) }),
            Err(why) => Ok(SearchResult {
                hits: self.keywords.search(&self.cfg.root, query, limit),
                notice: Some(format!("Semantic search unavailable: {why} — keyword results only.")),
                served: Served::LiveFiles,
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::embed::mock;
    use crate::store::{open_ro, MetaCache};
    use crate::testutil::{search_db, Chunk};
    use crate::vectors::Loader;
    use std::fs;

    const DOCS: [(&str, &str, &str); 5] = [
        ("c1", "wiki/ledger.md", "the knowledge ledger mints a revision per operation"),
        ("c2", "wiki/ledger.md", "ledger ledger ledger revision revision"),
        ("c3", "docs/boundary.md", "the boundary egress gate scans staged changes"),
        ("c4", "wiki/fusion.md", "keyword weight in hybrid fusion"),
        ("c5", "wiki/unrelated.md", "sourdough starter hydration notes"),
    ];

    fn mock_store() -> (tempfile::TempDir, Config) {
        let dir = tempfile::tempdir().unwrap();
        let cfg = Config::resolve_from(Some(dir.path().to_path_buf()), |_| None);
        let chunks: Vec<Chunk> = DOCS
            .iter()
            .map(|(id, p, t)| Chunk { chunk_id: id, source_path: p, heading: Some("H"), text: t, indexed_at: "2026-10-18T00:00:00", vector: mock(t) })
            .collect();
        search_db(&cfg.search_db, "mock", "deterministic_hash", &chunks, &[("built_from_revision", "1")]);
        (dir, cfg)
    }

    fn cache_for(cfg: &Config) -> std::sync::Arc<crate::vectors::VectorCache> {
        Loader::default().get(cfg, &MetaCache::default().index(cfg).unwrap(), &|_, _| {}).unwrap()
    }

    #[test]
    fn fts_rowid_matches_chunk_id_scores() {
        let (_d, cfg) = mock_store();
        let cache = cache_for(&cfg);
        let conn = open_ro(&cfg.search_db).unwrap();
        let by_row = fts_scores(&conn, &cache, "ledger revision boundary").unwrap();
        let mut via_rowid: Vec<(String, String)> =
            by_row.iter().map(|(r, s)| (cache.chunk_id(*r).to_string(), format!("{s:.12}"))).collect();
        // The chunk_id form Python uses, normalised the same way.
        let mut st = conn.prepare("SELECT chunk_id, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ?").unwrap();
        let raw: Vec<(String, f64)> = st
            .query_map(["\"ledger\" OR \"revision\" OR \"boundary\""], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        let (mn, mx) = raw.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |a, r| (a.0.min(r.1), a.1.max(r.1)));
        let mut via_chunk_id: Vec<(String, String)> = raw
            .iter()
            .map(|(id, s)| (id.clone(), format!("{:.12}", if mx == mn { 1.0 } else { (mx - s) / (mx - mn) })))
            .collect();
        via_rowid.sort();
        via_chunk_id.sort();
        assert_eq!(via_rowid, via_chunk_id);
        assert_eq!(via_rowid.len(), 3);
    }

    #[test]
    fn fts_normalisation() {
        let (_d, cfg) = mock_store();
        let cache = cache_for(&cfg);
        let conn = open_ro(&cfg.search_db).unwrap();
        let s = fts_scores(&conn, &cache, "ledger").unwrap();
        let mut v: Vec<f64> = s.values().cloned().collect();
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
        assert_eq!(v.len(), 2);
        assert_eq!((v[0], v[1]), (0.0, 1.0));
        let one = fts_scores(&conn, &cache, "sourdough").unwrap();
        assert_eq!(one.values().cloned().collect::<Vec<_>>(), vec![1.0], "a single match normalises to 1.0");
    }

    #[test]
    fn fts_handles_bareword_keywords() {
        let (_d, cfg) = mock_store();
        let cache = cache_for(&cfg);
        let conn = open_ro(&cfg.search_db).unwrap();
        assert!(fts_scores(&conn, &cache, "AND OR NOT NEAR").is_ok());
        assert!(fts_scores(&conn, &cache, "!!! ???").unwrap().is_empty());
    }

    #[test]
    fn hybrid_with_no_fts_terms_still_ranks() {
        let (_d, cfg) = mock_store();
        let st = SearchState::new(cfg);
        let hits = st.hybrid("!!! ??? ...", 10).unwrap();
        assert!(!hits.is_empty(), "semantic scoring runs without keyword terms");
        assert!(hits.iter().all(|h| h.keyword == 0.0));
        // Canonical order: score DESC, chunk_id ASC.
        for w in hits.windows(2) {
            let (a, b) = (w[0].score_f64(), w[1].score_f64());
            assert!(a > b || (a == b && w[0].chunk_id < w[1].chunk_id));
        }
    }

    #[test]
    fn hybrid_exact_match_wins_on_a_mock_index() {
        // A mock query vector equals the stored vector of identical text, so
        // the chunk whose text IS the query has cosine 1.
        let (_d, cfg) = mock_store();
        let st = SearchState::new(cfg);
        let hits = st.hybrid("sourdough starter hydration notes", 3).unwrap();
        assert_eq!(hits[0].chunk_id, "c5");
        assert_eq!(hits[0].source_path, "wiki/unrelated.md");
        assert_eq!(hits[0].heading.as_deref(), Some("H"));
    }

    #[test]
    fn min_score_is_strict() {
        assert!(!passes_floor(0.2));
        assert!(passes_floor(0.2000001));
    }

    #[test]
    fn keyword_order_is_traversal_order() {
        let dir = tempfile::tempdir().unwrap();
        let wiki = dir.path().join("wiki");
        fs::create_dir_all(wiki.join("traces")).unwrap();
        for name in ["m.md", "b.md", "z.md", "a.md", "k.md"] {
            fs::write(wiki.join(name), "Alpha beta").unwrap();
        }
        fs::write(wiki.join("traces/t.md"), "alpha").unwrap();
        fs::write(wiki.join("bin.md"), [0xffu8, 0xfe, 0x00]).unwrap();
        let readdir: Vec<String> = fs::read_dir(&wiki)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|n| n.ends_with(".md") && n != "bin.md")
            .map(|n| format!("wiki/{n}"))
            .collect();
        let hits = keyword(dir.path(), "ALPHA", 10);
        let got: Vec<String> = hits.iter().map(|h| h.source_path.clone()).collect();
        assert_eq!(got, readdir, "equal scores keep traversal order; traces and undecodable files are skipped");
        assert!(hits.iter().all(|h| h.score == Score::I(1)));
    }

    /// Security review 2026-10-19: the walk follows symlinked directories
    /// (as Python's glob does) and returns file content, so every walked file
    /// must pass the same read gate as zurvan_read_page.
    #[test]
    fn keyword_reads_only_what_the_read_gate_allows() {
        let dir = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("secret.md"), "alpha outside the root").unwrap();
        let wiki = dir.path().join("wiki");
        fs::create_dir_all(wiki.join("Traces")).unwrap();
        fs::write(wiki.join("ok.md"), "alpha").unwrap();
        fs::write(wiki.join("Traces/t.md"), "alpha in a case-variant traces dir").unwrap();
        std::os::unix::fs::symlink(outside.path(), wiki.join("link")).unwrap();
        let got: Vec<String> = keyword(dir.path(), "alpha", 10).into_iter().map(|h| h.source_path).collect();
        assert_eq!(got, vec!["wiki/ok.md".to_string()]);
    }

    #[test]
    fn keyword_counts_repeated_tokens() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("docs")).unwrap();
        fs::write(dir.path().join("docs/x.md"), "ledger").unwrap();
        let hits = keyword(dir.path(), "ledger ledger missing", 10);
        assert_eq!(hits[0].score, Score::I(2), "Python sums over the token LIST, duplicates included");
    }

    #[test]
    fn format_matches_python() {
        let hits = vec![
            Hit { chunk_id: String::new(), source_path: "wiki/a.md".into(), heading: None, text: "Alpha \n beta".into(), keyword: 0.0, semantic: 0.0, score: Score::I(2) },
            Hit { chunk_id: "c".into(), source_path: "docs/b.md".into(), heading: Some("Head".into()), text: "x".repeat(250), keyword: 0.5, semantic: 0.9, score: Score::F(0.8249) },
            Hit { chunk_id: "d".into(), source_path: "docs/c.md".into(), heading: Some(String::new()), text: String::new(), keyword: 0.0, semantic: 0.0, score: Score::F(0.825) },
        ];
        assert_eq!(
            format_results(&hits),
            format!("1. wiki/a.md (score 2)\n   Alpha beta\n2. docs/b.md (score 0.82)\n   heading: Head\n   {}…\n3. docs/c.md (score 0.82)", "x".repeat(200))
        );
        assert_eq!(format_results(&[]), "No matches found.");
    }

    /// Final review 2026-10-19 (I2), reproduced over stdio at 1,866 of 6,064
    /// calls: a promote between reading the generation and opening the index
    /// by path scored one generation's vectors against the other's text. Two
    /// generations share chunk ids but not text; a semantic score of ~1 can
    /// only come from the matrix whose text IS the query, so the hit's text
    /// must carry the query's word.
    #[test]
    fn hybrid_never_mixes_generations_under_promotion() {
        use std::sync::atomic::{AtomicBool, Ordering};
        const N: usize = 60;
        let dir = tempfile::tempdir().unwrap();
        let cfg = Config::resolve_from(Some(dir.path().to_path_buf()), |_| None);
        let ids: Vec<String> = (0..N).map(|i| format!("{i:064x}")).collect();
        let build = |path: &std::path::Path, word: &str| {
            let texts: Vec<String> = (0..N).map(|i| format!("{word} entry {i}")).collect();
            let chunks: Vec<Chunk> = (0..N)
                .map(|i| Chunk { chunk_id: &ids[i], source_path: "wiki/p.md", heading: None, text: &texts[i], indexed_at: "2026-10-18T00:00:00", vector: mock(&texts[i]) })
                .collect();
            search_db(path, "mock", "deterministic_hash", &chunks, &[("built_from_revision", "1")]);
        };
        let (a, b) = (dir.path().join("A.sqlite"), dir.path().join("B.sqlite"));
        build(&a, "alpha");
        build(&b, "beta");
        fs::create_dir_all(cfg.search_db.parent().unwrap()).unwrap();
        fs::copy(&a, &cfg.search_db).unwrap();
        let stop = AtomicBool::new(false);
        let st = SearchState::new(cfg.clone());
        let (mut calls, mut mixed) = (0usize, 0usize);
        std::thread::scope(|s| {
            s.spawn(|| {
                let tmp = cfg.search_db.with_extension("tmp");
                for i in 0.. {
                    if stop.load(Ordering::SeqCst) {
                        break;
                    }
                    fs::copy(if i % 2 == 0 { &b } else { &a }, &tmp).unwrap();
                    fs::rename(&tmp, &cfg.search_db).unwrap();
                    std::thread::sleep(std::time::Duration::from_micros(300));
                }
            });
            // Run until the storm has made enough calls, not for a fixed 3 s: on a
            // loaded host (the full Python suite alongside) a call that follows a
            // promote rebuilds the vector cache, and 3 s gave 42 calls, failing
            // the `calls > 50` sanity check, never the mixing assertion
            // (2026-11-16). The cap keeps a stuck run bounded.
            let t0 = std::time::Instant::now();
            while (calls < 200 || t0.elapsed() < std::time::Duration::from_secs(3))
                && t0.elapsed() < std::time::Duration::from_secs(60)
            {
                for word in ["alpha", "beta"] {
                    let q = format!("{word} entry {}", calls % N);
                    calls += 1;
                    let Ok(Ok((hits, _))) = st.hybrid_or_unavailable(&q, 3, &|_, _| {}) else { continue };
                    if let Some(h) = hits.first() {
                        if h.semantic > 0.99 && !h.text.starts_with(word) {
                            mixed += 1;
                        }
                    }
                }
            }
            stop.store(true, Ordering::SeqCst);
        });
        assert!(calls > 50, "the storm ran ({calls} calls)");
        assert_eq!(mixed, 0, "{mixed} of {calls} calls mixed two generations");
    }

    /// Audit 2026-10-22 F6: the server obtained hits and THEN read current
    /// index metadata for the stamp. A promote between the two stamped A's
    /// text as B. The response now carries the generation it hydrated from.
    #[test]
    fn the_stamp_names_the_generation_that_was_hydrated_not_the_one_promoted_after() {
        let (_d, cfg) = mock_store();
        let st = SearchState::new(cfg.clone());
        let promote_to = cfg.clone();
        *st.after_hydrate.lock().unwrap() = Some(Box::new(move || {
            // A new generation (built_from_revision 2) replaces the index
            // between hydration and the stamp: new inode, same path.
            let tmp = promote_to.search_db.with_extension("next");
            let chunks: Vec<Chunk> = DOCS
                .iter()
                .map(|(id, p, t)| Chunk { chunk_id: id, source_path: p, heading: Some("H"), text: t, indexed_at: "2026-10-19T00:00:00", vector: mock(t) })
                .collect();
            search_db(&tmp, "mock", "deterministic_hash", &chunks, &[("built_from_revision", "2")]);
            fs::rename(&tmp, &promote_to.search_db).unwrap();
        }));
        let r = st.search("ledger", true, 3, &|_, _| {}).unwrap();
        assert!(!r.hits.is_empty());
        let Served::Index(m) = &r.served else { panic!("hybrid serves an index") };
        assert_eq!(m.built_from_revision, Some(1), "the generation hydrated from");
        let s = crate::status::stamp_for(&cfg, &r.served, &st.meta, &[]);
        assert!(s.starts_with("[index rev 1 "), "{s}");
        assert!(!s.contains("index rev 2"), "{s}");
        assert!(s.contains("superseded"), "a promote after hydration is reported: {s}");
        // The generation now at the path IS 2: the old stamp would have said so.
        assert_eq!(st.meta.index(&cfg).unwrap().built_from_revision, Some(2));
    }

    /// 2026-11-16: the storm test above cannot catch an unpinned hydration
    /// connection (its race window is microseconds; mutation-checked). This one
    /// promotes deterministically between scoring and hydration: same chunk ids,
    /// different text. The hits must come from the generation that was scored.
    #[test]
    fn hydration_reads_the_scored_generation_even_if_promoted_in_between() {
        let (_d, cfg) = mock_store();
        let st = SearchState::new(cfg.clone());
        let promote_to = cfg.clone();
        *st.before_hydrate.lock().unwrap() = Some(Box::new(move || {
            let tmp = promote_to.search_db.with_extension("next");
            let texts: Vec<String> = DOCS.iter().map(|(_, _, t)| format!("REPLACED {t}")).collect();
            let chunks: Vec<Chunk> = DOCS
                .iter()
                .zip(&texts)
                .map(|((id, p, _), t)| Chunk { chunk_id: id, source_path: p, heading: Some("H"), text: t, indexed_at: "2026-10-19T00:00:00", vector: mock(t) })
                .collect();
            search_db(&tmp, "mock", "deterministic_hash", &chunks, &[("built_from_revision", "2")]);
            fs::rename(&tmp, &promote_to.search_db).unwrap();
        }));
        let r = st.search("ledger", true, 3, &|_, _| {}).unwrap();
        assert!(!r.hits.is_empty());
        assert!(r.hits.iter().all(|h| !h.text.starts_with("REPLACED")), "hydrated from the promoted file: {:?}",
                r.hits.iter().map(|h| &h.text).collect::<Vec<_>>());
        let Served::Index(m) = &r.served else { panic!("hybrid serves an index") };
        assert_eq!(m.built_from_revision, Some(1));
    }

    #[test]
    fn keyword_mode_serves_live_files_and_says_so() {
        let (_d, cfg) = mock_store();
        let st = SearchState::new(cfg.clone());
        let r = st.search("anything", false, 3, &|_, _| {}).unwrap();
        assert!(matches!(r.served, Served::LiveFiles));
        let s = crate::status::stamp_for(&cfg, &r.served, &st.meta, &[]);
        assert!(s.starts_with("[live files · ledger rev "), "{s}");
    }

    /// Final review minor 6: a failed model load was cached for the life of
    /// the process, so restoring the model needed a server restart.
    #[test]
    fn a_failed_model_load_is_retried_after_a_while() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let (_d, cfg) = mock_store();
        let attempts = Arc::new(AtomicUsize::new(0));
        let a = attempts.clone();
        let st = SearchState::with_model_loader(cfg, std::time::Duration::from_millis(200), move || {
            a.fetch_add(1, Ordering::SeqCst);
            Err("model not found at /nowhere (missing config.json)".to_string())
        });
        assert!(st.embedder().is_err());
        assert!(st.embedder().is_err());
        assert_eq!(attempts.load(Ordering::SeqCst), 1, "not retried inside the window");
        assert!(st.model_status().starts_with("Model: unavailable (model not found"), "{}", st.model_status());
        std::thread::sleep(std::time::Duration::from_millis(250));
        assert!(st.embedder().is_err());
        assert_eq!(attempts.load(Ordering::SeqCst), 2, "retried after the window");
    }

    /// The closure's hybrid p95 target (150 ms) was missed because one FTS5
    /// query scores ~170k rows. Partitioned by rowid over persistent mmap
    /// connections it runs in parallel; bm25's IDF and avgdl are table-wide
    /// (xRowCount, xColumnTotalSize, xQueryPhrase ignore the outer query's
    /// rowid constraint), so every row's score is bit-identical.
    #[test]
    fn partitioned_fts_scores_equal_the_single_query_bit_for_bit() {
        let (_d, cfg) = mock_store();
        let cache = cache_for(&cfg);
        let meta = MetaCache::default().index(&cfg).unwrap();
        let conn = open_ro(&cfg.search_db).unwrap();
        for parts in [1, 2, 3, 4, 7] {
            let pool = FtsPool::open(&cfg.search_db, meta.generation, parts).unwrap();
            for q in ["ledger revision boundary", "ledger ledger", "keyword weight in hybrid fusion", "sourdough", "!!! ???", "nothing matches zzzq"] {
                let one = fts_scores(&conn, &cache, q).unwrap();
                let split = pool.scores(&cache, q).unwrap();
                assert_eq!(one.len(), split.len(), "parts={parts} q={q}");
                for (row, s) in &one {
                    assert_eq!(s.to_bits(), split[row].to_bits(), "parts={parts} q={q} row={row}");
                }
            }
        }
    }

    #[test]
    fn an_fts_pool_is_never_opened_on_a_promoted_index() {
        let (_d, cfg) = mock_store();
        let old = MetaCache::default().index(&cfg).unwrap().generation;
        let next = cfg.root.join("next.sqlite");
        search_db(&next, "mock", "deterministic_hash",
                  &[Chunk { chunk_id: "z", source_path: "wiki/z.md", heading: None, text: "zebra", indexed_at: "2026-10-18T00:00:00", vector: mock("zebra") }],
                  &[("built_from_revision", "2")]);
        fs::rename(&next, &cfg.search_db).unwrap();
        assert_eq!(FtsPool::open(&cfg.search_db, old, 4).err().as_deref(), Some(GENERATION_CHANGED));
    }

    #[test]
    fn the_fts_pool_follows_the_index_generation() {
        let (_d, cfg) = mock_store();
        let st = SearchState::new(cfg.clone());
        st.hybrid("ledger revision", 3).unwrap();
        let g1 = st.fts_generation().expect("pool built");
        let next = cfg.root.join("next.sqlite");
        search_db(&next, "mock", "deterministic_hash",
                  &[Chunk { chunk_id: "z", source_path: "wiki/z.md", heading: None, text: "ledger zebra", indexed_at: "2026-10-18T00:00:00", vector: mock("ledger zebra") }],
                  &[("built_from_revision", "2")]);
        fs::rename(&next, &cfg.search_db).unwrap();
        let hits = st.hybrid("ledger zebra", 3).unwrap();
        assert_eq!(hits[0].chunk_id, "z");
        assert_ne!(st.fts_generation(), Some(g1), "a new pool for the new index");
    }

    /// IOU-Z2-6 / spec §7.5a: the index forgets a claim only when it is
    /// rebuilt, so a hit on a claim page that is no longer current is dropped
    /// at query time and the result refilled to `limit`.
    #[test]
    fn a_claim_made_noncurrent_after_indexing_leaves_hybrid_search() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = Config::resolve_from(Some(dir.path().to_path_buf()), |_| None);
        let claim_text = "sourdough starter hydration notes";
        let mut chunks: Vec<Chunk> = DOCS
            .iter()
            .map(|(id, p, t)| Chunk { chunk_id: id, source_path: p, heading: Some("H"), text: t, indexed_at: "2026-10-18T00:00:00", vector: mock(t) })
            .collect();
        chunks.push(Chunk { chunk_id: "k1", source_path: "wiki/claims/clm_a.md", heading: None, text: claim_text, indexed_at: "2026-10-18T00:00:00", vector: mock(claim_text) });
        search_db(&cfg.search_db, "mock", "deterministic_hash", &chunks, &[("built_from_revision", "1")]);
        fs::create_dir_all(dir.path().join("wiki/claims")).unwrap();
        let page = |status: &str| format!("---\nauthority: structured\nstatus: {status}\n---\n\n{claim_text}\n");
        let claim = dir.path().join("wiki/claims/clm_a.md");
        fs::write(&claim, page("asserted")).unwrap();
        let st = SearchState::new(cfg);
        let ids = |hits: Vec<Hit>| hits.into_iter().map(|h| h.chunk_id).collect::<Vec<_>>();
        let before = ids(st.hybrid(claim_text, 10).unwrap());
        assert!(before.contains(&"k1".to_string()), "a current claim is served: {before:?}");
        for status in NONCURRENT_FOR_TEST {
            fs::write(&claim, page(status)).unwrap();
            let after = ids(st.hybrid(claim_text, 2).unwrap());
            let expected: Vec<String> = before.iter().filter(|i| *i != "k1").take(2).cloned().collect();
            assert_eq!(after, expected, "{status}: dropped, and refilled to the limit");
        }
        // A legacy page (no `authority`) is never hidden.
        fs::write(&claim, format!("---\nstatus: retracted\n---\n\n{claim_text}\n")).unwrap();
        assert!(ids(st.hybrid(claim_text, 10).unwrap()).contains(&"k1".to_string()));
    }

    const NONCURRENT_FOR_TEST: [&str; 4] = ["superseded", "retracted", "orphaned", "erased"];
}
