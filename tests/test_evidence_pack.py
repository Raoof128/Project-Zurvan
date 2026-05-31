import os
import pytest
from pathlib import Path
from scripts.evidence_pack import build_evidence_pack, list_evidence_packs, inspect_evidence_pack

def test_build_evidence_pack(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    monkeypatch.setattr("scripts.evidence_pack.collect_evidence", lambda *a, **k: [
        {"evidence_id": "ev-1", "project": "p1", "excerpt": "test"}
    ])
    
    res = build_evidence_pack("test topic")
    assert res["item_count"] == 1
    assert "pack_id" in res
    
    packs = list_evidence_packs()
    assert len(packs) == 1
    assert packs[0]["pack_id"] == res["pack_id"]
    
    insp = inspect_evidence_pack(res["pack_id"])
    assert insp is not None
    assert len(insp["items"]) == 1
