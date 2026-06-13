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
