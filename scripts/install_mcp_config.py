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

def main():
    parser = argparse.ArgumentParser(description="Generate MCP client configurations for Zurvan")
    parser.add_argument("--client", choices=["claude-code", "cursor"], required=True, help="Target client")
    
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
    elif args.client == "cursor":
        config = get_cursor_config(cwd, args.readonly)
        print("Add the following to your Cursor MCP server settings:")
        
    print("\n" + json.dumps(config, indent=2))

if __name__ == "__main__":
    main()
