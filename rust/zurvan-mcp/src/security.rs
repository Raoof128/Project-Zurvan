//! Path safety and the MCP read allow-list (design §5).
//!
//! `is_safe_path` reproduces `scripts/mcp_security.is_safe_path` exactly: no
//! absolute paths, the resolved target must stay under the root, and `raw/`
//! (case-insensitive on its first component, for macOS) needs the flag.
//!
//! `is_readable_path` adds the allow-list the Python server lacks. On
//! 2026-10-19 the live Python server returned `.git/config`,
//! `.claude/settings.json`, `.remember/now.md` and an ingested legal document
//! under `sources/` through `zurvan_read_page`, because a deny-list of three
//! rules is all it has. The readable surface is now what the search index
//! covers plus the four repo-root docs, and every case variant of an allowed
//! name fails closed (`WIKI/…`), while every case variant of an EXCLUDED name
//! is still excluded (`wiki/TRACES/…`), because macOS resolves both.

use std::path::{Component, Path, PathBuf};

pub const READONLY_ERROR: &str =
    "Error: MCP server is in read-only mode (set ZURVAN_MCP_READONLY=0 to enable writes). Write operation blocked.";

pub fn unsafe_path_error(path: &str) -> String {
    format!("Error: Path {path} failed safety checks (no absolute paths, no '../', raw/ blocked).")
}

const ROOT_DOCS: [&str; 4] = ["README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"];
const MAX_SYMLINK_HOPS: usize = 40;

/// `os.path.realpath`: resolve component by component, following symlinks
/// that exist and keeping the rest lexically, applying `..` to the RESOLVED
/// prefix (so `link/..` is the link target's parent, as in Python).
pub fn resolve_lenient(path: &Path) -> Option<PathBuf> {
    let mut hops = 0usize;
    resolve_into(PathBuf::new(), path, &mut hops)
}

fn resolve_into(mut acc: PathBuf, path: &Path, hops: &mut usize) -> Option<PathBuf> {
    for comp in path.components() {
        match comp {
            Component::RootDir | Component::Prefix(_) => acc = PathBuf::from("/"),
            Component::CurDir => {}
            Component::ParentDir => {
                acc.pop();
            }
            Component::Normal(name) => {
                let next = acc.join(name);
                match std::fs::symlink_metadata(&next) {
                    Ok(m) if m.file_type().is_symlink() => {
                        *hops += 1;
                        if *hops > MAX_SYMLINK_HOPS {
                            return None;
                        }
                        let target = std::fs::read_link(&next).ok()?;
                        acc = resolve_into(acc, &target, hops)?;
                    }
                    _ => acc = next,
                }
            }
        }
    }
    Some(acc)
}

fn rel_components(root: &Path, target: &str) -> Option<Vec<String>> {
    if target.starts_with('/') || Path::new(target).is_absolute() {
        return None;
    }
    let resolved = resolve_lenient(&root.join(target))?;
    let rel = resolved.strip_prefix(root).ok()?;
    Some(
        rel.components()
            .filter_map(|c| match c {
                Component::Normal(n) => Some(n.to_string_lossy().into_owned()),
                _ => None,
            })
            .collect(),
    )
}

fn is_named(c: &str, name: &str) -> bool {
    c.eq_ignore_ascii_case(name)
}

/// `scripts/mcp_security.is_safe_path`. `root` must already be resolved.
pub fn is_safe_path(root: &Path, target: &str, allow_raw: bool) -> bool {
    match rel_components(root, target) {
        None => false,
        Some(parts) => match parts.first() {
            Some(first) if is_named(first, "raw") => allow_raw,
            _ => true,
        },
    }
}

