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

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Refusal {
    Unsafe,
    NotFound,
}

const ROOT_DOCS: [&str; 4] = ["README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"];

/// The path the filesystem itself would name: the longest existing ancestor
/// through `std::fs::canonicalize` (realpath(3): symlinks followed AND, on a
/// case-insensitive volume, the names as stored), then the non-existent
/// remainder normalised lexically.
///
/// Checks must run on stored names. Security review 2026-10-19: APFS folds
/// case with full Unicode rules, so `wiki/trace\u{17f}` (long s) opens the
/// real `traces` directory while any comparison against the REQUESTED string
/// sees a different name. Measured: libc realpath returns `traces`.
pub fn canonical_lenient(path: &Path) -> Option<PathBuf> {
    let comps: Vec<Component> = path.components().collect();
    let mut probe = PathBuf::new();
    let mut base: Option<PathBuf> = None;
    let mut existing = 0usize;
    for comp in &comps {
        probe.push(comp);
        match std::fs::canonicalize(&probe) {
            Ok(c) => {
                base = Some(c);
                existing += 1;
            }
            Err(_) => {
                // A symlink that does not resolve (dangling, or a loop) names a
                // place this function cannot see: fail closed rather than judge
                // the link's own name.
                if std::fs::symlink_metadata(&probe).is_ok_and(|m| m.file_type().is_symlink()) {
                    return None;
                }
                break;
            }
        }
    }
    let mut acc = base?;
    for comp in &comps[existing..] {
        match comp {
            Component::CurDir => {}
            // `..` after a component that does not exist cannot be resolved by
            // the filesystem; a lexical pop would judge a path nothing opens.
            Component::ParentDir => return None,
            Component::Normal(name) => acc.push(name),
            Component::RootDir | Component::Prefix(_) => return None,
        }
    }
    Some(acc)
}

