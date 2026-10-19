//! Embedding parity against sentence-transformers itself. IGNORED by default:
//! it needs the real model and a Python with sentence-transformers. Run with
//!     cargo test --release --test embed_parity -- --ignored
//! Python reference vectors are computed on the fly and never stored: vectors
//! of instrument queries are kept out of git like the queries themselves, and
//! instrument items are reported by index only.

use std::process::Command;

const PY: &str = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from scripts.embed import get_embedding
qs = json.loads(sys.stdin.read())
print(json.dumps([get_embedding(q, provider="sentence_transformers", model="all-MiniLM-L6-v2")["vector"] for q in qs]))
"#;

fn python_vectors(queries: &[String]) -> Vec<Vec<f32>> {
    let repo = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");
    let python = std::env::var("ZURVAN_PYTHON").unwrap_or_else(|_| "/opt/anaconda3/bin/python".into());
    let mut child = Command::new(python)
        .args(["-c", PY, repo])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .expect("python");
    use std::io::Write;
    child.stdin.take().unwrap().write_all(serde_json::to_string(queries).unwrap().as_bytes()).unwrap();
    let out = child.wait_with_output().unwrap();
    serde_json::from_slice(&out.stdout).expect("python vectors")
}

fn cosine(a: &[f32], b: &[f32]) -> f64 {
    let dot: f64 = a.iter().zip(b).map(|(x, y)| *x as f64 * *y as f64).sum();
    let na: f64 = a.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    let nb: f64 = b.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    dot / (na * nb)
}

#[test]
#[ignore]
fn embed_parity() {
    let adhoc: Vec<String> = serde_json::from_str(include_str!("../fixtures/adhoc_queries.json")).unwrap();
    let gold_path = std::path::PathBuf::from(std::env::var("HOME").unwrap()).join(".zurvan/instruments/search_gold.jsonl");
    let gold: Vec<String> = std::fs::read_to_string(gold_path)
        .map(|s| s.lines().filter(|l| !l.trim().is_empty()).map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap()["query"].as_str().unwrap().to_string()).collect())
        .unwrap_or_default();
    let all: Vec<String> = gold.iter().chain(adhoc.iter()).cloned().collect();
    let want = python_vectors(&all);
    let dir = zurvan_mcp::embed::locate_model().expect("model");
    let e = zurvan_mcp::embed::Embedder::load(&dir).unwrap();
    let mut worst = (1.0f64, String::new());
    for (i, q) in all.iter().enumerate() {
        let c = cosine(&e.embed(q).unwrap(), &want[i]);
        let label = if i < gold.len() { format!("instrument#{i}") } else { format!("adhoc#{}", i - gold.len()) };
        if c < worst.0 {
            worst = (c, label.clone());
        }
        assert!(c >= 0.9999, "{label}: cosine {c}");
    }
    eprintln!("embed parity: {} queries, min cosine {:.7} ({})", all.len(), worst.0, worst.1);
}
