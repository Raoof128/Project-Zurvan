//! A minimal stdio JSON-RPC client for `doctor` and `self-test`: they drive
//! THIS binary as a child, over the same wire an MCP client uses.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

pub struct Client {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
    next_id: i64,
    protocol: String,
}

fn meta_2026() -> Value {
    json!({
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "zurvan-mcp-selfcheck", "version": env!("CARGO_PKG_VERSION")},
        "io.modelcontextprotocol/clientCapabilities": {}
    })
}

impl Client {
    /// Spawn this executable as `serve` against `root`, then open the given
    /// lifecycle: `initialize` for 2025-11-25, `server/discover` for 2026-07-28.
    pub fn spawn(root: &Path, protocol: &str) -> Result<(Client, Value), String> {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let mut child = Command::new(exe)
            .arg("serve")
            .env("ZURVAN_ROOT", root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| e.to_string())?;
        let stdin = child.stdin.take().ok_or("no stdin")?;
        let stdout = child.stdout.take().ok_or("no stdout")?;
        let (tx, rx) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        let mut c = Client { child, stdin, lines: rx, next_id: 0, protocol: protocol.to_string() };
        let opened = if protocol == "2026-07-28" {
            c.request("server/discover", json!({}))?
        } else {
            let r = c.request(
                "initialize",
                json!({"protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "zurvan-mcp-selfcheck", "version": "1"}}),
            )?;
            c.send(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"}))?;
            r
        };
        Ok((c, opened))
    }

    fn send(&mut self, v: &Value) -> Result<(), String> {
        writeln!(self.stdin, "{v}").and_then(|_| self.stdin.flush()).map_err(|e| e.to_string())
    }

    pub fn request(&mut self, method: &str, mut params: Value) -> Result<Value, String> {
        self.next_id += 1;
        let id = self.next_id;
        if self.protocol == "2026-07-28" {
            params["_meta"] = meta_2026();
        }
        self.send(&json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}))?;
        loop {
            let line = self.lines.recv_timeout(Duration::from_secs(120)).map_err(|_| format!("no response to {method}"))?;
            let v: Value = serde_json::from_str(&line).map_err(|_| "the server wrote a non-JSON line to stdout".to_string())?;
            if v.get("id") == Some(&json!(id)) {
                if let Some(e) = v.get("error") {
                    return Err(format!("{method}: {}", e["message"]));
                }
                return Ok(v["result"].clone());
            }
        }
    }

    /// (text, isError) of one tool call.
    pub fn call(&mut self, tool: &str, args: Value) -> Result<(String, bool), String> {
        let r = self.request("tools/call", json!({"name": tool, "arguments": args}))?;
        Ok((r["content"][0]["text"].as_str().unwrap_or_default().to_string(), r["isError"] == json!(true)))
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
