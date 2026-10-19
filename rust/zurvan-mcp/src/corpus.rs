//! The corpus walk Python does with `glob.glob(dir + "/**/*.md", recursive=True)`,
//! in the SAME order: pre-order over directories, each directory's matching
//! entries in `readdir` order, dot-entries skipped, symlinked directories
//! followed. Verified on the live store 2026-10-19: a Python model of this
//! walk reproduced glob's order over all 11,912 files (not name-sorted).
//!
//! Keyword search depends on this order: its scores are small integers, so
//! ties are everywhere and a tie keeps traversal order.

use std::path::{Path, PathBuf};

/// Paths (absolute, `base`-joined) of every `*.md` entry under `top`, in glob order.
pub fn glob_md(top: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if top.is_dir() {
        visit(top, &mut out);
    }
    out
}

fn visit(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    let entries: Vec<(String, PathBuf)> = rd
        .filter_map(Result::ok)
        .map(|e| (e.file_name().to_string_lossy().into_owned(), e.path()))
        .filter(|(name, _)| !name.starts_with('.'))
        .collect();
    // glob's "*.md" matches any entry name (files and directories alike),
    // case-sensitively (posix normcase is the identity).
    for (name, path) in &entries {
        if name.ends_with(".md") {
            out.push(path.clone());
        }
    }
    for (_, path) in &entries {
        if path.is_dir() {
            visit(path, out);
        }
    }
}

/// Repo-relative path with `/` separators.
pub fn rel(root: &Path, p: &Path) -> String {
    p.strip_prefix(root).unwrap_or(p).to_string_lossy().into_owned()
}

/// `scripts/chunk.py::scan_markdown_files`: wiki/ + docs/ (skipping any `raw`
/// or `traces` segment and the `docs/evaluation/retrieval` prefix), then the
/// root index files that exist.
pub const ROOT_INDEX_FILES: [&str; 3] = ["README.md", "AGENTS.md", "CLAUDE.md"];
pub const EXCLUDED_PATH_PREFIXES: [&str; 1] = ["docs/evaluation/retrieval"];

pub fn scan_markdown_files(root: &Path) -> Vec<String> {
    let mut files = Vec::new();
    for top in ["wiki", "docs"] {
        for p in glob_md(&root.join(top)) {
            let r = rel(root, &p);
            if r.split('/').any(|seg| seg == "raw" || seg == "traces") {
                continue;
            }
            if EXCLUDED_PATH_PREFIXES.iter().any(|x| r == *x || r.starts_with(&format!("{x}/"))) {
                continue;
            }
            files.push(r);
        }
    }
    for name in ROOT_INDEX_FILES {
        if root.join(name).is_file() {
            files.push(name.to_string());
        }
    }
    files
}
