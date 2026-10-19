fn main() {
    let cfg = zurvan_mcp::config::Config::resolve(None);
    eprintln!("zurvan-mcp: no server yet (Task 3); root = {}", cfg.root.display());
    std::process::exit(2);
}