fn rel_of(root: &Path, resolved: &Path) -> Option<Vec<String>> {
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

/// The root as the filesystem names it, so `strip_prefix` against canonical
/// targets works whatever form the caller passed (`/var` vs `/private/var`).
fn canonical_root(root: &Path) -> PathBuf {
    root.canonicalize().unwrap_or_else(|_| root.to_path_buf())
}

fn rel_components(root: &Path, target: &str) -> Option<Vec<String>> {
    if target.starts_with('/') || Path::new(target).is_absolute() {
        return None;
    }
    let root = canonical_root(root);
    rel_of(&root, &canonical_lenient(&root.join(target))?)
}

/// The path a reader may open: it must EXIST, its full canonical form must
/// stay under `root`, and the allow-list is applied to that canonical form.
/// Readers open the returned path, never the requested string, so nothing
/// between the check and the open can be re-interpreted.
pub fn open_target(root: &Path, target: &str, allow_raw: bool) -> Result<PathBuf, Refusal> {
    if !is_readable_path(root, target, allow_raw) {
        return Err(Refusal::Unsafe);
    }
    let root = canonical_root(root);
    let canonical = std::fs::canonicalize(root.join(target)).map_err(|_| Refusal::NotFound)?;
    match rel_of(&root, &canonical) {
        Some(parts) if allowed(&parts, allow_raw) => Ok(canonical),
        _ => Err(Refusal::Unsafe),
    }
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
/// `docs/` (minus `docs/evaluation/retrieval/`), `eval/`, the four root docs;
/// `raw/` only with the flag.
pub fn is_readable_path(root: &Path, target: &str, allow_raw: bool) -> bool {
    match rel_components(root, target) {
        Some(parts) => allowed(&parts, allow_raw),
        None => false,
    }
}

/// The allow-list over root-relative components. Allowed names compare
/// exactly (a case variant fails closed); excluded names compare
/// case-insensitively (a case variant is still excluded). The components are
/// the STORED names wherever the path exists (`canonical_lenient`).
fn allowed(parts: &[String], allow_raw: bool) -> bool {
    let Some(first) = parts.first() else { return false };
    if is_named(first, "raw") {
        return allow_raw;
    }
    match first.as_str() {
        "wiki" => !parts.iter().skip(1).any(|c| is_named(c, "traces")),
        // Excluded from the index (scripts/chunk.py EXCLUDED_PATH_PREFIXES) so
        // reports about the retriever cannot feed it; they can quote the
        // retired instrument, so they are not readable either.
        "docs" => !(parts.len() >= 3 && is_named(&parts[1], "evaluation") && is_named(&parts[2], "retrieval")),
        "eval" => true,
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

    /// Checks run on STORED names, so on a case-insensitive volume a case
    /// variant is judged as the entry it actually opens: an allowed page stays
    /// that page, and nothing outside the allow-list becomes reachable.
    #[test]
    fn case_variants_are_judged_by_stored_names() {
        let (_d, root) = fixture();
        assert!(!is_readable_path(&root, ".GIT/HEAD", false));
        assert!(!is_readable_path(&root, "Sources/x.md", false));
        assert!(!is_readable_path(&root, "wiki/../.Claude/settings.json", false));
        if root.join("WIKI").exists() {
            assert_eq!(open_target(&root, "WIKI/index.md", false).unwrap(), root.join("wiki/index.md"));
            assert_eq!(open_target(&root, "readme.md", false).unwrap(), root.join("README.md"));
        } else {
            assert!(!is_readable_path(&root, "WIKI/index.md", false));
        }
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

    /// Security review 2026-10-19: APFS folds case with full Unicode rules, so
    /// "trace\u{17f}" (long s) opens the real `traces` directory. Measured on
    /// this machine before the fix: the ASCII comparison let it through.
    #[test]
    fn unicode_case_variant_of_traces_is_refused() {
        let (_d, root) = fixture();
        if !root.join("wiki/TRACES").exists() {
            return; // case-sensitive filesystem: the variant cannot open the directory
        }
        assert!(!is_readable_path(&root, "wiki/trace\u{17f}/x.md", false));
        assert!(!is_readable_path(&root, "wiki/TRACE\u{17f}/x.md", false));
        assert!(open_target(&root, "wiki/trace\u{17f}/x.md", false).is_err());
    }

    #[test]
    fn open_target_returns_the_canonical_path() {
        let (_d, root) = fixture();
        assert_eq!(open_target(&root, "wiki/./index.md", false).unwrap(), root.join("wiki/index.md"));
        assert_eq!(open_target(&root, "docs/../wiki/index.md", false).unwrap(), root.join("wiki/index.md"));
        assert_eq!(open_target(&root, "wiki/missing.md", false), Err(Refusal::NotFound));
        assert_eq!(open_target(&root, ".git/HEAD", false), Err(Refusal::Unsafe));
        // A nonexistent intermediate never reaches the symlink behind it.
        assert!(open_target(&root, "wiki/nope/../escape/passwd", false).is_err());
        assert_eq!(open_target(&root, "wiki/escape/passwd", false), Err(Refusal::Unsafe));
    }

    /// Security review 2026-10-19 (second pass): `..` after a component that
    /// does not exist was applied lexically, and a dangling symlink was pushed
    /// as a plain name. Neither can be read through open_target, but the
    /// lenient gate answered "allowed"; it now fails closed.
    #[test]
    fn parent_dir_after_missing_component_is_refused() {
        let (_d, root) = fixture();
        assert!(!is_readable_path(&root, "wiki/nope/../index.md", false));
        assert!(!is_readable_path(&root, "wiki/nope/../escape/passwd", false));
        assert!(!is_safe_path(&root, "wiki/nope/../../.git/config", false));
    }

    #[test]
    fn dangling_symlink_is_refused() {
        let (_d, root) = fixture();
        std::os::unix::fs::symlink("/nonexistent-zurvan-target/x.md", root.join("wiki/dangling.md")).unwrap();
        assert!(!is_readable_path(&root, "wiki/dangling.md", false));
        assert!(!is_safe_path(&root, "wiki/dangling.md", false));
    }

    #[test]
    fn error_texts_are_exact() {
        assert_eq!(READONLY_ERROR, "Error: MCP server is in read-only mode (set ZURVAN_MCP_READONLY=0 to enable writes). Write operation blocked.");
        assert_eq!(unsafe_path_error(".git/HEAD"), "Error: Path .git/HEAD failed safety checks (no absolute paths, no '../', raw/ blocked).");
    }

    /// Final review minor 9: `docs/evaluation/retrieval/` is excluded from the
    /// index so reports about the retriever cannot feed it, and those reports
    /// can quote the retired instrument. The read surface excludes it too.
    #[test]
    fn retrieval_reports_are_not_readable() {
        let (_d, root) = fixture();
        fs::create_dir_all(root.join("docs/evaluation/retrieval")).unwrap();
        fs::write(root.join("docs/evaluation/retrieval/report.md"), "x").unwrap();
        fs::write(root.join("docs/evaluation/other.md"), "x").unwrap();
        assert!(!is_readable_path(&root, "docs/evaluation/retrieval/report.md", false));
        assert!(!is_readable_path(&root, "docs/evaluation/retrieval", false));
        assert!(!is_readable_path(&root, "docs/Evaluation/RETRIEVAL/report.md", false));
        assert!(open_target(&root, "docs/evaluation/retrieval/report.md", false).is_err());
        assert!(is_readable_path(&root, "docs/evaluation/other.md", false));
    }
}
