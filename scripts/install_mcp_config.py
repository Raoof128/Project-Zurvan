#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _server_args(cwd: str) -> list:
    """Absolute path to the server script.

    A relative "scripts/mcp_server.py" only resolves when the client happens to
    launch with the repo as its working directory, which no client guarantees.
    """
    return [str(Path(cwd) / "scripts" / "mcp_server.py")]


def _interpreter() -> str:
    """Absolute interpreter path, never a bare "python".

    PATH resolution is not a safe way to pick the interpreter here: on this
    machine `python3` is a Homebrew build with neither `mcp` nor PyYAML
    installed, so a bare name can select an interpreter that cannot run the
    server at all. sys.executable is the one we know imported this script.
    """
    return sys.executable


def _env(cwd: str, readonly: bool) -> dict:
    return {
        "PYTHONPATH": cwd,
        "ZURVAN_MCP_READONLY": "1" if readonly else "0",
        "ZURVAN_MCP_TRANSPORT": "stdio",
        "ZURVAN_MCP_ALLOW_RAW_READ": "0",
        # NOT "mock". Query embeddings follow the index, so mock would not break
        # search on its own — but any rebuild run under this environment writes
        # deterministic-hash vectors over the real ones and silently destroys
        # semantic retrieval (measured at 0% recall when that last happened).
        # The generated config should never be the thing that poisons the index.
        "ZURVAN_EMBED_PROVIDER": "sentence_transformers",
    }

def rust_binary(cwd: str) -> str:
    """The Rust server (rust/zurvan-mcp), built with `cargo build --release`."""
    return str(Path(cwd) / "rust" / "zurvan-mcp" / "target" / "release" / "zurvan-mcp")


def _launch(cwd: str, readonly: bool, engine: str) -> dict:
    """command / args / env for one engine. `python` is the reference server
    (scripts/mcp_server.py); `rust` is the drop-in binary, whose write and eval
    tools run through scripts/mcp_bridge.py with ZURVAN_PYTHON — absolute, for
    the same reason the python engine's interpreter is (see _interpreter)."""
    env = _env(cwd, readonly)
    if engine == "rust":
        return {"command": rust_binary(cwd), "args": ["serve"], "env": {**env, "ZURVAN_PYTHON": _interpreter()}}
    if engine != "python":
        raise ValueError(f"unknown engine {engine!r}")
    return {"command": _interpreter(), "args": _server_args(cwd), "env": env}


def get_claude_code_config(cwd: str, readonly: bool, engine: str = "python"):
    return {"mcpServers": {"zurvan": _launch(cwd, readonly, engine)}}

def get_cursor_config(cwd: str, readonly: bool, engine: str = "python"):
    return {"mcpServers": {"zurvan": _launch(cwd, readonly, engine)}}

def get_codex_config(cwd: str, readonly: bool, engine: str = "python"):
    """Codex stores MCP servers in ~/.codex/config.toml under [mcp_servers.<name>].
    Returns a plain dict describing the server; render_codex_toml() turns it into
    the TOML block and CLI command."""
    # Absolute command path so the launch does not depend on Codex's PATH.
    return {"name": "zurvan", **_launch(cwd, readonly, engine)}

def render_codex_toml(config: dict) -> str:
    name = config["name"]
    args_toml = ", ".join(f'"{a}"' for a in config["args"])
    env_toml = ", ".join(f'{k} = "{v}"' for k, v in config["env"].items())
    return (
        f"[mcp_servers.{name}]\n"
        f'command = "{config["command"]}"\n'
        f"args = [{args_toml}]\n"
        f"env = {{ {env_toml} }}\n"
    )

def render_codex_cli(config: dict) -> str:
    env_flags = " ".join(f"--env {k}={v}" for k, v in config["env"].items())
    return (
        f"codex mcp add {config['name']} {env_flags} "
        f"-- {config['command']} {' '.join(config['args'])}"
    )

def main():
    parser = argparse.ArgumentParser(description="Generate MCP client configurations for Zurvan")
    parser.add_argument("--client", choices=["claude-code", "cursor", "codex"], required=True, help="Target client")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--readonly", action="store_true", help="Generate safe read-only config")
    group.add_argument("--write-mode", action="store_true", help="Enable write mode (WARNING: can modify your wiki!)")
    
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing config files (mocked)")
    parser.add_argument("--engine", choices=["python", "rust"], default="python",
                        help="python = scripts/mcp_server.py (reference); rust = rust/zurvan-mcp (drop-in, faster)")

    args = parser.parse_args()

    if args.write_mode:
        print("\n⚠️  WARNING: Write mode is enabled. The agent will have permission to modify your Zurvan knowledge base.", file=sys.stderr)
        print("   Proceed with caution.\n", file=sys.stderr)

    cwd = str(ROOT)
    if args.engine == "rust" and not Path(rust_binary(cwd)).exists():
        print(f"⚠️  {rust_binary(cwd)} is not built yet. Build it first:\n"
              f"   cargo build --release --manifest-path {ROOT / 'rust' / 'zurvan-mcp' / 'Cargo.toml'}\n", file=sys.stderr)

    if args.client == "claude-code":
        config = get_claude_code_config(cwd, args.readonly, args.engine)
        print("Add the following to your claude.json (or equivalent config):")
        print("\n" + json.dumps(config, indent=2))
    elif args.client == "cursor":
        config = get_cursor_config(cwd, args.readonly, args.engine)
        print("Add the following to your Cursor MCP server settings:")
        print("\n" + json.dumps(config, indent=2))
    elif args.client == "codex":
        config = get_codex_config(cwd, args.readonly, args.engine)
        print("Option A — run this command:\n")
        print(render_codex_cli(config))
        print("\nOption B — add this block to ~/.codex/config.toml:\n")
        print(render_codex_toml(config))

if __name__ == "__main__":
    main()
