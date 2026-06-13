#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def get_claude_code_config(cwd: str, readonly: bool):
    return {
        "mcpServers": {
            "zurvan": {
                "command": "python",
                "args": ["scripts/mcp_server.py"],
                "env": {
                    "PYTHONPATH": cwd,
                    "ZURVAN_MCP_READONLY": "1" if readonly else "0",
                    "ZURVAN_MCP_TRANSPORT": "stdio",
                    "ZURVAN_MCP_ALLOW_RAW_READ": "0",
                    "ZURVAN_EMBED_PROVIDER": "mock"
                }
            }
        }
    }

def get_cursor_config(cwd: str, readonly: bool):
    return {
        "mcpServers": {
            "zurvan": {
                "command": "python",
                "args": ["scripts/mcp_server.py"],
                "env": {
                    "PYTHONPATH": cwd,
                    "ZURVAN_MCP_READONLY": "1" if readonly else "0",
                    "ZURVAN_MCP_TRANSPORT": "stdio",
                    "ZURVAN_MCP_ALLOW_RAW_READ": "0",
                    "ZURVAN_EMBED_PROVIDER": "mock"
                }
            }
        }
    }

def get_codex_config(cwd: str, readonly: bool):
    """Codex stores MCP servers in ~/.codex/config.toml under [mcp_servers.<name>].
    Returns a plain dict describing the server; render_codex_toml() turns it into
    the TOML block and CLI command."""
    return {
        "name": "zurvan",
        # Absolute interpreter path so the launch does not depend on Codex's PATH
        # resolving to an interpreter that has the `mcp` package installed.
        "command": sys.executable,
        "args": [str(Path(cwd) / "scripts" / "mcp_server.py")],
        "env": {
            "PYTHONPATH": cwd,
            "ZURVAN_MCP_READONLY": "1" if readonly else "0",
            "ZURVAN_MCP_TRANSPORT": "stdio",
            "ZURVAN_MCP_ALLOW_RAW_READ": "0",
            "ZURVAN_EMBED_PROVIDER": "mock",
        },
    }

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
        f"-- {config['command']} {config['args'][0]}"
    )

def main():
    parser = argparse.ArgumentParser(description="Generate MCP client configurations for Zurvan")
    parser.add_argument("--client", choices=["claude-code", "cursor", "codex"], required=True, help="Target client")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--readonly", action="store_true", help="Generate safe read-only config")
    group.add_argument("--write-mode", action="store_true", help="Enable write mode (WARNING: can modify your wiki!)")
    
    parser.add_argument("--force", action="store_true", help="Force overwrite of existing config files (mocked)")
    
    args = parser.parse_args()
    
    if args.write_mode:
        print("\n⚠️  WARNING: Write mode is enabled. The agent will have permission to modify your Zurvan knowledge base.", file=sys.stderr)
        print("   Proceed with caution.\n", file=sys.stderr)
        
    cwd = str(ROOT)
    
    if args.client == "claude-code":
        config = get_claude_code_config(cwd, args.readonly)
        print("Add the following to your claude.json (or equivalent config):")
        print("\n" + json.dumps(config, indent=2))
    elif args.client == "cursor":
        config = get_cursor_config(cwd, args.readonly)
        print("Add the following to your Cursor MCP server settings:")
        print("\n" + json.dumps(config, indent=2))
    elif args.client == "codex":
        config = get_codex_config(cwd, args.readonly)
        print("Option A — run this command:\n")
        print(render_codex_cli(config))
        print("\nOption B — add this block to ~/.codex/config.toml:\n")
        print(render_codex_toml(config))

if __name__ == "__main__":
    main()
