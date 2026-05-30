import sys

def test_mcp_server_import():
    # Just ensure it imports without crashing
    import scripts.mcp_server
    assert scripts.mcp_server.mcp.name == "Zurvan"
