//! Which claim pages search must skip — a port of `scripts/claim_visibility.py`
//! (Z2 spec §7.5a, IOU-Z2-6).
//!
//! A generated claim page (`authority: structured`) whose status is
//! superseded, retracted, orphaned or erased stays in `wiki/claims/` but must
//! not be served. The index forgets it only on its next rebuild, so search
//! re-reads each claim hit's frontmatter from the live store at query time.
//! Legacy pages carry no `authority` and are never hidden.

use std::io::Read;
use std::path::Path;

pub const NONCURRENT: [&str; 4] = ["superseded", "retracted", "orphaned", "erased"];
pub const CLAIMS_PREFIX: &str = "wiki/claims/";

/// One `key: value` frontmatter line, as `claim_visibility._LINE` matches it:
/// `^([A-Za-z_]+):\s*"?([^"\n]*?)"?\s*$`.
fn line_kv(line: &str) -> Option<(&str, &str)> {
    let (key, rest) = line.split_once(':')?;
    if key.is_empty() || !key.chars().all(|c| c.is_ascii_alphabetic() || c == '_') {
        return None;
    }
    let mut v = rest.trim_start().trim_end();
    v = v.strip_prefix('"').unwrap_or(v);
    v = v.strip_suffix('"').unwrap_or(v);
    (!v.contains('"')).then_some((key, v))
}

/// `claim_visibility._front` over text already read: the flat lines of the
/// frontmatter within the first 4 KiB.
fn front_value<'a>(head: &'a str, want: &str) -> Option<&'a str> {
    let body = head.strip_prefix("---")?;
    let body = match body.find("\n---") {
        Some(end) => &body[..end],
        None => body,
    };
    body.lines().filter_map(line_kv).filter(|(k, _)| *k == want).map(|(_, v)| v).next_back()
}

/// `is_hidden_claim_page` for a page whose text (or its head) is in hand.
pub fn text_is_hidden(rel_path: &str, text: &str) -> bool {
    if !rel_path.replace('\\', "/").starts_with(CLAIMS_PREFIX) {
        return false;
    }
    let mut cut = text.len().min(4096);
    while !text.is_char_boundary(cut) {
        cut -= 1;
    }
    let head = &text[..cut];
    front_value(head, "authority") == Some("structured")
        && front_value(head, "status").is_some_and(|s| NONCURRENT.contains(&s))
}

/// `page_is_hidden(root, rel_path)`: reads at most 4 KiB, and only for a page
/// under `wiki/claims/`. An unreadable page is not hidden (Python's `{}`).
pub fn page_is_hidden(root: &Path, rel_path: &str) -> bool {
    let rel = rel_path.replace('\\', "/");
    if !rel.starts_with(CLAIMS_PREFIX) {
        return false;
    }
    let mut buf = Vec::with_capacity(4096);
    let Ok(f) = std::fs::File::open(root.join(&rel)) else { return false };
    if f.take(4096).read_to_end(&mut buf).is_err() {
        return false;
    }
    text_is_hidden(&rel, &String::from_utf8_lossy(&buf))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn page(status: &str, authority: Option<&str>) -> String {
        let auth = authority.map(|a| format!("authority: {a}\n")).unwrap_or_default();
        format!("---\ntype: claim\n{auth}status: {status}\ntags:\n  - \"p\"\n---\n\n# Claim\nfact\n")
    }

    #[test]
    fn the_rule_matches_python() {
        for s in NONCURRENT {
            assert!(text_is_hidden("wiki/claims/x.md", &page(s, Some("structured"))), "{s}");
            assert!(text_is_hidden("wiki/claims/x.md", &page(&format!("\"{s}\""), Some("\"structured\""))), "quoted {s}");
        }
        assert!(!text_is_hidden("wiki/claims/x.md", &page("asserted", Some("structured"))));
        assert!(!text_is_hidden("wiki/claims/x.md", &page("contested", Some("structured"))));
        assert!(!text_is_hidden("wiki/claims/x.md", &page("retracted", None)), "legacy page");
        assert!(!text_is_hidden("wiki/decisions/x.md", &page("retracted", Some("structured"))));
        assert!(!text_is_hidden("wiki/claims/x.md", "no frontmatter\nstatus: retracted\nauthority: structured\n"));
        // A `status:` below the frontmatter is body text, not the page's status.
        let body = "---\nauthority: structured\nstatus: asserted\n---\nstatus: retracted\n";
        assert!(!text_is_hidden("wiki/claims/x.md", body));
    }

    #[test]
    fn page_is_hidden_reads_the_live_file() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("wiki/claims")).unwrap();
        let p = dir.path().join("wiki/claims/clm_a.md");
        std::fs::write(&p, page("asserted", Some("structured"))).unwrap();
        assert!(!page_is_hidden(dir.path(), "wiki/claims/clm_a.md"));
        std::fs::write(&p, page("retracted", Some("structured"))).unwrap();
        assert!(page_is_hidden(dir.path(), "wiki/claims/clm_a.md"));
        assert!(!page_is_hidden(dir.path(), "wiki/claims/missing.md"));
    }
}
