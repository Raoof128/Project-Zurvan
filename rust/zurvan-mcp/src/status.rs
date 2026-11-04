//! `zurvan_status` (port of `tool_zurvan_status` + `agent_workflow._index_staleness`)
//! and the per-result revision stamp (design §7.8).

use crate::config::{readonly, Config};
use crate::corpus::scan_markdown_files;
use crate::search::Served;
use crate::store::{generation, ledger, MetaCache};
use crate::text::thousands;
use chrono::{DateTime, Local, NaiveDateTime, Utc};
use rayon::prelude::*;
use std::time::{Duration, Instant};

/// How long a freshness verdict is reused (pre-flight ruling: a full corpus
/// mtime walk per search would eat the latency budget).
const FRESHNESS_REUSE: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, PartialEq)]
pub enum Freshness {
    Missing,
    Unreadable,
    Newer(usize),
    Unknown(String),
}

pub fn staleness_text(f: &Freshness) -> String {
    match f {
        Freshness::Missing => "missing — run `zurvan index search`".into(),
        Freshness::Unreadable => "unreadable — run `zurvan index search`".into(),
        Freshness::Newer(0) => "fresh".into(),
        Freshness::Newer(n) => format!("STALE — {n} file(s) newer than the index; run `zurvan index search`"),
        Freshness::Unknown(e) => format!("staleness unknown ({e})"),
    }
}

/// `datetime.fromisoformat` for the naive forms the indexer writes.
fn parse_naive(s: &str) -> Option<NaiveDateTime> {
    ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S"]
        .iter()
        .find_map(|f| NaiveDateTime::parse_from_str(s, f).ok())
}

/// `_index_staleness`: newest `indexed_at` in `<root>/data/search.sqlite`
/// (Python ignores ZURVAN_SEARCH_DB here) against each corpus file's mtime as
/// a LOCAL naive datetime, exactly as `datetime.fromtimestamp` gives it.
pub fn freshness(cfg: &Config, cache: &MetaCache) -> Freshness {
    let g = generation(&cfg.root.join("data").join("search.sqlite")).ok();
    if let Some((at, seen, f)) = cache.freshness.lock().unwrap().as_ref() {
        if at.elapsed() < FRESHNESS_REUSE && *seen == g {
            return f.clone();
        }
    }
    let f = compute_freshness(cfg, cache);
    *cache.freshness.lock().unwrap() = Some((Instant::now(), g, f.clone()));
    f
}

fn compute_freshness(cfg: &Config, cache: &MetaCache) -> Freshness {
    let db = cfg.root.join("data").join("search.sqlite");
    if !db.exists() {
        return Freshness::Missing;
    }
    let newest = if db == cfg.search_db {
        cache.index(cfg).ok().and_then(|m| m.max_indexed_at.clone())
    } else {
        MetaCache::default().index_at(&db).ok().and_then(|m| m.max_indexed_at.clone())
    };
    let Some(newest) = newest else { return Freshness::Unreadable };
    let Some(indexed_at) = parse_naive(&newest) else {
        if DateTime::parse_from_rfc3339(&newest).is_ok() {
            return Freshness::Unknown("can't compare offset-naive and offset-aware datetimes".into());
        }
        return Freshness::Unreadable;
    };
    let root = cfg.root.clone();
    let stale = scan_markdown_files(&root)
        .par_iter()
        .filter(|rel| {
            std::fs::metadata(root.join(rel))
                .and_then(|m| m.modified())
                .map(|t| DateTime::<Local>::from(t).naive_local() > indexed_at)
                .unwrap_or(false)
        })
        .count();
    Freshness::Newer(stale)
}

fn built_minute_utc(built_at: Option<&str>) -> String {
    built_at
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|d| d.with_timezone(&Utc).format("%Y-%m-%dT%H:%MZ").to_string())
        .unwrap_or_else(|| "?".into())
}

fn opt<T: ToString>(v: Option<T>) -> String {
    v.map(|x| x.to_string()).unwrap_or_else(|| "?".into())
}

