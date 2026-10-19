//! Agent readiness (design §7A): progress notifications, hot reload after an
//! atomic index promote, `doctor`, `self-test`. Wire-level, throwaway roots.

use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_zurvan-mcp");

/// A search index shaped like the real one; `mock` vectors so no model is needed.
fn index(path: &Path, provider: &str, revision: &str, chunks: &[(&str, &str, &str)]) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let c = Connection::open(path).unwrap();
    c.execute_batch(
        "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, source_path TEXT, heading TEXT, text TEXT, content_hash TEXT, indexed_at TEXT);
         CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id, heading, text, content='chunks', content_rowid='rowid', tokenize='porter unicode61');
         CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, model TEXT, dimension INTEGER, vector BLOB);
         CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    )
    .unwrap();
    let model = if provider == "mock" { "deterministic_hash" } else { "all-MiniLM-L6-v2" };
    for (id, p, text) in chunks {
        c.execute("INSERT INTO chunks VALUES (?,?,NULL,?,'h','2999-01-01T00:00:00')", params![id, p, text]).unwrap();
        let v = if provider == "mock" { zurvan_mcp::embed::mock(text) } else { vec![0.1f32; 384] };
        let blob: Vec<u8> = v.iter().flat_map(|f| f.to_le_bytes()).collect();
        c.execute("INSERT INTO embeddings VALUES (?,?,?,384,?)", params![id, provider, model, blob]).unwrap();
    }
    c.execute_batch("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild');").unwrap();
    for (k, v) in [("built_from_revision", revision), ("built_at", "2026-10-18T08:23:19+00:00"), ("built_from_manifest", revision)] {
        c.execute("INSERT INTO index_meta VALUES (?,?)", params![k, v]).unwrap();
    }
}

fn root(provider: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let r = dir.path();
    for d in ["wiki", "docs", ".git"] {
        std::fs::create_dir_all(r.join(d)).unwrap();
    }
    std::fs::write(r.join("wiki/index.md"), "# Index\n").unwrap();
    std::fs::write(r.join("AGENTS.md"), "# Agents\n").unwrap();
    std::fs::write(r.join(".git/HEAD"), "ref: refs/heads/main\n").unwrap();
    index(&r.join("data/search.sqlite"), provider, "1", &[("c1", "wiki/alpha.md", "alpha one")]);
    dir
}

struct Server {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
    next_id: i64,
}

impl Server {
    fn spawn(root: &Path, extra_env: &[(&str, &str)]) -> Server {
        let mut cmd = Command::new(BIN);
        cmd.env("ZURVAN_ROOT", root).env("ZURVAN_MCP_READONLY", "1").env_remove("ZURVAN_SEARCH_DB");
        for (k, v) in extra_env {
            cmd.env(k, v);
        }
        let mut child = cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, rx) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        let mut s = Server { child, stdin, lines: rx, next_id: 0 };
        s.request("initialize", json!({"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}));
        writeln!(s.stdin, "{}", json!({"jsonrpc": "2.0", "method": "notifications/initialized"})).unwrap();
        s
    }

    /// (response, notifications received before it)
    fn request_with_notes(&mut self, method: &str, params: Value) -> (Value, Vec<Value>) {
        self.next_id += 1;
        let id = self.next_id;
        writeln!(self.stdin, "{}", json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})).unwrap();
        self.stdin.flush().unwrap();
        let mut notes = Vec::new();
        loop {
            let v: Value = serde_json::from_str(&self.lines.recv_timeout(Duration::from_secs(60)).unwrap()).unwrap();
            if v.get("id") == Some(&json!(id)) {
                return (v, notes);
            }
            if v.get("method").is_some() {
                notes.push(v);
            }
        }
    }

    fn request(&mut self, method: &str, params: Value) -> Value {
        self.request_with_notes(method, params).0
    }

