//! Writes and the two eval tools, run as ONE Python process per call through
//! `scripts/mcp_bridge.py` (design §6): the knowledge ledger's operation
//! boundary stays in Python, untouched.

use crate::config::Config;
use crate::security::READONLY_ERROR;
use serde_json::{json, Map, Value};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub const MISSING_PYTHON: &str =
    "Error: write tools need ZURVAN_PYTHON (an absolute interpreter path); run scripts/install_mcp_config.py --engine rust.";
pub const WRITE_TIMEOUT: Duration = Duration::from_secs(120);
pub const EVAL_TIMEOUT: Duration = Duration::from_secs(1800);
const PROGRESS_EVERY: Duration = Duration::from_secs(2);

/// Bridge tool name for an MCP tool, and whether it writes.
pub fn bridge_tool(mcp_name: &str) -> Option<(&'static str, bool)> {
    Some(match mcp_name {
        "zurvan_remember" => ("remember", true),
        "zurvan_decision_add" => ("decision_add", true),
        "zurvan_decision_set_project" => ("decision_set_project", true),
        "zurvan_claim_add" => ("claim_add", true),
        "zurvan_question_add" => ("question_add", true),
        "zurvan_eval_search" => ("eval_search", false),
        "zurvan_validate_gold" => ("validate_gold", false),
        _ => return None,
    })
}

fn is_write(tool: &str) -> bool {
    matches!(tool, "remember" | "decision_add" | "decision_set_project" | "claim_add" | "question_add")
}

/// Where and how the bridge runs. `code_root` holds `scripts/` (the build's
/// repo, or ZURVAN_CODE_ROOT); `store_root` is the store (ZURVAN_ROOT).
#[derive(Debug, Clone)]
pub struct BridgeEnv {
    pub python: Option<PathBuf>,
    pub code_root: PathBuf,
    pub store_root: PathBuf,
    pub readonly: bool,
}

impl BridgeEnv {
    pub fn from_env(cfg: &Config) -> BridgeEnv {
        let python = std::env::var("ZURVAN_PYTHON").ok().filter(|p| !p.is_empty()).map(PathBuf::from).filter(|p| p.is_absolute());
        let code_root = std::env::var("ZURVAN_CODE_ROOT")
            .ok()
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("ZURVAN_BUILD_ROOT")));
        BridgeEnv { python, code_root, store_root: cfg.root.clone(), readonly: crate::config::readonly() }
    }
}

