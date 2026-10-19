//! Text helpers that must reproduce `scripts/mcp_tools.py` and Python's text
//! I/O byte for byte: code-point (not byte) counts, Python's whitespace set,
//! universal newlines, and Python's `None` / `{:,}` rendering.

use std::path::Path;

pub const DEFAULT_PAGE: usize = 25;
pub const MAX_PAGE: usize = 200;
pub const RESULT_CHAR_BUDGET: usize = 24_000;

/// Python's `str.isspace()` set: Unicode White_Space plus the four
/// information separators U+001C..U+001F, which Rust does not count.
pub fn py_isspace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `" ".join(text.split())`.
pub fn collapse_ws(text: &str) -> String {
    text.split(py_isspace).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `str.rstrip()`.
pub fn py_rstrip(s: &str) -> &str {
    s.trim_end_matches(py_isspace)
}

/// First `n` code points of `s` (Python `s[:n]`).
pub fn take_chars(s: &str, n: usize) -> &str {
    match s.char_indices().nth(n) {
        Some((i, _)) => &s[..i],
        None => s,
    }
}

/// `_snippet`: collapse whitespace; truncate to `limit` code points + "…".
pub fn snippet(text: &str, limit: usize) -> String {
    if text.is_empty() {
        return String::new();
    }
    let flat = collapse_ws(text);
    if flat.chars().count() <= limit {
        return flat;
    }
    format!("{}…", py_rstrip(take_chars(&flat, limit)))
}

/// `_page_bounds`: a missing limit becomes 25, then clamp to 1..=200; a
/// missing or negative cursor becomes 0.
pub fn page_bounds(limit: Option<i64>, cursor: Option<i64>) -> (usize, usize) {
    let limit = limit.unwrap_or(DEFAULT_PAGE as i64).clamp(1, MAX_PAGE as i64) as usize;
    let offset = cursor.unwrap_or(0).max(0) as usize;
    (limit, offset)
}

/// `_paginate`: one slice plus a header naming the next cursor.
pub fn paginate<T: Clone>(items: &[T], limit: Option<i64>, cursor: Option<i64>) -> (Vec<T>, String) {
    let (limit, offset) = page_bounds(limit, cursor);
    let total = items.len();
    let page: Vec<T> = items.iter().skip(offset).take(limit).cloned().collect();
    let shown_to = offset + page.len();
    let mut header = format!("Showing {}-{} of {}.", offset + 1, shown_to, total);
    if shown_to < total {
        header.push_str(&format!(" More available: call again with cursor={shown_to}."));
    }
    (page, header)
}

/// `_budget`: hard character cap, cut back to the last newline inside it.
pub fn budget(text: &str, budget: usize) -> String {
    let total = text.chars().count();
    if total <= budget {
        return text.to_string();
    }
    let head = take_chars(text, budget);
    let kept = match head.rfind('\n') {
        Some(i) => &head[..i],
        None => head,
    };
    let dropped = total - kept.chars().count();
    format!(
        "{kept}\n\n[truncated: {} more characters. Narrow the query or use a smaller limit / the next cursor.]",
        thousands(dropped as i64)
    )
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReadError {
    NotFound,
    IsDir,
    Binary,
    Io(String),
}

/// Python `open(path, encoding="utf-8").read()`: strict UTF-8 (a BOM is kept
/// as U+FEFF) and universal newlines (`\r\n` and lone `\r` become `\n`).
pub fn read_universal(path: &Path) -> Result<String, ReadError> {
    let meta = std::fs::metadata(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => ReadError::NotFound,
        _ => ReadError::Io(e.to_string()),
    })?;
    if meta.is_dir() {
        return Err(ReadError::IsDir);
    }
    let bytes = std::fs::read(path).map_err(|e| ReadError::Io(e.to_string()))?;
    let s = String::from_utf8(bytes).map_err(|_| ReadError::Binary)?;
    Ok(universal_newlines(&s))
}

pub fn universal_newlines(s: &str) -> String {
    if !s.contains('\r') {
        return s.to_string();
    }
    s.replace("\r\n", "\n").replace('\r', "\n")
}

/// Python's `str(None)` for an optional column.
pub fn py_str(o: Option<&str>) -> &str {
    o.unwrap_or("None")
}

/// Python's `f"{n:,}"`.
pub fn thousands(n: i64) -> String {
    let digits = n.unsigned_abs().to_string();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3 + 1);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    if n < 0 {
        format!("-{out}")
    } else {
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn tmp_with(bytes: &[u8]) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        f.write_all(bytes).unwrap();
        f
    }

    #[test]
    fn snippet_collapses_and_truncates() {
        assert_eq!(snippet("a \n\t b", 200), "a b");
        assert_eq!(snippet(&"x".repeat(250), 200), format!("{}…", "x".repeat(200)));
        assert_eq!(snippet("ab  \u{1c}cd", 200), "ab cd"); // Python str.split() treats \x1c-\x1f as whitespace
        assert_eq!(snippet("", 200), "");
    }

    #[test]
    fn paginate_matches_python_headers() {
        let v: Vec<i32> = (0..60).collect();
        assert_eq!(paginate(&v, Some(25), Some(0)).1, "Showing 1-25 of 60. More available: call again with cursor=25.");
        assert_eq!(paginate(&v, Some(25), Some(50)).1, "Showing 51-60 of 60.");
        assert_eq!(paginate(&v, Some(25), Some(70)).1, "Showing 71-70 of 60."); // Python quirk, preserved
        assert_eq!(paginate(&v, Some(25), Some(50)).0, (50..60).collect::<Vec<i32>>());
        assert_eq!(page_bounds(Some(0), Some(-3)), (1, 0));
        assert_eq!(page_bounds(Some(999), None), (200, 0));
        assert_eq!(page_bounds(None, None), (25, 0));
    }

    #[test]
    fn budget_counts_code_points() {
        let s = format!("{}\n{}", "é".repeat(23_990), "z".repeat(100));
        let out = budget(&s, 24_000);
        assert!(out.starts_with(&"é".repeat(23_990)));
        assert!(out.ends_with("éééééé\n\n[truncated: 101 more characters. Narrow the query or use a smaller limit / the next cursor.]"));
    }

    #[test]
    fn budget_without_newline_keeps_budget_chars() {
        let out = budget(&"a".repeat(30_000), 24_000);
        assert_eq!(out.chars().count(), 24_096);
        assert!(out.ends_with("[truncated: 6,000 more characters. Narrow the query or use a smaller limit / the next cursor.]"));
        assert_eq!(budget("short", 24_000), "short");
    }

    #[test]
    fn universal_newlines() {
        let f = tmp_with(b"\xef\xbb\xbfa\r\nb\rc\n");
        assert_eq!(read_universal(f.path()).unwrap(), "\u{feff}a\nb\nc\n");
    }

    #[test]
    fn binary_is_reported() {
        let f = tmp_with(b"\xff\xfe\x00");
        assert_eq!(read_universal(f.path()), Err(ReadError::Binary));
        assert_eq!(read_universal(std::path::Path::new("/nonexistent/zz")), Err(ReadError::NotFound));
        assert_eq!(read_universal(std::env::temp_dir().as_path()), Err(ReadError::IsDir));
    }

    #[test]
    fn thousands_matches_python() {
        assert_eq!(thousands(303080), "303,080");
        assert_eq!(thousands(7), "7");
        assert_eq!(thousands(-1234567), "-1,234,567");
    }

    #[test]
    fn py_str_renders_none() {
        assert_eq!(py_str(None), "None");
        assert_eq!(py_str(Some("x")), "x");
    }
}
