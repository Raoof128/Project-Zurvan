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


def _fake_binary(tmp_path, body):
    exe = tmp_path / "zurvan-mcp"
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(0o755)
    return exe


def test_doctor_mcp_delegates_to_the_rust_doctor(capsys, monkeypatch, tmp_path):
    import scripts.doctor_mcp as dm
    monkeypatch.setattr(dm, "RUST_BIN", _fake_binary(tmp_path, 'echo "args: $*"; echo "doctor: PASS"; exit 0'))
    assert dm.run_checks(engine="rust") == 0
    out = capsys.readouterr().out
    assert "args: doctor" in out and "doctor: PASS" in out


def test_doctor_mcp_rust_failure_and_missing_binary(capsys, monkeypatch, tmp_path):
    import scripts.doctor_mcp as dm
    monkeypatch.setattr(dm, "RUST_BIN", _fake_binary(tmp_path, 'echo "FAIL model: x"; exit 1'))
    assert dm.run_checks(engine="rust") == 1
    monkeypatch.setattr(dm, "RUST_BIN", tmp_path / "not-built")
    assert dm.run_checks(engine="rust") == 1
    assert "cargo build --release" in capsys.readouterr().out