/// `tool_zurvan_status`, line for line, then the Rust server's own lines
/// (prefixes the parity harness strips).
pub fn status_text(cfg: &Config, cache: &MetaCache) -> String {
    let mut lines = vec![format!("Search index: {}", staleness_text(&freshness(cfg, cache)))];
    let index = cache.index(cfg);
    match &index {
        Ok(m) => {
            lines.push(format!("Indexed: {} chunks across {} files", thousands(m.chunks), thousands(m.files)));
            if let Some(p) = &m.provider {
                lines.push(format!("Embeddings: {p}/{}", m.model.as_deref().unwrap_or("None")));
                if p == "mock" {
                    lines.push(
                        "WARNING: the index holds MOCK embeddings — semantic search is meaningless until it is rebuilt with a real provider."
                            .into(),
                    );
                }
            }
        }
        Err(_) => lines.push(format!(
            "Indexed: no search index at {} — run `zurvan index search`.",
            cfg.search_db.display()
        )),
    }
    let graph = cache.graph(cfg);
    match &graph {
        Ok(g) => lines.push(format!("Graph: {} nodes / {} edges", thousands(g.nodes), thousands(g.edges))),
        Err(e) => lines.push(format!("Graph: unavailable ({e})")),
    }
    let sources = cfg.root.join("wiki").join("sources");
    if sources.exists() {
        let n = std::fs::read_dir(&sources)
            .map(|rd| rd.filter_map(Result::ok).filter(|e| e.file_name().to_string_lossy().ends_with(".md")).count())
            .unwrap_or(0);
        lines.push(format!("Source pages: {}", thousands(n as i64)));
    }
    lines.push(format!("Write tools: {}", if readonly() { "BLOCKED (read-only mode)" } else { "enabled" }));

    if let Ok(m) = &index {
        lines.push(format!(
            "Serving: index rev {} built {} · graph rev {}",
            opt(m.built_from_revision),
            built_minute_utc(m.built_at.as_deref()),
            opt(graph.as_ref().ok().and_then(|g| g.built_from_revision))
        ));
    }
    lines.push(match ledger(cfg) {
        Some((rev, state)) => format!("Ledger: rev {rev} {state}"),
        None => "Ledger: unavailable".into(),
    });
    lines.join("\n")
}

/// The §7.8 stamp: which knowledge a result answered from. Empty when
/// ZURVAN_MCP_STAMP=0. Says what the index was BUILT FROM, not that it is
/// complete (only index_health can say that; IOU-6).
///
/// Derived from `served` — the generation the hits were hydrated from —
/// never from a fresh read of the index path (audit 2026-10-22 F6: a promote
/// between hydration and the stamp labelled generation A's text as B). When
/// the path no longer holds the served generation, the stamp says so instead
/// of describing the newer index's freshness. `extra` parts are appended
/// (the context tool's graph revision, orient's live page reads).
pub fn stamp_for(cfg: &Config, served: &Served, cache: &MetaCache, extra: &[String]) -> String {
    if std::env::var("ZURVAN_MCP_STAMP").as_deref() == Ok("0") {
        return String::new();
    }
    let ledger_rev = ledger(cfg).map(|(r, _)| r);
    let mut parts = match served {
        Served::LiveFiles => vec!["live files".to_string(), format!("ledger rev {}", opt(ledger_rev))],
        Served::NoIndex => vec!["index unavailable".to_string(), format!("ledger rev {}", opt(ledger_rev))],
        Served::Index(m) => {
            let mut parts = vec![
                format!("index rev {} built {}", opt(m.built_from_revision), built_minute_utc(m.built_at.as_deref())),
                format!("ledger rev {}", opt(ledger_rev)),
            ];
            if let (Some(n), Some(r)) = (ledger_rev, m.built_from_revision) {
                parts.push(format!("{} writes since", n - r));
            }
            let still_current = cache.index(cfg).map(|c| c.generation == m.generation).unwrap_or(false);
            parts.push(if still_current {
                match freshness(cfg, cache) {
                    Freshness::Newer(k) => format!("{k} files newer"),
                    other => staleness_text(&other),
                }
            } else {
                "superseded: a newer index was promoted during this call".to_string()
            });
            parts
        }
    };
    parts.extend(extra.iter().cloned());
    format!("[{}]", parts.join(" · "))
}

