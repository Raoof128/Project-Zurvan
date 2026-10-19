#!/usr/bin/env python3
import sys
import os
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST_BIN = ROOT / "rust" / "zurvan-mcp" / "target" / "release" / "zurvan-mcp"


def _rust_doctor() -> int:
    """The Rust server checks itself: index, model, vector cache, write
    bridge, both protocol lifecycles — each failing line followed by its fix."""
    import subprocess
    if not Path(RUST_BIN).exists():
        print(f"❌ {RUST_BIN} is not built. Run: cargo build --release --manifest-path "
              f"{ROOT / 'rust' / 'zurvan-mcp' / 'Cargo.toml'}")
        return 1
    p = subprocess.run([str(RUST_BIN), "doctor"], capture_output=True, text=True)
    print(p.stdout, end="")
    return 0 if p.returncode == 0 else 1


def run_checks(engine: str = "python"):
    if engine == "rust":
        return _rust_doctor()
    errors = []
    warnings = []

    # 1. Python version
    if sys.version_info < (3, 10):
        errors.append("Python version must be >= 3.10")

    # 2. MCP package availability
    try:
        import mcp
    except ImportError:
        errors.append("MCP package is not installed. Run: pip install mcp")

    # 3. scripts/mcp_server.py exists
    if not (ROOT / "scripts" / "mcp_server.py").exists():
        errors.append("scripts/mcp_server.py is missing.")

    # 4. data/search.sqlite exists
    if not (ROOT / "data" / "search.sqlite").exists():
        warnings.append("data/search.sqlite is missing. Run: zurvan index rebuild")

    # 5. data/graph.sqlite exists
    if not (ROOT / "data" / "graph.sqlite").exists():
        warnings.append("data/graph.sqlite is missing. Run: zurvan graph rebuild")

    # 6. eval/search_gold.jsonl validates
    from scripts.instruments import gold_path as _gp, InstrumentMissing
    try:
        gold_path = _gp()          # local-only cleartext; see scripts/instruments.py
    except InstrumentMissing:
        gold_path = ROOT / "eval" / "search_gold.jsonl"
    if not gold_path.exists():
        warnings.append("eval/search_gold.jsonl is missing.")
    else:
        try:
            with open(gold_path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    if line.strip():
                        json.loads(line)
        except Exception as e:
            errors.append(f"eval/search_gold.jsonl is invalid JSONL at line {idx+1}: {e}")

    # 7. Security env checks
    readonly = os.environ.get("ZURVAN_MCP_READONLY", "1")
    if readonly != "1":
        warnings.append("ZURVAN_MCP_READONLY is not 1. Write mode is enabled. Ensure this is safe for your environment.")

    transport = os.environ.get("ZURVAN_MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        errors.append(f"ZURVAN_MCP_TRANSPORT is set to {transport}. Only 'stdio' is allowed.")

    allow_raw = os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0")
    if allow_raw != "0":
        warnings.append("ZURVAN_MCP_ALLOW_RAW_READ is not 0. Agents can read raw files.")

    print("🩺 Zurvan MCP Doctor")
    print("====================")
    
    if not errors and not warnings:
        print("✅ System is healthy and ready for MCP integration.")
        return 0

    if warnings:
        print("\n⚠️  Warnings:")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print("\n❌ Errors:")
        for e in errors:
            print(f"  - {e}")
        print("\nPlease fix the errors above before connecting MCP clients.")
        return 1
    
    print("\n✅ System is healthy (with warnings).")
    return 0

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check the Zurvan MCP setup")
    parser.add_argument("--engine", choices=["python", "rust"], default="python")
    sys.exit(run_checks(parser.parse_args().engine))
