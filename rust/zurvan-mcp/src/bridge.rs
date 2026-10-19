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
/// `tool_zurvan_claim_add`'s own refusal text for an unsafe source, verbatim.
pub const CLAIM_SOURCE_ERROR: &str =
    "Error: Source path fails safety checks (e.g. no absolute paths, no ../, no raw/ without flag).";
pub const WRITE_TIMEOUT: Duration = Duration::from_secs(120);
pub const EVAL_TIMEOUT: Duration = Duration::from_secs(1800);
const PROGRESS_EVERY: Duration = Duration::from_secs(2);
/// How long a timed-out bridge gets after SIGTERM before SIGKILL: a write
/// killed outright can leave the ledger SUSPECT mid-operation.
const STOP_GRACE: Duration = Duration::from_secs(5);
/// How much of the bridge's stderr an error carries.
const STDERR_TAIL: usize = 2048;

/// SIGTERM, a grace period, then SIGKILL; always reaped.
fn stop(child: &mut std::process::Child) {
    // SAFETY: kill(2) with our own child's pid and a constant signal.
    unsafe {
        libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
    }
    let asked = Instant::now();
    while asked.elapsed() < STOP_GRACE {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = child.kill();
    let _ = child.wait();
}

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
    /// The client's own PYTHONPATH, kept after the code root.
    pub extra_pythonpath: Option<String>,
}