/// The graph part of a context stamp: the revision of the graph generation
/// that was actually opened, or "graph superseded" when the path has moved
/// on since. `None` when the bundle opened no graph.
pub fn graph_part(cfg: &Config, cache: &MetaCache, opened: Option<crate::store::Generation>) -> Option<String> {
    let g = opened?;
    Some(match cache.graph(cfg) {
        Ok(m) if m.generation == g => format!("graph rev {}", opt(m.built_from_revision)),
        _ => "graph superseded: a newer graph was promoted during this call".to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::store::MetaCache;
    use crate::testutil::{graph_db, registry_db, search_db, Chunk};
    use std::fs;

    fn chunk<'a>(id: &'a str, path: &'a str, at: &'a str) -> Chunk<'a> {
        Chunk { chunk_id: id, source_path: path, heading: None, text: "t", indexed_at: at, vector: vec![0.0; 4] }
    }

    fn root_with_files() -> (tempfile::TempDir, Config) {
        let dir = tempfile::tempdir().unwrap();
        let r = dir.path();
        for d in ["wiki/sources", "wiki/traces", "docs/evaluation/retrieval", "raw"] {
            fs::create_dir_all(r.join(d)).unwrap();
        }
        for f in ["wiki/a.md", "docs/b.md", "README.md", "wiki/sources/s1.md", "wiki/sources/s2.md"] {
            fs::write(r.join(f), "x").unwrap();
        }
        // Excluded by scan_markdown_files: traces segment, raw, the retrieval-eval prefix,
        // dot-files, and non-.md files.
        for f in ["wiki/traces/t.md", "raw/r.md", "docs/evaluation/retrieval/e.md", "wiki/.hidden.md", "wiki/note.txt"] {
            fs::write(r.join(f), "x").unwrap();
        }
        let cfg = Config::resolve_from(Some(r.to_path_buf()), |_| None);
        (dir, cfg)
    }

    #[test]
    fn staleness_strings_exact() {
        let (_d, cfg) = root_with_files();
        let cache = MetaCache::default();
        assert_eq!(staleness_text(&freshness(&cfg, &cache)), "missing — run `zurvan index search`");

        fs::create_dir_all(cfg.search_db.parent().unwrap()).unwrap();
        fs::write(&cfg.search_db, b"this is not a database").unwrap();
        assert_eq!(staleness_text(&freshness(&cfg, &MetaCache::default())), "unreadable — run `zurvan index search`");

        fs::remove_file(&cfg.search_db).unwrap();
        search_db(&cfg.search_db, "mock", "m", &[chunk("a", "wiki/a.md", "2999-01-01T00:00:00")], &[]);
        assert_eq!(staleness_text(&freshness(&cfg, &MetaCache::default())), "fresh");

        fs::remove_file(&cfg.search_db).unwrap();
        search_db(&cfg.search_db, "mock", "m", &[chunk("a", "wiki/a.md", "2000-01-01T00:00:00.5")], &[]);
        // wiki/a.md, docs/b.md, README.md, wiki/sources/s1.md, s2.md — 5 counted, the rest excluded.
        assert_eq!(
            staleness_text(&freshness(&cfg, &MetaCache::default())),
            "STALE — 5 file(s) newer than the index; run `zurvan index search`"
        );
    }

    #[test]
    fn status_lines_in_python_order() {
        let (_d, cfg) = root_with_files();
        search_db(&cfg.search_db, "mock", "deterministic_hash",
                  &[chunk("a", "wiki/x.md", "2999-01-01T00:00:00"), chunk("b", "wiki/x.md", "2999-01-01T00:00:00"),
                    chunk("c", "wiki/y.md", "2999-01-01T00:00:00")],
                  &[("built_from_revision", "745"), ("built_at", "2026-10-18T08:23:19.137571+00:00")]);
        graph_db(&cfg.graph_db, &[("n1", "decision", Some("A"), None)], &[]);
        registry_db(&cfg.registry_db, 751, "CLEAN");
        let text = status_text(&cfg, &MetaCache::default());
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(&lines[..7], &[
            "Search index: fresh",
            "Indexed: 3 chunks across 2 files",
            "Embeddings: mock/deterministic_hash",
            "WARNING: the index holds MOCK embeddings — semantic search is meaningless until it is rebuilt with a real provider.",
            "Graph: 1 nodes / 0 edges",
            "Source pages: 2",
            "Write tools: BLOCKED (read-only mode)",
        ]);
        assert_eq!(lines[7], "Serving: index rev 745 built 2026-10-18T08:23Z · graph rev ?");
        assert_eq!(lines[8], "Ledger: rev 751 CLEAN");
    }

    /// Python output on empty roots. Since IOU-10 was fixed (2026-10-20) the
    /// Python graph reads open read-only, so a missing graph is "unable to
    /// open database file" whether or not `data/` exists, and neither server
    /// creates anything.
    #[test]
    fn status_without_stores_matches_python_messages() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = Config::resolve_from(Some(dir.path().to_path_buf()), |_| None);
        let text = status_text(&cfg, &MetaCache::default());
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines[0], "Search index: missing — run `zurvan index search`");
        assert_eq!(lines[1], format!("Indexed: no search index at {} — run `zurvan index search`.", cfg.search_db.display()));
        assert_eq!(lines[2], "Graph: unavailable (unable to open database file)");
        assert_eq!(lines[3], "Write tools: BLOCKED (read-only mode)");

        fs::create_dir_all(cfg.root.join("data")).unwrap();
        let text = status_text(&cfg, &MetaCache::default());
        assert_eq!(text.lines().nth(2), Some("Graph: unavailable (unable to open database file)"));
        assert!(!cfg.graph_db.exists(), "status must never create the graph store");
        assert!(!cfg.search_db.exists(), "status must never create the search index");
    }

    #[test]
    fn stamp_format() {
        let (_d, cfg) = root_with_files();
        search_db(&cfg.search_db, "sentence_transformers", "all-MiniLM-L6-v2",
                  &[chunk("a", "wiki/a.md", "2000-01-01T00:00:00")],
                  &[("built_from_revision", "745"), ("built_at", "2026-10-18T08:23:19.137571+00:00")]);
        registry_db(&cfg.registry_db, 751, "CLEAN");
        let cache = MetaCache::default();
        let served = Served::Index(cache.index(&cfg).unwrap());
        assert_eq!(
            stamp_for(&cfg, &served, &cache, &[]),
            "[index rev 745 built 2026-10-18T08:23Z · ledger rev 751 · 6 writes since · 5 files newer]"
        );
        assert_eq!(
            stamp_for(&cfg, &served, &cache, &["graph rev 740".into()]),
            "[index rev 745 built 2026-10-18T08:23Z · ledger rev 751 · 6 writes since · 5 files newer · graph rev 740]"
        );
        assert_eq!(stamp_for(&cfg, &Served::LiveFiles, &cache, &[]), "[live files · ledger rev 751]");
        let bare = tempfile::tempdir().unwrap();
        let cfg2 = Config::resolve_from(Some(bare.path().to_path_buf()), |_| None);
        assert_eq!(stamp_for(&cfg2, &Served::LiveFiles, &MetaCache::default(), &[]), "[live files · ledger rev ?]");
        assert_eq!(stamp_for(&cfg2, &Served::NoIndex, &MetaCache::default(), &[]), "[index unavailable · ledger rev ?]");
    }

    #[test]
    fn graph_part_names_the_opened_generation_or_says_superseded() {
        let (_d, cfg) = root_with_files();
        graph_db(&cfg.graph_db, &[], &[]);
        let cache = MetaCache::default();
        let g = crate::store::generation(&cfg.graph_db).unwrap();
        assert_eq!(graph_part(&cfg, &cache, None), None);
        assert_eq!(graph_part(&cfg, &cache, Some(g)).as_deref(), Some("graph rev ?"));
        let stale = crate::store::Generation { ino: g.ino.wrapping_add(1), ..g };
        assert!(graph_part(&cfg, &cache, Some(stale)).unwrap().starts_with("graph superseded"));
    }

    /// Final review minor 7: the 5 s freshness reuse was not keyed by index
    /// generation, so for 5 s after a promote the stamp described the OLD
    /// index's staleness.
    #[test]
    fn freshness_is_recomputed_when_the_index_is_replaced() {
        let (_d, cfg) = root_with_files();
        search_db(&cfg.search_db, "mock", "m", &[chunk("a", "wiki/a.md", "2000-01-01T00:00:00.5")], &[]);
        let cache = MetaCache::default();
        assert_eq!(freshness(&cfg, &cache), Freshness::Newer(5));
        let next = cfg.root.join("next.sqlite");
        search_db(&next, "mock", "m", &[chunk("a", "wiki/a.md", "2999-01-01T00:00:00")], &[]);
        fs::rename(&next, &cfg.search_db).unwrap();
        assert_eq!(freshness(&cfg, &cache), Freshness::Newer(0), "a promoted index is never judged by the old verdict");
    }
}
