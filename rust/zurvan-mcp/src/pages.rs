//! `zurvan_read_page`, the resources and the prompts — ports of
//! `scripts/mcp_tools.tool_zurvan_read_page` and `scripts/mcp_resources.py`,
//! with the read allow-list of design §5 in front of every file read.

use crate::config::{allow_raw, Config};
use crate::security::{is_readable_path, open_target, unsafe_path_error, Refusal};
use crate::text::{budget, read_universal, ReadError};
use std::path::Path;

/// A page larger than this is delivered in slices rather than refused.
pub const PAGE_SLICE_CHARS: usize = 24_000;
/// `resource_file` refuses files above this many BYTES (Python `os.path.getsize`).
pub const RESOURCE_FILE_MAX_BYTES: u64 = 256 * 1024;

fn not_found(path: &str) -> String {
    format!("Error: File {path} not found. Locate it with zurvan_search first.")
}

fn read_error(cfg: &Config, path: &str, e: ReadError) -> String {
    match e {
        ReadError::Binary => "Error: File appears to be binary.".to_string(),
        ReadError::NotFound => not_found(path),
        ReadError::IsDir => format!(
            "Error reading file: [Errno 21] Is a directory: '{}'",
            cfg.root.join(path).display()
        ),
        ReadError::Io(msg) => format!("Error reading file: {msg}"),
    }
}

/// The checked, canonical file to open, or the Python-shaped error text.
fn checked(cfg: &Config, path: &str) -> Result<std::path::PathBuf, String> {
    if !is_readable_path(&cfg.root, path, allow_raw()) {
        return Err(unsafe_path_error(path));
    }
    match open_target(&cfg.root, path, allow_raw()) {
        Ok(p) => Ok(p),
        Err(Refusal::NotFound) => Err(not_found(path)),
        Err(Refusal::Unsafe) => Err(unsafe_path_error(path)),
    }
}

/// `scripts/mcp_resources.resource_file`.
pub fn resource_file(cfg: &Config, path: &str) -> String {
    let target = match checked(cfg, path) {
        Ok(p) => p,
        Err(e) => return e,
    };
    if let Ok(m) = std::fs::metadata(&target) {
        if m.is_file() && m.len() > RESOURCE_FILE_MAX_BYTES {
            return "Error: File too large (> 256 KB). Read it in slices with \
                    zurvan_read_page(path, offset=0) and advance the offset it returns."
                .to_string();
        }
    }
    match read_universal(&target) {
        Ok(s) => s,
        Err(e) => read_error(cfg, path, e),
    }
}

/// `scripts/mcp_resources.read_file_slice`: offsets count code points after
/// universal-newline translation, exactly as Python text mode reads.
pub fn read_file_slice(cfg: &Config, path: &str, offset: usize, limit: usize) -> String {
    let target = match checked(cfg, path) {
        Ok(p) => p,
        Err(e) => return e,
    };
    let content = match read_universal(&target) {
        Ok(s) => s,
        Err(e) => return read_error(cfg, path, e),
    };
    let mut chars = content.char_indices().skip(offset);
    let Some((start, _)) = chars.next() else {
        return format!("[end of {path} — offset {offset} is past the end]");
    };
    let rest = &content[start..];
    let end = rest.char_indices().nth(limit).map(|(i, _)| i).unwrap_or(rest.len());
    let chunk = &rest[..end];
    let n = chunk.chars().count();
    let mut header = format!("[{path} — characters {offset}-{}]", offset + n);
    if end < rest.len() {
        header.push_str(&format!(" More: call again with offset={}.", offset + n));
    }
    format!("{header}\n{chunk}")
}

/// `tool_zurvan_read_page`. At offset 0 a file up to 256 KB is returned whole
/// (budgeted to 24,000 characters, ignoring `limit`, as Python does); larger
/// files and any non-zero offset are sliced.
pub fn read_page(cfg: &Config, path: &str, offset: Option<i64>, limit: Option<i64>) -> String {
    let offset = offset.unwrap_or(0).max(0) as usize;
    let limit = limit.unwrap_or(PAGE_SLICE_CHARS as i64).clamp(1, PAGE_SLICE_CHARS as i64) as usize;
    if offset == 0 {
        let content = resource_file(cfg, path);
        if !content.starts_with("Error: File too large") {
            return if content.chars().count() > PAGE_SLICE_CHARS {
                budget(&content, PAGE_SLICE_CHARS)
            } else {
                content
            };
        }
    }
    read_file_slice(cfg, path, offset, limit)
}

/// `get_static_resource` for the fixed wiki/eval resources.
fn static_resource(abs: &Path) -> String {
    match read_universal(abs) {
        Ok(s) => s,
        Err(ReadError::NotFound) => format!("Error: Resource {} not found.", abs.display()),
        Err(ReadError::Binary) => "Error reading resource: 'utf-8' codec can't decode the file".to_string(),
        Err(ReadError::IsDir) => format!("Error reading resource: [Errno 21] Is a directory: '{}'", abs.display()),
        Err(ReadError::Io(m)) => format!("Error reading resource: {m}"),
    }
}

/// A resource's text, or `None` for an unknown URI (a JSON-RPC error).
/// `graph_stats` is supplied by the caller (it needs the graph store).
pub fn resource(cfg: &Config, uri: &str, graph_stats: impl FnOnce() -> String) -> Option<String> {
    let wiki = |name: &str| static_resource(&cfg.root.join("wiki").join(name));
    Some(match uri {
        "zurvan://wiki/index" => wiki("index.md"),
        "zurvan://wiki/log" => wiki("log.md"),
        "zurvan://wiki/overview" => wiki("overview.md"),
        "zurvan://wiki/open-questions" => wiki("open-questions.md"),
        "zurvan://graph/stats" => graph_stats(),
        "zurvan://eval/baseline" => static_resource(&cfg.root.join("eval").join("README.md")),
        other => match other.strip_prefix("zurvan://file/") {
            Some(path) if !path.is_empty() => resource_file(cfg, path),
            _ => return None,
        },
    })
}

/// Prompt name → (description, text), copied verbatim from
/// `scripts/mcp_server.py` docstrings and `scripts/mcp_prompts.py`.
pub fn prompt(name: &str) -> Option<(&'static str, &'static str)> {
    Some(match name {
        "zurvan_project_brief" => (
            "Orientation brief: how to interact with the Zurvan knowledge engine.",
            "You are an agent interacting with the Zurvan Local-first AI knowledge engine. Zurvan holds project knowledge securely without internal LLMs. Read AGENTS.md for constraints and use zurvan_search to orient yourself.",
        ),
        "zurvan_pre_edit_context" => (
            "Checklist to gather relevant context before editing files.",
            "Before editing files, run zurvan_context or zurvan_search to understand the decisions, claims, and contradictions surrounding the topic. Ensure you respect past decisions recorded in Zurvan.",
        ),
        "zurvan_post_edit_memory" => (
            "Reminder to persist new decisions/claims/notes after an edit.",
            "If your edit made a new decision, claim, or note, use the write tools (if ZURVAN_MCP_READONLY=0) to record it into Zurvan so future agents will know.",
        ),
        "zurvan_research_audit" => (
            "Workflow for researching a topic thoroughly using search + graph expand.",
            "Run a search on the topic you are researching. Then use zurvan_graph_expand to find nearby concepts and notes to build a comprehensive picture before answering.",
        ),
        _ => return None,
    })
}
