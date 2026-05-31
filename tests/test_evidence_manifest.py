from scripts.evidence_manifest import create_manifest

def test_create_manifest():
    items = [
        {"content_hash": "abc"},
        {"excerpt": "some text"}
    ]
    manifest = create_manifest("pack-123", "test topic", ["proj1"], {"hybrid": True}, items, ["items.json"])
    
    assert manifest["pack_id"] == "pack-123"
    assert manifest["topic"] == "test topic"
    assert manifest["projects_scanned"] == ["proj1"]
    assert manifest["item_count"] == 2
    assert len(manifest["content_hashes"]) == 2
    assert "items.json" in manifest["files_created"]
