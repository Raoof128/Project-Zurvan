//! `zurvan-mcp [--root DIR] [serve]` — stdio MCP server. Logs go to stderr;
//! stdout carries JSON-RPC only.
use rmcp::ServiceExt;
use std::path::PathBuf;
use zurvan_mcp::config::Config;
use zurvan_mcp::server::ZurvanServer;

fn parse_args() -> (Option<PathBuf>, String) {
    let mut root = None;
    let mut cmd = "serve".to_string();
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--root" => root = it.next().map(PathBuf::from),
            other => cmd = other.to_string(),
        }
    }
    (root, cmd)
}

#[tokio::main(flavor = "multi_thread")]
async fn main() -> anyhow::Result<()> {
    let (root, cmd) = parse_args();
    let cfg = Config::resolve(root);
    match cmd.as_str() {
        "serve" => {
            let server = ZurvanServer::new(cfg);
            server.warm();
            let running = server.serve(rmcp::transport::stdio()).await?;
            running.waiting().await?;
            Ok(())
        }
        other => {
            eprintln!("zurvan-mcp: unknown command {other:?} (expected: serve)");
            std::process::exit(2);
        }
    }
}
