//! Paths and environment flags, resolved exactly as `scripts/config.py` does.
//!
//! Root precedence is `--root` > `ZURVAN_ROOT` > the repo path compiled in by
//! `build.rs` (plan decision: the design's order made `--root` unreachable).
//! Flags are read per call, never cached, so a changed environment cannot be
//! served stale — and both fail closed: only the exact value opens them.

use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub root: PathBuf,
    pub search_db: PathBuf,
    pub graph_db: PathBuf,
    pub registry_db: PathBuf,
    pub cache_dir: PathBuf,
}

fn non_empty(v: Option<String>) -> Option<String> {
    v.filter(|s| !s.is_empty())
}

/// `Path.resolve()` for an existing path; the path unchanged otherwise.
fn resolve(p: PathBuf) -> PathBuf {
    p.canonicalize().unwrap_or(p)
}

impl Config {
    pub fn resolve(root_flag: Option<PathBuf>) -> Config {
        Config::resolve_from(root_flag, |k| std::env::var(k).ok())
    }

    pub fn resolve_from(root_flag: Option<PathBuf>, getenv: impl Fn(&str) -> Option<String>) -> Config {
        let root = root_flag
            .or_else(|| non_empty(getenv("ZURVAN_ROOT")).map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from(env!("ZURVAN_BUILD_ROOT")));
        let root = resolve(root);
        let data = root.join("data");
        let search_db = non_empty(getenv("ZURVAN_SEARCH_DB"))
            .map(PathBuf::from)
            .unwrap_or_else(|| data.join("search.sqlite"));
        Config {
            graph_db: data.join("graph.sqlite"),
            registry_db: data.join("registry.sqlite"),
            cache_dir: data.join("cache"),
            search_db,
            root,
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }
}

/// Writes are permitted ONLY when `ZURVAN_MCP_READONLY` is exactly "0" after
/// trimming (Python: `os.environ.get(..., "1").strip() != "0"`).
pub fn readonly() -> bool {
    readonly_from(&|k: &str| std::env::var(k).ok())
}

pub fn readonly_from(getenv: &dyn Fn(&str) -> Option<String>) -> bool {
    getenv("ZURVAN_MCP_READONLY").unwrap_or_else(|| "1".into()).trim() != "0"
}

/// raw/ reads only when `ZURVAN_MCP_ALLOW_RAW_READ` is exactly "1" (untrimmed,
/// as Python compares it).
pub fn allow_raw() -> bool {
    allow_raw_from(&|k: &str| std::env::var(k).ok())
}

pub fn allow_raw_from(getenv: &dyn Fn(&str) -> Option<String>) -> bool {
    getenv("ZURVAN_MCP_ALLOW_RAW_READ").as_deref() == Some("1")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    fn env_of(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |k| pairs.iter().find(|(name, _)| *name == k).map(|(_, v)| v.to_string())
    }

    #[test]
    fn root_precedence() {
        let flag = Config::resolve_from(Some(PathBuf::from("/flag")), env_of(&[("ZURVAN_ROOT", "/env")]));
        assert_eq!(flag.root, Path::new("/flag"));
        let env = Config::resolve_from(None, env_of(&[("ZURVAN_ROOT", "/env")]));
        assert_eq!(env.root, Path::new("/env"));
        let build = Config::resolve_from(None, env_of(&[]));
        assert_eq!(build.root, Path::new(env!("ZURVAN_BUILD_ROOT")));
        assert_eq!(env.graph_db, Path::new("/env/data/graph.sqlite"));
        assert_eq!(env.registry_db, Path::new("/env/data/registry.sqlite"));
        assert_eq!(env.cache_dir, Path::new("/env/data/cache"));
    }

    #[test]
    fn search_db_override() {
        let c = Config::resolve_from(None, env_of(&[("ZURVAN_ROOT", "/env"), ("ZURVAN_SEARCH_DB", "/tmp/x.sqlite")]));
        assert_eq!(c.search_db, Path::new("/tmp/x.sqlite"));
        let d = Config::resolve_from(None, env_of(&[("ZURVAN_ROOT", "/env")]));
        assert_eq!(d.search_db, Path::new("/env/data/search.sqlite"));
        let empty = Config::resolve_from(None, env_of(&[("ZURVAN_ROOT", "/env"), ("ZURVAN_SEARCH_DB", "")]));
        assert_eq!(empty.search_db, Path::new("/env/data/search.sqlite"));
    }

    #[test]
    fn flags_fail_closed() {
        assert!(readonly_from(&env_of(&[])));
        assert!(readonly_from(&env_of(&[("ZURVAN_MCP_READONLY", "true")])));
        assert!(readonly_from(&env_of(&[("ZURVAN_MCP_READONLY", "")])));
        assert!(!readonly_from(&env_of(&[("ZURVAN_MCP_READONLY", " 0 ")])));
        assert!(!allow_raw_from(&env_of(&[("ZURVAN_MCP_ALLOW_RAW_READ", "true")])));
        assert!(allow_raw_from(&env_of(&[("ZURVAN_MCP_ALLOW_RAW_READ", "1")])));
    }
}
