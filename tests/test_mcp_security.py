import os
from scripts.mcp_security import is_safe_path, enforce_read_only

def test_is_safe_path():
    # Should be true for safe relative paths
    assert is_safe_path("wiki/index.md", allow_raw=False) is True
    
    # Should block absolute paths
    assert is_safe_path("/etc/passwd", allow_raw=False) is False
    
    # Should block traversal
    assert is_safe_path("../outside.md", allow_raw=False) is False
    
    # Should block raw/ by default
    assert is_safe_path("raw/notes/example.md", allow_raw=False) is False
    
    # Should allow raw/ if allow_raw=True
    assert is_safe_path("raw/notes/example.md", allow_raw=True) is True

def test_enforce_read_only(monkeypatch):
    @enforce_read_only
    def dummy_write():
        return "success"
        
    # Default is read-only
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    assert "Error" in dummy_write()
    
    # Write allowed
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    assert dummy_write() == "success"
