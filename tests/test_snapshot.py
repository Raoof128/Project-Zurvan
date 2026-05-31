import os
import tarfile
from unittest.mock import patch
from scripts.snapshot import create_snapshot, list_snapshots

def test_snapshot_create(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.snapshot.ROOT", tmp_path)
    monkeypatch.setattr("scripts.snapshot.SNAPSHOTS_DIR", tmp_path / "dist" / "snapshots")
    
    (tmp_path / "wiki").mkdir()
    with open(tmp_path / "wiki" / "index.md", "w") as f: f.write("wiki index")
    
    (tmp_path / "raw").mkdir()
    with open(tmp_path / "raw" / "secret.txt", "w") as f: f.write("secret")
    
    (tmp_path / "dist").mkdir()
    
    with open(tmp_path / "README.md", "w") as f: f.write("readme")
    
    snapshot_path_str = create_snapshot(include_raw=False)
    assert os.path.exists(snapshot_path_str)
    
    with tarfile.open(snapshot_path_str, "r:gz") as tar:
        names = tar.getnames()
        assert any(n.startswith("wiki/") or n == "wiki" for n in names)
        assert any(n == "README.md" for n in names)
        assert not any("raw" in n for n in names)
        assert not any(".git" in n for n in names)
