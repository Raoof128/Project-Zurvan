import os
from scripts.mcp_tools import tool_zurvan_search, tool_zurvan_context, tool_zurvan_remember

def test_tool_zurvan_search():
    # Write a test file
    os.makedirs("wiki", exist_ok=True)
    with open("wiki/test_mcp_search.md", "w") as f:
        f.write("unique_mcp_search_term")
        
    result = tool_zurvan_search("unique_mcp_search_term", hybrid=False)
    assert "test_mcp_search.md" in result
    
    os.remove("wiki/test_mcp_search.md")

def test_tool_zurvan_remember(monkeypatch):
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    result = tool_zurvan_remember("note", "MCP test", "body", ["test"])
    assert "read-only mode" in result
    
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    result = tool_zurvan_remember("note", "MCP test", "body", ["test"])
    assert "successfully" in result
    
    os.remove("wiki/note-mcp-test.md")
