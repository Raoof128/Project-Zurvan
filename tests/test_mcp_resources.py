import os
from scripts.mcp_resources import resource_file, get_static_resource

def test_resource_file(monkeypatch, tmp_path):
    # Was written into the LIVE store under a FIXED name and `os.remove`d. A run
    # killed between the two leaves the page behind, and the next run then
    # deletes a page it never created — which is how the session guard came to
    # report `wiki pages: 120 -> 119` on 2026-09-19 with nothing attributable.
    # `resource_file` resolves through the module's `PROJECT_ROOT`, so moving
    # that is the whole redirect.
    import scripts.mcp_resources as mcp_resources
    monkeypatch.setattr(mcp_resources, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_resource.md").write_text(
        "resource content", encoding="utf-8")

    assert "resource content" in resource_file("wiki/test_resource.md")

    # Test safe path failure
    assert "failed safety checks" in resource_file("/etc/passwd")
