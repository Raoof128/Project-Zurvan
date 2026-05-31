import sys
from unittest.mock import patch, MagicMock
from scripts.doctor_mcp import run_checks

def test_doctor_mcp_healthy(capsys, monkeypatch):
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    monkeypatch.setenv("ZURVAN_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("ZURVAN_MCP_ALLOW_RAW_READ", "0")
    
    with patch("scripts.doctor_mcp.sys") as mock_sys:
        mock_sys.version_info = (3, 12)
        ret = run_checks()
        assert ret == 0
        out, err = capsys.readouterr()
        assert "✅ System is healthy" in out

def test_doctor_mcp_errors(capsys, monkeypatch):
    monkeypatch.setenv("ZURVAN_MCP_TRANSPORT", "http")
    
    with patch("scripts.doctor_mcp.sys") as mock_sys:
        mock_sys.version_info = (3, 9)
        with patch("scripts.doctor_mcp.Path.exists", return_value=False):
            ret = run_checks()
            assert ret == 1
            out, err = capsys.readouterr()
            assert "Python version must be >= 3.10" in out
            assert "scripts/mcp_server.py is missing" in out
            assert "ZURVAN_MCP_TRANSPORT is set to http" in out
