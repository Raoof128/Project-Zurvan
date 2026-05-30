import os
from scripts.mcp_resources import resource_file, get_static_resource

def test_resource_file(monkeypatch):
    os.makedirs("wiki", exist_ok=True)
    with open("wiki/test_resource.md", "w") as f:
        f.write("resource content")
        
    assert "resource content" in resource_file("wiki/test_resource.md")
    
    # Test safe path failure
    assert "failed safety checks" in resource_file("/etc/passwd")
    
    os.remove("wiki/test_resource.md")
