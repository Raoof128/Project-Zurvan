//! Wire-level tests: spawn the real binary and speak raw JSON-RPC over stdio,
//! exactly what an MCP client sees. Expected shapes were captured from the
//! Python server on 2026-10-19 (fixtures/tools_list.json, fixtures/listings.json,
//! and the argument-error / unknown-resource shapes asserted below).

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const INSTRUCTIONS: &str = include_str!("../instructions.md");

fn fixture(name: &str) -> Value {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures").join(name);
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn meta_2026() -> Value {
    json!({
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "protocol-test", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {}
    })
}

struct Server {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
    next_id: i64,
    _root: tempfile::TempDir,
}

impl Server {
    fn spawn() -> Server {
        let root = tempfile::tempdir().unwrap();
        let r = root.path();
        for d in ["wiki/a", "wiki/traces", "docs", "eval", ".git"] {
            std::fs::create_dir_all(r.join(d)).unwrap();
        }
        std::fs::write(r.join("wiki/a/b.md"), "nested page\r\nsecond line\n").unwrap();
        std::fs::write(r.join("wiki/index.md"), "# Index\n").unwrap();
        std::fs::write(r.join(".git/HEAD"), "ref: refs/heads/main\n").unwrap();
        let mut child = Command::new(env!("CARGO_BIN_EXE_zurvan-mcp"))
            .env("ZURVAN_ROOT", r)
            .env("ZURVAN_MCP_READONLY", "1")
            .env_remove("ZURVAN_SEARCH_DB")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn zurvan-mcp");
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, rx) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Server { child, stdin, lines: rx, next_id: 0, _root: root }
    }

    fn send(&mut self, msg: &Value) {
        writeln!(self.stdin, "{msg}").unwrap();
        self.stdin.flush().unwrap();
    }

    /// Send a request and return its whole response object (result or error).
    fn request(&mut self, method: &str, params: Value) -> Value {
        self.next_id += 1;
        let id = self.next_id;
        self.send(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}));
        loop {
            let line = self.lines.recv_timeout(Duration::from_secs(20)).expect("response within 20 s");
            let v: Value = serde_json::from_str(&line).unwrap_or_else(|e| panic!("stdout carried non-JSON {line:?}: {e}"));
            if v.get("id") == Some(&json!(id)) {
                return v;
            }
        }
    }

    fn init_legacy(&mut self) -> Value {
        let r = self.request("initialize", json!({
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "protocol-test", "version": "0"}
        }));
        self.send(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"}));
        r
    }

    fn call(&mut self, name: &str, args: Value) -> Value {
        self.request("tools/call", json!({"name": name, "arguments": args}))["result"].clone()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[test]
fn tools_list_matches_fixture() {
    let mut s = Server::spawn();
    s.init_legacy();
    let got = s.request("tools/list", json!({}))["result"]["tools"].clone();
    assert_eq!(got, fixture("tools_list.json")["tools"]);
}

#[test]
fn list_results_carry_cache_hints() {
    let mut s = Server::spawn();
    for method in ["tools/list", "prompts/list", "resources/list", "resources/templates/list"] {
        let r = s.request(method, json!({"_meta": meta_2026()}));
        assert_eq!(r["result"]["ttlMs"], json!(300_000), "{method}: {r}");
        assert_eq!(r["result"]["cacheScope"], json!("private"), "{method}");
    }
}

#[test]
fn instructions_on_initialize() {
    let mut s = Server::spawn();
    let init = s.init_legacy();
    assert_eq!(init["result"]["instructions"], json!(INSTRUCTIONS));
    assert_eq!(init["result"]["serverInfo"]["name"], json!("Zurvan"));
    assert_eq!(init["result"]["serverInfo"]["title"], json!("Zurvan knowledge engine"));
    let mut t = Server::spawn();
    let disc = t.request("server/discover", json!({"_meta": meta_2026()}));
    assert_eq!(disc["result"]["instructions"], json!(INSTRUCTIONS), "{disc}");
}

#[test]
fn both_lifecycles() {
    let mut legacy = Server::spawn();
    assert_eq!(legacy.init_legacy()["result"]["protocolVersion"], json!("2025-11-25"));
    let expected = fixture("tools_list.json")["tools"].as_array().unwrap().len();
    assert_eq!(legacy.request("tools/list", json!({}))["result"]["tools"].as_array().unwrap().len(), expected);

    let mut modern = Server::spawn();
    let disc = modern.request("server/discover", json!({"_meta": meta_2026()}));
    let versions = disc["result"]["supportedVersions"].as_array().unwrap();
    assert!(versions.contains(&json!("2026-07-28")) && versions.contains(&json!("2025-11-25")), "{disc}");
    let tools = modern.request("tools/list", json!({"_meta": meta_2026()}));
    assert_eq!(tools["result"]["tools"].as_array().unwrap().len(), expected);
}

#[test]
fn resources_and_template_listed() {
    let mut s = Server::spawn();
    s.init_legacy();
    let listings = fixture("listings.json");
    assert_eq!(s.request("resources/list", json!({}))["result"]["resources"], listings["resources"]["resources"]);
    assert_eq!(
        s.request("resources/templates/list", json!({}))["result"]["resourceTemplates"],
        listings["resourceTemplates"]["resourceTemplates"]
    );
    assert_eq!(s.request("prompts/list", json!({}))["result"]["prompts"], listings["prompts"]["prompts"]);
    let read = s.request("resources/read", json!({"uri": "zurvan://file/wiki/a/b.md"}));
    assert_eq!(read["result"]["contents"][0]["text"], json!("nested page\nsecond line\n"), "{read}");
    let unknown = s.request("resources/read", json!({"uri": "zurvan://nope"}));
    assert_eq!(unknown["error"]["message"], json!("Unknown resource: zurvan://nope"), "{unknown}");
}

#[test]
fn prompts_match_python_text() {
    let mut s = Server::spawn();
    s.init_legacy();
    let p = s.request("prompts/get", json!({"name": "zurvan_project_brief"}))["result"].clone();
    assert_eq!(p["description"], json!("Orientation brief: how to interact with the Zurvan knowledge engine."));
    assert_eq!(p["messages"][0]["role"], json!("user"));
    assert_eq!(
        p["messages"][0]["content"]["text"],
        json!("You are an agent interacting with the Zurvan Local-first AI knowledge engine. Zurvan holds project knowledge securely without internal LLMs. Read AGENTS.md for constraints and use zurvan_search to orient yourself.")
    );
}

#[test]
fn read_page_errors_are_flagged() {
    let mut s = Server::spawn();
    s.init_legacy();
    let r = s.call("zurvan_read_page", json!({"path": ".git/HEAD"}));
    let text = "Error: Path .git/HEAD failed safety checks (no absolute paths, no '../', raw/ blocked).";
    assert_eq!(r["content"][0]["text"], json!(text), "{r}");
    assert_eq!(r["isError"], json!(true));
    assert_eq!(r["structuredContent"], json!({"result": text}));
    let ok = s.call("zurvan_read_page", json!({"path": "wiki/index.md"}));
    assert_eq!(ok["content"][0]["text"], json!("# Index\n"));
    assert_ne!(ok["isError"], json!(true));
    let missing = s.call("zurvan_read_page", json!({"path": "wiki/missing.md"}));
    assert_eq!(missing["content"][0]["text"], json!("Error: File wiki/missing.md not found. Locate it with zurvan_search first."));
}

#[test]
fn read_page_slices_by_code_point() {
    let mut s = Server::spawn();
    s.init_legacy();
    // Expected values are Python's read_file_slice output on the same bytes.
    let r = s.call("zurvan_read_page", json!({"path": "wiki/a/b.md", "offset": 5, "limit": 4}));
    assert_eq!(r["content"][0]["text"], json!("[wiki/a/b.md — characters 5-9] More: call again with offset=9.\nd pa"));
    let crlf = s.call("zurvan_read_page", json!({"path": "wiki/a/b.md", "offset": 10, "limit": 4}));
    assert_eq!(crlf["content"][0]["text"], json!("[wiki/a/b.md — characters 10-14] More: call again with offset=14.\ne\nse"));
    let end = s.call("zurvan_read_page", json!({"path": "wiki/a/b.md", "offset": 999}));
    assert_eq!(end["content"][0]["text"], json!("[end of wiki/a/b.md — offset 999 is past the end]"));
}

#[test]
fn orient_sections_in_order() {
    // No index in this fixture root: search finds nothing, so orient is the
    // freshness line and "No matches found." — as Python's tool_zurvan_orient.
    let mut s = Server::spawn();
    s.init_legacy();
    let r = s.call("zurvan_orient", json!({"topic": "anything"}));
    let text = r["content"][0]["text"].as_str().unwrap();
    let lines: Vec<&str> = text.lines().filter(|l| !l.starts_with("[index ")).collect();
    assert_eq!(lines.join("\n"), "Search index: missing — run `zurvan index search`\n\nNo matches found.", "{text}");
    assert_ne!(r["isError"], json!(true));
}

#[test]
fn range_violation_shape() {
    // Python SDK v2 answers an out-of-range argument with a TOOL RESULT,
    // isError true, text "Error executing tool <name>: 1 validation error for
    // <name>Arguments\n<field>\n  ..." (captured 2026-10-19).
    let mut s = Server::spawn();
    s.init_legacy();
    let r = s.call("zurvan_search", json!({"query": "x", "limit": 0}));
    assert_eq!(r["isError"], json!(true), "{r}");
    let t = r["content"][0]["text"].as_str().unwrap();
    assert!(t.starts_with("Error executing tool zurvan_search: 1 validation error for zurvan_searchArguments\nlimit\n"), "{t}");
    let missing = s.call("zurvan_search", json!({"limit": 3}));
    assert!(missing["content"][0]["text"].as_str().unwrap().contains("\nquery\n  Field required"), "{missing}");
    let bad_enum = s.call("zurvan_remember", json!({"type": "bogus", "title": "t", "body": "b", "tags": []}));
    assert!(bad_enum["content"][0]["text"].as_str().unwrap().starts_with("Error executing tool zurvan_remember: 1 validation error"));
}
