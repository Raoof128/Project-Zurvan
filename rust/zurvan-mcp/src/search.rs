//! `zurvan_search`: hybrid (FTS5 BM25 + full-scan cosine, fused) and the
//! keyword live scan — ports of `hybrid_search.search_hybrid`,
//! `context_export._search_internal` and `tool_zurvan_search`.

use crate::config::Config;
use crate::corpus::{glob_md, rel};
use crate::embed::{locate_model, mock, Embedder, Space};
use crate::store::{open_ro, MetaCache};
use crate::text::{budget, read_universal, snippet, RESULT_CHAR_BUDGET};
use crate::vectors::{Loader, VectorCache, KEYWORD_WEIGHT};
use rayon::prelude::*;
use rusqlite::Connection;
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, OnceLock};

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

/// Keyword scores by matrix row. Selects `rowid`, never `chunk_id`: on the
/// external-content table the latter costs one `chunks` lookup per matching
/// row (spike: 4.79 s cold vs 0.18 s, identical score set). Min-max is taken
/// over EVERY matched row, as Python does.
pub fn fts_scores(conn: &Connection, cache: &VectorCache, query: &str) -> rusqlite::Result<HashMap<u32, f64>> {
    static TERMS: OnceLock<regex::Regex> = OnceLock::new();
    let re = TERMS.get_or_init(|| regex::Regex::new(r"\W+").unwrap());
    let terms: Vec<String> = re.split(query).filter(|t| !t.is_empty()).map(|t| format!("\"{t}\"")).collect();
    let mut out = HashMap::new();
    if terms.is_empty() {
        return Ok(out);
    }
    let mut st = conn.prepare_cached("SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ?")?;
    let rows: Vec<(i64, f64)> = st.query_map([terms.join(" OR ")], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?;
    if rows.is_empty() {
        return Ok(out);
    }
    let (min, max) = rows.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |a, r| (a.0.min(r.1), a.1.max(r.1)));
    for (rowid, score) in rows {
        let norm = if max == min { 1.0 } else { (max - score) / (max - min) };
        if let Some(row) = cache.row_for_rowid(rowid) {
            out.insert(row, norm);
        }
    }
    Ok(out)
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
            let content = read_universal(p).ok()?;
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

/// `tool_zurvan_search`'s rendering of `_search_internal` results.
pub fn format_results(hits: &[Hit]) -> String {
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
    budget(&out.join("\n"), RESULT_CHAR_BUDGET)
}

/// Everything a hybrid search needs, loaded lazily and kept for the life of
/// the process: store metadata, the vector cache, and the embedder.
pub struct SearchState {
    pub cfg: Arc<Config>,
    pub meta: Arc<MetaCache>,
    pub loader: Loader,
    embedder: OnceLock<Result<Arc<Embedder>, String>>,
}

impl SearchState {
    pub fn new(cfg: Config) -> Self {
        SearchState::with(Arc::new(cfg), Arc::new(MetaCache::default()))
    }

    pub fn with(cfg: Arc<Config>, meta: Arc<MetaCache>) -> Self {
        SearchState { cfg, meta, loader: Loader::default(), embedder: OnceLock::new() }
    }

    pub fn embedder(&self) -> Result<Arc<Embedder>, String> {
        self.embedder
            .get_or_init(|| locate_model().and_then(|d| Embedder::load(&d)).map(Arc::new))
            .clone()
    }

    /// The model's state for `zurvan_status` without loading it.
    pub fn model_status(&self) -> String {
        match self.embedder.get() {
            Some(Ok(_)) => "Model: loaded (all-MiniLM-L6-v2, candle CPU)".into(),
            Some(Err(e)) => format!("Model: unavailable ({e})"),
            None => match locate_model() {
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
    fn query_vector(&self, space: &Space, query: &str) -> Result<Vec<f32>, String> {
        match space {
            Space::Mock => Ok(mock(query)),
            Space::Real => self.embedder()?.embed(query),
            Space::Unsupported(why) => Err(why.clone()),
        }
    }

    /// `search_hybrid(query, limit)`. `Ok(None)` = semantic search is not
    /// available (the caller falls back to keyword results and says so).
    pub fn hybrid(&self, query: &str, limit: usize) -> Result<Vec<Hit>, String> {
        match self.hybrid_or_unavailable(query, limit, &|_, _| {})? {
            Ok(hits) => Ok(hits),
            Err(why) => Err(format!("semantic search unavailable: {why}")),
        }
    }

    pub fn hybrid_or_unavailable(
        &self,
        query: &str,
        limit: usize,
        on_progress: &dyn Fn(u64, u64),
    ) -> Result<Result<Vec<Hit>, String>, String> {
        if !self.cfg.search_db.exists() {
            return Ok(Ok(Vec::new())); // Python prints to stderr and returns []
        }
        let meta = self.meta.index(&self.cfg)?;
        let space = Space::for_index(meta.provider.as_deref(), meta.model.as_deref());
        let qv = match self.query_vector(&space, query) {
            Ok(v) => v,
            Err(why) => return Ok(Err(why)),
        };
        let cache = match self.loader.get(&self.cfg, &meta, on_progress) {
            Ok(c) => c,
            Err(why) => return Ok(Err(format!("vector cache: {why}"))),
        };
        let conn = open_ro(&self.cfg.search_db).map_err(|e| e.to_string())?;
        let kw = fts_scores(&conn, &cache, query).map_err(|e| e.to_string())?;
        let top = cache.top_k(&qv, &kw, limit);
        if top.is_empty() {
            return Ok(Ok(Vec::new()));
        }
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

    /// `_search_internal(query, hybrid, limit)`, with Rust's explicit
    /// fallback notice when semantic search cannot run (design §7A).
    pub fn search(&self, query: &str, hybrid: bool, limit: usize, on_progress: &dyn Fn(u64, u64)) -> Result<(Vec<Hit>, Option<String>), String> {
        if !hybrid {
            return Ok((keyword(&self.cfg.root, query, limit), None));
        }
        match self.hybrid_or_unavailable(query, limit, on_progress)? {
            Ok(hits) => Ok((hits, None)),
            Err(why) => Ok((
                keyword(&self.cfg.root, query, limit),
                Some(format!("Semantic search unavailable: {why} — keyword results only.")),
            )),
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
}