/// Run one bridge call. Ok = the tool's own text (possibly an error text the
/// tool produced) and the ledger revision after a write; Err = the bridge
/// itself failed (cannot start, timed out, bad reply).
pub fn call(
    env: &BridgeEnv,
    tool: &str,
    args: &Map<String, Value>,
    timeout: Duration,
    on_progress: &dyn Fn(u64, u64),
) -> Result<(String, Option<i64>), String> {
    // The read-only gate is checked HERE, before any process exists, with
    // the exact text Python's enforce_read_only returns.
    if is_write(tool) && env.readonly {
        return Ok((READONLY_ERROR.to_string(), None));
    }
    let Some(python) = env.python.as_ref() else {
        return Ok((MISSING_PYTHON.to_string(), None));
    };
    let script = env.code_root.join("scripts").join("mcp_bridge.py");
    // The bridge inherits the SERVER's working directory (no current_dir):
    // `decision_add` without project= stamps the subject from the cwd's
    // registered project, and that stamp gates publication. Running in the
    // code checkout stamped "zurvan" (public) where Python stamps "unknown"
    // (security review 2026-10-19, reproduced by the parity harness).
    let mut child = Command::new(python)
        .arg(&script)
        .env("PYTHONPATH", &env.code_root)
        .env("ZURVAN_ROOT", &env.store_root)
        .env("ZURVAN_MCP_READONLY", if env.readonly { "1" } else { "0" })
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("cannot start the Python bridge ({}): {e}", python.display()))?;
    let request = json!({ "tool": tool, "args": args }).to_string();
    child.stdin.take().map(|mut s| s.write_all(request.as_bytes())).transpose().map_err(|e| e.to_string())?;
    let mut stdout = child.stdout.take().expect("piped stdout");
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stdout.read_to_string(&mut s);
        s
    });
    let start = Instant::now();
    let mut last_tick = Instant::now();
    loop {
        match child.try_wait().map_err(|e| e.to_string())? {
            Some(_) => break,
            None if start.elapsed() > timeout => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("the Python bridge timed out after {} s", timeout.as_secs_f64()));
            }
            None => {
                if last_tick.elapsed() >= PROGRESS_EVERY {
                    on_progress(start.elapsed().as_secs(), timeout.as_secs());
                    last_tick = Instant::now();
                }
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }
    let out = reader.join().unwrap_or_default();
    let line = out.lines().rev().find(|l| !l.trim().is_empty()).ok_or("the Python bridge wrote no reply")?;
    let reply: Value = serde_json::from_str(line).map_err(|e| format!("the Python bridge wrote a bad reply: {e}"))?;
    if reply.get("ok") != Some(&Value::Bool(true)) {
        return Err(reply.get("error").and_then(Value::as_str).unwrap_or("unknown bridge error").to_string());
    }
    Ok((
        reply.get("text").and_then(Value::as_str).unwrap_or_default().to_string(),
        reply.get("revision").and_then(Value::as_i64),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map, Value};
    use std::time::Duration;

    /// A fake bridge: `sh` runs `scripts/mcp_bridge.py` as a shell script.
    fn fake(body: &str) -> (tempfile::TempDir, BridgeEnv) {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("scripts")).unwrap();
        std::fs::write(dir.path().join("scripts/mcp_bridge.py"), body).unwrap();
        let env = BridgeEnv {
            python: Some("/bin/sh".into()),
            code_root: dir.path().to_path_buf(),
            store_root: dir.path().to_path_buf(),
            readonly: false,
        };
        (dir, env)
    }

    fn args(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn readonly_gate_never_spawns() {
        let env = BridgeEnv { python: Some("/bin/false".into()), code_root: "/nonexistent".into(), store_root: "/nonexistent".into(), readonly: true };
        let out = call(&env, "decision_add", &args(json!({"title": "T"})), Duration::from_secs(5), &|_, _| {}).unwrap();
        assert_eq!(out, (crate::security::READONLY_ERROR.to_string(), None));
    }

    #[test]
    fn eval_tools_are_not_gated_by_read_only() {
        // printf '%s', not echo: sh's echo would turn the JSON's \n into a real newline.
        let (_d, mut env) = fake("cat >/dev/null; printf '%s\\n' '{\"ok\": true, \"text\": \"Evaluates the Python reference retriever, not this server.\\nok\", \"revision\": null}'");
        env.readonly = true;
        let (text, rev) = call(&env, "validate_gold", &Map::new(), Duration::from_secs(10), &|_, _| {}).unwrap();
        assert!(text.starts_with("Evaluates the Python reference retriever"));
        assert_eq!(rev, None);
    }

    #[test]
    fn missing_python_message() {
        let env = BridgeEnv { python: None, code_root: "/x".into(), store_root: "/x".into(), readonly: false };
        let (text, _) = call(&env, "remember", &Map::new(), Duration::from_secs(5), &|_, _| {}).unwrap();
        assert_eq!(text, MISSING_PYTHON);
        assert!(text.starts_with("Error"), "flagged isError by the A5 rule");
    }

    #[test]
    fn round_trip_reads_the_one_json_line() {
        let (_d, env) = fake("cat >/dev/null; echo 'noise on stderr' 1>&2; echo '{\"ok\": true, \"text\": \"Decision added successfully (project: zurvan): T\", \"revision\": 9}'");
        let out = call(&env, "decision_add", &args(json!({"title": "T"})), Duration::from_secs(10), &|_, _| {}).unwrap();
        assert_eq!(out, ("Decision added successfully (project: zurvan): T".to_string(), Some(9)));
    }

    #[test]
    fn request_reaches_the_bridge_on_stdin() {
        let (dir, env) = fake("cat > \"$(dirname \"$0\")/request.json\"; echo '{\"ok\": true, \"text\": \"done\", \"revision\": 3}'");
        call(&env, "question_add", &args(json!({"question": "q?"})), Duration::from_secs(10), &|_, _| {}).unwrap();
        let req: Value = serde_json::from_str(&std::fs::read_to_string(dir.path().join("scripts/request.json")).unwrap()).unwrap();
        assert_eq!(req, json!({"tool": "question_add", "args": {"question": "q?"}}));
    }

    /// Security review 2026-10-19: `decision_add` without `project=` stamps
    /// the subject from the WORKING DIRECTORY's registered project, which gates
    /// publication. The bridge must run where the server runs, never in the
    /// code checkout (a registered, public project).
    #[test]
    fn bridge_runs_in_the_servers_working_directory() {
        let (dir, env) = fake("cat >/dev/null; printf '{\"ok\": true, \"text\": \"%s\", \"revision\": null}\\n' \"$(pwd -P)\"");
        let (cwd, _) = call(&env, "decision_add", &Map::new(), Duration::from_secs(10), &|_, _| {}).unwrap();
        let here = std::env::current_dir().unwrap().canonicalize().unwrap();
        assert_eq!(std::path::PathBuf::from(&cwd), here);
        assert_ne!(std::path::PathBuf::from(&cwd), dir.path().canonicalize().unwrap(), "not the code root");
    }

    #[test]
    fn error_reply_and_timeout_are_errors() {
        let (_d, env) = fake("cat >/dev/null; echo '{\"ok\": false, \"error\": \"ValueError: nope\"}'");
        assert_eq!(call(&env, "remember", &Map::new(), Duration::from_secs(10), &|_, _| {}).unwrap_err(), "ValueError: nope");
        let (_d2, slow) = fake("sleep 5");
        let e = call(&slow, "remember", &Map::new(), Duration::from_millis(300), &|_, _| {}).unwrap_err();
        assert!(e.contains("timed out"), "{e}");
    }
}
