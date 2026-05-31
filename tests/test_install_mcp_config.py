import json
from unittest.mock import patch
from scripts.install_mcp_config import get_claude_code_config, get_cursor_config, main

def test_get_claude_code_config():
    config = get_claude_code_config("/fake/dir", readonly=True)
    assert config["mcpServers"]["zurvan"]["env"]["ZURVAN_MCP_READONLY"] == "1"
    
    config = get_claude_code_config("/fake/dir", readonly=False)
    assert config["mcpServers"]["zurvan"]["env"]["ZURVAN_MCP_READONLY"] == "0"

def test_get_cursor_config():
    config = get_cursor_config("/fake/dir", readonly=True)
    assert config["mcpServers"]["zurvan"]["env"]["ZURVAN_MCP_TRANSPORT"] == "stdio"

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
