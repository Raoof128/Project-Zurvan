//! `zurvan-mcp self-test` (design §7A A8): drive this binary over stdio the
//! way a client does and check the contract an agent relies on. Never writes:
//! in write mode the blocked-write step is skipped, not exercised.

use crate::client::Client;
use crate::config::{readonly, Config};
use crate::security::{unsafe_path_error, READONLY_ERROR};
use serde_json::json;

pub fn run(cfg: &Config) -> (String, bool) {
    let mut lines = Vec::new();
    let mut ok = true;
    let mut step = |name: &str, result: Result<(), String>| match result {
        Ok(()) => lines.push(format!("PASS {name}")),
        Err(e) => {
            ok = false;
            lines.push(format!("FAIL {name}: {e}"));
        }
    };
    let (mut c, opened) = match Client::spawn(&cfg.root, "2025-11-25") {
        Ok(x) => x,
        Err(e) => return (format!("FAIL start: {e}\nself-test: FAIL"), false),
    };
    step("initialize carries instructions", if opened["instructions"].as_str().is_some_and(|s| !s.is_empty()) { Ok(()) } else { Err("missing".into()) });
    step("tools/list", match c.request("tools/list", json!({})) {
        Ok(r) if r["tools"].as_array().map(|a| a.len()) == Some(14) => Ok(()),
        Ok(r) => Err(format!("{} tools", r["tools"].as_array().map(|a| a.len()).unwrap_or(0))),
        Err(e) => Err(e),
    });
    step("zurvan_status", match c.call("zurvan_status", json!({})) {
        Ok((t, false)) if t.starts_with("Search index:") => Ok(()),
        Ok((t, _)) => Err(t.lines().next().unwrap_or("").to_string()),
        Err(e) => Err(e),
    });
    step("zurvan_search", match c.call("zurvan_search", json!({"query": "zurvan"})) {
        Ok((_, false)) => Ok(()),
        Ok((t, true)) => Err(t),
        Err(e) => Err(e),
    });
    step("zurvan_read_page allowed path", match c.call("zurvan_read_page", json!({"path": "AGENTS.md"})) {
        Ok((t, _)) if t.starts_with("Error: Path") => Err(t),
        Ok(_) => Ok(()),
        Err(e) => Err(e),
    });
    step("zurvan_read_page refuses .git/HEAD", match c.call("zurvan_read_page", json!({"path": ".git/HEAD"})) {
        Ok((t, true)) if t == unsafe_path_error(".git/HEAD") => Ok(()),
        Ok((t, flag)) => Err(format!("isError {flag}: {t}")),
        Err(e) => Err(e),
    });
    if readonly() {
        step("write blocked in read-only mode", match c.call("zurvan_question_add", json!({"question": "self-test?", "reason": "r", "tags": []})) {
            Ok((t, true)) if t == READONLY_ERROR => Ok(()),
            Ok((t, flag)) => Err(format!("isError {flag}: {t}")),
            Err(e) => Err(e),
        });
    } else {
        lines.push("SKIP write blocked in read-only mode (write mode is on; self-test never writes)".into());
    }
    lines.push(format!("self-test: {}", if ok { "PASS" } else { "FAIL" }));
    (lines.join("\n"), ok)
}