/// Safe AND on the allow-list: `wiki/` (no `traces` segment, any case),
/// `docs/`, `eval/`, the four root docs; `raw/` only with the flag.
pub fn is_readable_path(root: &Path, target: &str, allow_raw: bool) -> bool {
    let Some(parts) = rel_components(root, target) else { return false };
    let Some(first) = parts.first() else { return false };
    if is_named(first, "raw") {
        return allow_raw;
    }
    match first.as_str() {
        "wiki" => !parts.iter().skip(1).any(|c| is_named(c, "traces")),
        "docs" | "eval" => true,
        name => parts.len() == 1 && ROOT_DOCS.contains(&name),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    /// A throwaway tree holding every top-level directory the 2026-10-19 live
    /// probe reached, plus a symlink that escapes the root.
    fn fixture() -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for d in ["wiki/traces", "wiki/sources", "docs", "eval", ".git", ".claude", ".remember",
                  "sources", "data", "raw", "scripts"] {
            fs::create_dir_all(root.join(d)).unwrap();
        }
        for f in ["wiki/index.md", "wiki/traces/x.md", "wiki/sources/x.md", "docs/API.md", "eval/README.md",
                  ".git/HEAD", ".git/config", ".claude/settings.json", ".remember/now.md", "sources/x.md",
                  "data/registry.sqlite", "raw/x.md", "scripts/cli.py",
                  "README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"] {
            fs::write(root.join(f), "x").unwrap();
        }
        std::os::unix::fs::symlink("/etc", root.join("wiki/escape")).unwrap();
        (dir, root)
    }

    #[test]
    fn read_gate_refuses_probe_paths() {
        let (_d, root) = fixture();
        for p in [".git/HEAD", "wiki/../.git/config", ".claude/settings.json", ".remember/now.md",
                  "sources/x.md", "data/registry.sqlite", "wiki/traces/x.md", "scripts/cli.py",
                  "/etc/passwd", "../outside.md", "wiki/escape/passwd", "", "."] {
            assert!(!is_readable_path(&root, p, false), "{p:?} should be refused");
        }
    }

    #[test]
    fn read_gate_allows_corpus() {
        let (_d, root) = fixture();
        for p in ["wiki/index.md", "wiki/sources/x.md", "docs/API.md", "eval/README.md",
                  "README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md", "wiki/./index.md",
                  "docs/../wiki/index.md", "wiki/new-page-not-yet-written.md"] {
            assert!(is_readable_path(&root, p, false), "{p:?} should be allowed");
        }
    }

    #[test]
    fn traces_exclusion_is_case_insensitive() {
        let (_d, root) = fixture();
        assert!(!is_readable_path(&root, "wiki/TRACES/x.md", false));
        assert!(!is_readable_path(&root, "wiki/sub/Traces/y.md", false));
    }

    #[test]
    fn allow_list_fails_closed_on_case_variant() {
        let (_d, root) = fixture();
        assert!(!is_readable_path(&root, "WIKI/index.md", false));
        assert!(!is_readable_path(&root, ".GIT/HEAD", false));
        assert!(!is_readable_path(&root, "readme.md", false));
    }

    #[test]
    fn raw_needs_flag_any_case() {
        let (_d, root) = fixture();
        assert!(!is_readable_path(&root, "Raw/x.md", false));
        assert!(!is_readable_path(&root, "raw/x.md", false));
        assert!(is_readable_path(&root, "raw/x.md", true));
        assert!(!is_safe_path(&root, "RAW/x.md", false));
        assert!(is_safe_path(&root, "RAW/x.md", true));
    }

    #[test]
    fn safe_path_matches_python_for_non_allowlist() {
        // Documents the Python gap the allow-list closes: is_safe_path alone
        // admits these, exactly as scripts/mcp_security.py does today.
        let (_d, root) = fixture();
        assert!(is_safe_path(&root, ".git/HEAD", false));
        assert!(is_safe_path(&root, "sources/x.md", false));
        assert!(!is_safe_path(&root, "/etc/passwd", false));
        assert!(!is_safe_path(&root, "../outside.md", false));
        assert!(!is_safe_path(&root, "wiki/escape/passwd", false));
        assert!(!is_safe_path(&root, "wiki/../../outside.md", false));
    }

    #[test]
    fn error_texts_are_exact() {
        assert_eq!(READONLY_ERROR, "Error: MCP server is in read-only mode (set ZURVAN_MCP_READONLY=0 to enable writes). Write operation blocked.");
        assert_eq!(unsafe_path_error(".git/HEAD"), "Error: Path .git/HEAD failed safety checks (no absolute paths, no '../', raw/ blocked).");
    }
}