    fn text(&mut self, tool: &str, args: Value) -> String {
        let r = self.request("tools/call", json!({"name": tool, "arguments": args}));
        r["result"]["content"][0]["text"].as_str().unwrap_or_default().to_string()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn fake_bridge(dir: &Path, script: &str) -> PathBuf {
    std::fs::create_dir_all(dir.join("scripts")).unwrap();
    std::fs::write(dir.join("scripts/mcp_bridge.py"), script).unwrap();
    dir.to_path_buf()
}

#[test]
fn progress_only_with_token() {
    let r = root("mock");
    let code = tempfile::tempdir().unwrap();
    let code_root = fake_bridge(code.path(), "cat >/dev/null; sleep 2.6; printf '%s\\n' '{\"ok\": true, \"text\": \"done\", \"revision\": null}'");
    let env = [("ZURVAN_PYTHON", "/bin/sh"), ("ZURVAN_CODE_ROOT", code_root.to_str().unwrap())];
    let mut s = Server::spawn(r.path(), &env);
    let (resp, notes) = s.request_with_notes("tools/call", json!({"name": "zurvan_validate_gold", "arguments": {}, "_meta": {"progressToken": "t1"}}));
    assert_eq!(resp["result"]["content"][0]["text"], json!("done"));
    let progress: Vec<&Value> = notes.iter().filter(|n| n["method"] == "notifications/progress").collect();
    assert!(!progress.is_empty(), "a 2.6 s bridge call with a token reports progress");
    assert!(progress.iter().all(|n| n["params"]["progressToken"] == "t1"));

    let (_, quiet) = s.request_with_notes("tools/call", json!({"name": "zurvan_validate_gold", "arguments": {}}));
    assert!(quiet.iter().all(|n| n["method"] != "notifications/progress"), "no token, no progress");
}

#[test]
fn hot_reload_after_promote() {
    let r = root("mock");
    let mut s = Server::spawn(r.path(), &[]);
    let first = s.text("zurvan_search", json!({"query": "alpha one"}));
    assert!(first.starts_with("[index rev 1 "), "{first}");
    assert!(first.contains("wiki/alpha.md"));

    // An atomic promote: build elsewhere, rename over the published path.
    let next = r.path().join("data/search.sqlite.building-1");
    index(&next, "mock", "2", &[("c2", "wiki/beta.md", "beta two")]);
    std::fs::rename(&next, r.path().join("data/search.sqlite")).unwrap();
    let second = s.text("zurvan_search", json!({"query": "beta two"}));
    assert!(second.starts_with("[index rev 2 "), "{second}");
    assert!(second.contains("wiki/beta.md") && !second.contains("wiki/alpha.md"), "{second}");
}

#[test]
fn doctor_names_fix_for_missing_model() {
    let r = root("sentence_transformers");
    let out = Command::new(BIN).arg("doctor").env("ZURVAN_ROOT", r.path()).env("ZURVAN_MODEL_DIR", "/nope/zurvan-model").output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    let lines: Vec<&str> = text.lines().collect();
    let i = lines.iter().position(|l| l.starts_with("FAIL model")).unwrap_or_else(|| panic!("{text}"));
    assert!(lines[i + 1].starts_with("fix: "), "{text}");
    assert_eq!(out.status.code(), Some(1), "{text}");
    assert!(lines.iter().any(|l| l.starts_with("OK search index")), "{text}");
}

#[test]
fn doctor_passes_on_a_healthy_mock_root() {
    let r = root("mock");
    let out = Command::new(BIN).arg("doctor").env("ZURVAN_ROOT", r.path()).env("ZURVAN_MCP_READONLY", "1").output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(!text.lines().any(|l| l.starts_with("FAIL")), "{text}");
    assert!(text.lines().any(|l| l.starts_with("OK protocol")), "{text}");
    assert_eq!(out.status.code(), Some(0), "{text}");
}

#[test]
fn self_test_passes_on_fixture_root() {
    let r = root("mock");
    let out = Command::new(BIN).arg("self-test").env("ZURVAN_ROOT", r.path()).env("ZURVAN_MCP_READONLY", "1").output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(0), "{text}");
    assert!(text.contains("self-test: PASS"), "{text}");
}
