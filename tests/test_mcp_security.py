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


def test_raw_block_is_case_insensitive():
    # On a case-insensitive filesystem "Raw/..." resolves to the real raw/ dir,
    # so the block must ignore case or an agent could read untrusted raw content.
    assert is_safe_path("Raw/secret.md", allow_raw=False) is False
    assert is_safe_path("RAW/secret.md", allow_raw=False) is False
    assert is_safe_path("Raw/secret.md", allow_raw=True) is True

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


def test_enforce_read_only_fails_closed(monkeypatch):
    # Only the exact string "0" may enable writes; any other value — including a
    # well-meaning "true"/"yes"/"" — must stay read-only (fail closed), so a
    # misconfigured variable can never silently open write access.
    @enforce_read_only
    def dummy_write():
        return "success"

    for value in ("true", "yes", "", "2", "1 ", "off", "false"):
        monkeypatch.setenv("ZURVAN_MCP_READONLY", value)
        assert "Error" in dummy_write(), f"{value!r} should stay read-only"

    # Unset behaves as read-only too (default "1").
    monkeypatch.delenv("ZURVAN_MCP_READONLY", raising=False)
    assert "Error" in dummy_write()

    # Trailing whitespace around the enabling value is tolerated.
    monkeypatch.setenv("ZURVAN_MCP_READONLY", " 0 ")
    assert dummy_write() == "success"