impl BridgeEnv {
    pub fn from_env(cfg: &Config) -> BridgeEnv {
        let python = std::env::var("ZURVAN_PYTHON").ok().filter(|p| !p.is_empty()).map(PathBuf::from).filter(|p| p.is_absolute());
        let code_root = std::env::var("ZURVAN_CODE_ROOT")
            .ok()
            .filter(|p| !p.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("ZURVAN_BUILD_ROOT")));
        BridgeEnv {
            python,
            code_root,
            store_root: cfg.root.clone(),
            readonly: crate::config::readonly(),
            extra_pythonpath: std::env::var("PYTHONPATH").ok().filter(|p| !p.is_empty()),
        }
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
    // Design §5: `claim_add` reads its `source` to verify the evidence, so the
    // source is held to the same allow-list as `zurvan_read_page`. Python
    // checks only `is_safe_path`, which let a claim copy `.git/config` or a
    // `sources/` document into a readable claim page (final review I1).
    if tool == "claim_add" {
        let source = args.get("source").and_then(Value::as_str).unwrap_or("");
        if !crate::security::is_readable_path(&env.store_root, source, crate::config::allow_raw()) {
            return Ok((CLAIM_SOURCE_ERROR.to_string(), None));
        }
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
    // The code root first, then whatever the client already had (final
    // review minor 3: the client's PYTHONPATH was overwritten).
    let code = env.code_root.display().to_string();
    let pythonpath = match env.extra_pythonpath.as_deref() {
        Some(extra) if extra != code => format!("{code}:{extra}"),
        _ => code,
    };
    let mut child = Command::new(python)
        .arg(&script)
        .env("PYTHONPATH", pythonpath)
        .env("ZURVAN_ROOT", &env.store_root)
        .env("ZURVAN_MCP_READONLY", if env.readonly { "1" } else { "0" })
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot start the Python bridge ({}): {e}", python.display()))?;
    let mut stdout = child.stdout.take().expect("piped stdout");
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stdout.read_to_string(&mut s);
        s
    });
    // stderr is drained (a full pipe would block the child) and its tail kept
    // for the error text: "bad reply" with no cause was undiagnosable.
    let mut stderr = child.stderr.take().expect("piped stderr");
    let err_reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stderr.read_to_end(&mut buf);
        let tail = &buf[buf.len().saturating_sub(STDERR_TAIL)..];
        String::from_utf8_lossy(tail).trim().to_string()
    });
    let request = json!({ "tool": tool, "args": args }).to_string();
    // A child that exits before reading gives EPIPE here; it is reported with
    // its exit status below, after the child is reaped, never left a zombie.
    let wrote = child.stdin.take().map(|mut s| s.write_all(request.as_bytes())).transpose();
    let start = Instant::now();
    let mut last_tick = Instant::now();
    let status = loop {
        match child.try_wait().map_err(|e| e.to_string())? {
            Some(status) => break status,
            None if start.elapsed() > timeout => {
                stop(&mut child);
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
    };
    let out = reader.join().unwrap_or_default();
    let err_tail = err_reader.join().unwrap_or_default();
    let why = |what: &str| {
        if err_tail.is_empty() {
            format!("the Python bridge {what} ({status})")
        } else {
            format!("the Python bridge {what} ({status}): {err_tail}")
        }
    };
    if wrote.is_err() {
        return Err(why("exited before reading its request"));
    }
    let line = out.lines().rev().find(|l| !l.trim().is_empty()).ok_or_else(|| why("wrote no reply"))?;
    let reply: Value = serde_json::from_str(line).map_err(|e| why(&format!("wrote a bad reply ({e})")))?;
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
            extra_pythonpath: None,
        };
        (dir, env)
    }

    fn args(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn readonly_gate_never_spawns() {
        let env = BridgeEnv { python: Some("/bin/false".into()), code_root: "/nonexistent".into(), store_root: "/nonexistent".into(), readonly: true, extra_pythonpath: None };
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
        let env = BridgeEnv { python: None, code_root: "/x".into(), store_root: "/x".into(), readonly: false, extra_pythonpath: None };
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

    /// Final review 2026-10-19 (I1): design §5 applies the read allow-list to
    /// `zurvan_claim_add`'s `source`. Python checks only `is_safe_path`, so a
    /// claim quoting `.git/config` or `sources/` succeeded, copied the secret
    /// into a readable claim page, and made success-vs-failure a guess oracle.
    #[test]
    fn claim_add_source_is_allow_listed() {
        let (dir, env) = fake("cat >/dev/null; echo '{\"ok\": true, \"text\": \"Claim added successfully: leak\", \"revision\": 1}'");
        for (p, body) in [(".git/config", "SECRET"), ("sources/legal.md", "SECRET"), ("wiki/a.md", "ok")] {
            let f = dir.path().join(p);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, body).unwrap();
        }
        for refused in [".git/config", "sources/legal.md", "wiki/traces/x.md", "../x", "/etc/passwd"] {
            let a = args(json!({"text": "leak", "source": refused, "evidence": "SECRET"}));
            let (text, rev) = call(&env, "claim_add", &a, Duration::from_secs(10), &|_, _| {}).unwrap();
            assert_eq!(text, CLAIM_SOURCE_ERROR, "{refused}");
            assert_eq!(rev, None, "{refused}");
        }
        let a = args(json!({"text": "leak", "source": "wiki/a.md", "evidence": "ok"}));
        let (text, _) = call(&env, "claim_add", &a, Duration::from_secs(10), &|_, _| {}).unwrap();
        assert_eq!(text, "Claim added successfully: leak", "an allowed source still reaches Python");
    }

    #[test]
    fn claim_add_read_only_error_comes_before_the_source_check() {
        let env = BridgeEnv { python: Some("/bin/false".into()), code_root: "/nonexistent".into(), store_root: "/nonexistent".into(), readonly: true, extra_pythonpath: None };
        let a = args(json!({"source": ".git/config"}));
        let (text, _) = call(&env, "claim_add", &a, Duration::from_secs(5), &|_, _| {}).unwrap();
        assert_eq!(text, crate::security::READONLY_ERROR, "Python's decorator order: read-only first");
    }

    #[test]
    fn error_reply_and_timeout_are_errors() {
        let (_d, env) = fake("cat >/dev/null; echo '{\"ok\": false, \"error\": \"ValueError: nope\"}'");
        assert_eq!(call(&env, "remember", &Map::new(), Duration::from_secs(10), &|_, _| {}).unwrap_err(), "ValueError: nope");
        let (_d2, slow) = fake("sleep 5");
        let e = call(&slow, "remember", &Map::new(), Duration::from_millis(300), &|_, _| {}).unwrap_err();
        assert!(e.contains("timed out"), "{e}");
    }

    /// Final review minor 3: a timed-out write was SIGKILLed, which can leave
    /// the ledger SUSPECT mid-operation. It now gets SIGTERM and a grace period.
    #[test]
    fn timeout_asks_the_child_to_stop_before_killing_it() {
        let (dir, env) = fake("trap 'echo stopped > \"$(dirname \"$0\")/term\"; exit 0' TERM; cat >/dev/null; while :; do sleep 0.05; done");
        let e = call(&env, "remember", &Map::new(), Duration::from_millis(300), &|_, _| {}).unwrap_err();
        assert!(e.contains("timed out"), "{e}");
        assert!(dir.path().join("scripts/term").exists(), "SIGTERM first, so a write can finish its ledger step");
    }

    #[test]
    fn a_failed_bridge_reports_its_stderr_tail() {
        let (_d, env) = fake("cat >/dev/null; echo 'ModuleNotFoundError: No module named yaml' >&2; exit 1");
        let e = call(&env, "remember", &Map::new(), Duration::from_secs(10), &|_, _| {}).unwrap_err();
        assert!(e.contains("No module named yaml"), "{e}");
        assert!(e.contains("exit status: 1") || e.contains("exit code: 1"), "{e}");
    }

    #[test]
    fn a_bridge_that_exits_before_reading_is_an_error_naming_its_status() {
        let (_d, env) = fake("exit 3");
        let big = args(json!({"body": "x".repeat(4 << 20)}));
        let e = call(&env, "remember", &big, Duration::from_secs(10), &|_, _| {}).unwrap_err();
        assert!(e.contains("exit status: 3") || e.contains("exit code: 3"), "{e}");
    }

    #[test]
    fn pythonpath_is_prefixed_not_replaced() {
        let (dir, mut env) = fake("cat >/dev/null; printf '{\"ok\": true, \"text\": \"%s\", \"revision\": null}\\n' \"$PYTHONPATH\"");
        env.extra_pythonpath = Some("/opt/site-extra".into());
        let (text, _) = call(&env, "remember", &Map::new(), Duration::from_secs(10), &|_, _| {}).unwrap();
        assert_eq!(text, format!("{}:/opt/site-extra", dir.path().display()));
    }
}
