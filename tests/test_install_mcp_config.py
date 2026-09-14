import json
from unittest.mock import patch
from scripts.install_mcp_config import (
    get_claude_code_config,
    get_cursor_config,
    get_codex_config,
    render_codex_toml,
    render_codex_cli,
    main,
)

def test_get_claude_code_config():
    config = get_claude_code_config("/fake/dir", readonly=True)
    assert config["mcpServers"]["zurvan"]["env"]["ZURVAN_MCP_READONLY"] == "1"
    
    config = get_claude_code_config("/fake/dir", readonly=False)
    assert config["mcpServers"]["zurvan"]["env"]["ZURVAN_MCP_READONLY"] == "0"

def test_get_cursor_config():
    config = get_cursor_config("/fake/dir", readonly=True)
    assert config["mcpServers"]["zurvan"]["env"]["ZURVAN_MCP_TRANSPORT"] == "stdio"

def test_get_codex_config():
    config = get_codex_config("/fake/dir", readonly=True)
    assert config["name"] == "zurvan"
    assert config["env"]["ZURVAN_MCP_READONLY"] == "1"
    assert config["args"][0].endswith("scripts/mcp_server.py")

def test_render_codex_toml_and_cli():
    config = get_codex_config("/fake/dir", readonly=False)
    toml = render_codex_toml(config)
    assert "[mcp_servers.zurvan]" in toml
    assert 'ZURVAN_MCP_READONLY = "0"' in toml
    cli = render_codex_cli(config)
    assert cli.startswith("codex mcp add zurvan")
    assert "--env ZURVAN_MCP_READONLY=0" in cli

def test_main_codex(capsys):
    with patch("sys.argv", ["install_mcp_config.py", "--client", "codex", "--readonly"]):
        main()
        out, err = capsys.readouterr()
        assert "codex mcp add zurvan" in out
        assert "[mcp_servers.zurvan]" in out

def test_main_readonly(capsys):
    with patch("sys.argv", ["install_mcp_config.py", "--client", "claude-code", "--readonly"]):
        main()
        out, err = capsys.readouterr()
        assert "ZURVAN_MCP_READONLY\": \"1\"" in out

def test_main_write_mode(capsys):
    with patch("sys.argv", ["install_mcp_config.py", "--client", "claude-code", "--write-mode"]):
        main()
        out, err = capsys.readouterr()
        assert "WARNING: Write mode is enabled" in err
        assert "ZURVAN_MCP_READONLY\": \"0\"" in out


# ── generated configs must actually launch ────────────────────────────────────

import os as _os
import sys as _sys

import pytest as _pytest

from scripts.install_mcp_config import (
    get_claude_code_config,
    get_codex_config,
    get_cursor_config,
)

_CWD = "/repo/Zurvan"


def _server_entries():
    claude = get_claude_code_config(_CWD, readonly=True)["mcpServers"]["zurvan"]
    cursor = get_cursor_config(_CWD, readonly=True)["mcpServers"]["zurvan"]
    codex = get_codex_config(_CWD, readonly=True)
    return {"claude-code": claude, "cursor": cursor, "codex": codex}


@_pytest.mark.parametrize("client", ["claude-code", "cursor", "codex"])
def test_generated_config_uses_absolute_paths(client):
    # A relative "scripts/mcp_server.py" only resolves if the client launches
    # with the repo as its CWD, which none of them guarantee; and a bare
    # "python" is chosen by PATH — on this machine `python3` is a Homebrew
    # build with neither `mcp` nor PyYAML, so a bare name can select an
    # interpreter that cannot run the server at all.
    entry = _server_entries()[client]

    assert _os.path.isabs(entry["command"]), f"{client}: interpreter must be absolute"
    assert entry["command"] not in ("python", "python3")
    assert entry["args"], f"{client}: no server path"
    assert _os.path.isabs(entry["args"][0]), f"{client}: server path must be absolute"
    assert entry["args"][0].endswith("mcp_server.py")


@_pytest.mark.parametrize("client", ["claude-code", "cursor", "codex"])
def test_generated_config_never_selects_mock_embeddings(client):
    # A rebuild run under ZURVAN_EMBED_PROVIDER=mock writes deterministic-hash
    # vectors over the real ones and silently destroys semantic search — that
    # has happened before and measured 0% recall. The config Zurvan hands out
    # must never be the thing that does it.
    env = _server_entries()[client]["env"]
    assert env["ZURVAN_EMBED_PROVIDER"] != "mock"


@_pytest.mark.parametrize("client", ["claude-code", "cursor", "codex"])
def test_readonly_flag_reaches_the_env(client):
    assert _server_entries()[client]["env"]["ZURVAN_MCP_READONLY"] == "1"

    write = {
        "claude-code": lambda: get_claude_code_config(_CWD, readonly=False)["mcpServers"]["zurvan"],
        "cursor": lambda: get_cursor_config(_CWD, readonly=False)["mcpServers"]["zurvan"],
        "codex": lambda: get_codex_config(_CWD, readonly=False),
    }[client]()
    assert write["env"]["ZURVAN_MCP_READONLY"] == "0"
