import os
import tarfile
import pytest
from unittest.mock import patch
from scripts.restore_snapshot import restore_snapshot, safe_extract

def test_restore_refuses_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("scripts.restore_snapshot.ROOT", tmp_path)
    monkeypatch.setattr("scripts.restore_snapshot.SNAPSHOTS_DIR", tmp_path / "dist" / "snapshots")
    
    (tmp_path / "dist" / "snapshots").mkdir(parents=True)
    snap = tmp_path / "dist" / "snapshots" / "test.tar.gz"
    with tarfile.open(snap, "w:gz") as tar:
        pass
        
    with pytest.raises(SystemExit):
        restore_snapshot("test.tar.gz", force=False)
        
    captured = capsys.readouterr()
    assert "Refusing to restore" in captured.out

def test_restore_creates_backup_and_restores(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.restore_snapshot.ROOT", tmp_path)
    monkeypatch.setattr("scripts.restore_snapshot.SNAPSHOTS_DIR", tmp_path / "dist" / "snapshots")
    monkeypatch.setattr("scripts.restore_snapshot.BACKUP_DIR", tmp_path / "dist" / "backups")
    
    (tmp_path / "dist" / "snapshots").mkdir(parents=True)
    snap = tmp_path / "dist" / "snapshots" / "test.tar.gz"
    
    # Create fake content to restore
    (tmp_path / "fake").mkdir()
    with open(tmp_path / "fake" / "content.md", "w") as f:
        f.write("content")
        
    with tarfile.open(snap, "w:gz") as tar:
        tar.add(tmp_path / "fake" / "content.md", arcname="wiki/content.md")
        
    restore_snapshot("test.tar.gz", force=True)
    
    # check backup created
    backups = list((tmp_path / "dist" / "backups").glob("*.tar.gz"))
    assert len(backups) == 1
    
    # check restored file
    assert (tmp_path / "wiki" / "content.md").exists()

def test_safe_extract_blocks_traversal(tmp_path):
    class FakeMember:
        def __init__(self, name):
            self.name = name
            
    tar = type("FakeTar", (), {"getmembers": lambda self: [FakeMember("../evil.txt")]})()
    
    with pytest.raises(Exception, match="Unsafe path"):
        safe_extract(tar, path=str(tmp_path))

def test_safe_extract_blocks_raw(tmp_path):
    class FakeMember:
        def __init__(self, name):
            self.name = name
            
    tar = type("FakeTar", (), {"getmembers": lambda self: [FakeMember("raw/secret.txt")]})()
    
    with pytest.raises(Exception, match="protected raw/ directory"):
        safe_extract(tar, path=str(tmp_path))
