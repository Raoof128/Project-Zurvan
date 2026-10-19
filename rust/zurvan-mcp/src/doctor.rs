//! `zurvan-mcp doctor` (design §7A A8): every check an agent on a fresh
//! machine needs, each failing line followed by the command that fixes it.

use crate::bridge::{BridgeEnv, MISSING_PYTHON};
use crate::client::Client;
use crate::config::{readonly, Config};
use crate::embed::{locate_model, Space};
use crate::status::{freshness, Freshness};
use crate::store::{ledger, MetaCache};
use crate::vectors::{cache_key, VectorCache};
use std::process::Command;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Ok,
    Warn,
    Fail,
}

pub struct Check {
    pub level: Level,
    pub name: &'static str,
    pub detail: String,
    pub fix: Option<String>,
}

fn check(level: Level, name: &'static str, detail: impl Into<String>, fix: Option<&str>) -> Check {
    Check { level, name, detail: detail.into(), fix: fix.map(String::from) }
}

pub fn checks(cfg: &Config) -> Vec<Check> {
    use Level::*;
    let mut out = Vec::new();
    out.push(if cfg.root.join("wiki").is_dir() {
        check(Ok, "root", cfg.root.display().to_string(), None)
    } else {
        check(Fail, "root", format!("{} has no wiki/", cfg.root.display()), Some("pass --root <store> or set ZURVAN_ROOT"))
    });

    let meta = MetaCache::default();
    let index = meta.index(cfg);
    match &index {
        std::result::Result::Ok(m) => out.push(check(
            Ok,
            "search index",
            format!(
                "rev {}, {} chunks, {}/{}",
                m.built_from_revision.map(|r| r.to_string()).unwrap_or_else(|| "?".into()),
                m.chunks,
                m.provider.as_deref().unwrap_or("None"),
                m.model.as_deref().unwrap_or("None")
            ),
            None,
        )),
        Err(e) => out.push(check(Fail, "search index", format!("{}: {e}", cfg.search_db.display()), Some("zurvan index search"))),
    }
    match freshness(cfg, &meta) {
        Freshness::Newer(0) => out.push(check(Ok, "freshness", "fresh", None)),
        Freshness::Newer(n) => out.push(check(Warn, "freshness", format!("{n} file(s) newer than the index"), Some("zurvan index search"))),
        Freshness::Unknown(e) => out.push(check(Warn, "freshness", e, None)),
        _ => {}
    }

    let bridge = BridgeEnv::from_env(cfg);
    match (&bridge.python, &index) {
        (Some(py), std::result::Result::Ok(_)) => {
            let ok = Command::new(py)
                .arg(bridge.code_root.join("scripts").join("index_health.py"))
                .arg("--db")
                .arg(&cfg.search_db)
                .env("PYTHONPATH", &bridge.code_root)
                .env("ZURVAN_ROOT", &cfg.root)
                .output()
                .map(|o| o.status.success());
            out.push(match ok {
                std::result::Result::Ok(true) => check(Ok, "index health", "scripts/index_health.py passed", None),
                _ => check(Warn, "index health", "scripts/index_health.py reports a problem", Some("python scripts/index_health.py --deep")),
            });
        }
        (None, _) => out.push(check(Warn, "index health", "skipped (ZURVAN_PYTHON not set)", None)),
        _ => {}
    }

    out.push(match meta.graph(cfg) {
        std::result::Result::Ok(g) => check(Ok, "graph", format!("{} nodes / {} edges", g.nodes, g.edges), None),
        Err(e) => check(Warn, "graph", e, Some("zurvan graph rebuild")),
    });

    out.push(match ledger(cfg) {
        Some((rev, state)) if state == "CLEAN" => check(Ok, "ledger", format!("rev {rev} CLEAN"), None),
        Some((rev, state)) => check(Warn, "ledger", format!("rev {rev} {state}"), Some("zurvan knowledge reconcile")),
        None => check(Warn, "ledger", "no registry yet (nothing has been written)", None),
    });

    if let std::result::Result::Ok(m) = &index {
        match Space::for_index(m.provider.as_deref(), m.model.as_deref()) {
            Space::Real => out.push(match locate_model() {
                std::result::Result::Ok(d) => check(Ok, "model", d.display().to_string(), None),
                Err(e) => check(
                    Fail,
                    "model",
                    e,
                    Some("python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')\"  (or set ZURVAN_MODEL_DIR)"),
                ),
            }),
            Space::Mock => out.push(check(Warn, "model", "the index holds MOCK embeddings; semantic ranking is meaningless", Some("zurvan index search  (with ZURVAN_EMBED_PROVIDER=sentence_transformers)"))),
            Space::Unsupported(why) => out.push(check(Fail, "model", why, Some("rebuild the index with all-MiniLM-L6-v2"))),
        }
        let key = cache_key(m);
        let path = cfg.cache_dir.join(format!("vectors-{key}.zvec"));
        out.push(match VectorCache::open(&path, &key, m.embeddings as u64) {
            std::result::Result::Ok(c) => check(Ok, "vector cache", format!("{} rows", c.rows()), None),
            Err(_) => check(Warn, "vector cache", "not built for this index generation (first hybrid search builds it)", Some("zurvan-mcp build-cache")),
        });
    }

    out.push(match &bridge.python {
        Some(py) => {
            let ok = Command::new(py).args(["-c", "import scripts.mcp_tools"]).env("PYTHONPATH", &bridge.code_root).output().map(|o| o.status.success());
            match ok {
                std::result::Result::Ok(true) => check(Ok, "write bridge", format!("{} imports scripts.mcp_tools", py.display()), None),
                _ => check(Fail, "write bridge", format!("{} cannot import scripts.mcp_tools from {}", py.display(), bridge.code_root.display()), Some("set ZURVAN_PYTHON to an interpreter with Zurvan's dependencies (scripts/install_mcp_config.py --engine rust)")),
            }
        }
        None if readonly() => check(Warn, "write bridge", "ZURVAN_PYTHON not set (writes are disabled anyway: read-only mode)", None),
        None => check(Fail, "write bridge", MISSING_PYTHON.trim_start_matches("Error: "), Some("scripts/install_mcp_config.py --engine rust")),
    });

    for protocol in ["2025-11-25", "2026-07-28"] {
        let r = Client::spawn(&cfg.root, protocol).and_then(|(mut c, opened)| {
            let n = c.request("tools/list", serde_json::json!({}))?["tools"].as_array().map(|a| a.len()).unwrap_or(0);
            let instructions = opened["instructions"].as_str().is_some_and(|s| !s.is_empty());
            std::result::Result::Ok((n, instructions))
        });
        out.push(match r {
            std::result::Result::Ok((n, true)) if n == crate::server::tool_count() => check(Ok, "protocol", format!("{protocol}: {n} tools, instructions present"), None),
            std::result::Result::Ok((n, i)) => check(Fail, "protocol", format!("{protocol}: {n} tools, instructions present: {i}"), None),
            Err(e) => check(Fail, "protocol", format!("{protocol}: {e}"), None),
        });
    }
    out
}

/// The report text and whether it passed (no FAIL).
pub fn run(cfg: &Config) -> (String, bool) {
    let cs = checks(cfg);
    let mut lines = Vec::new();
    for c in &cs {
        let level = match c.level {
            Level::Ok => "OK",
            Level::Warn => "WARN",
            Level::Fail => "FAIL",
        };
        lines.push(format!("{level} {}: {}", c.name, c.detail));
        if c.level != Level::Ok {
            if let Some(f) = &c.fix {
                lines.push(format!("fix: {f}"));
            }
        }
    }
    let ok = !cs.iter().any(|c| c.level == Level::Fail);
    lines.push(format!("doctor: {}", if ok { "PASS" } else { "FAIL" }));
    (lines.join("\n"), ok)
}
