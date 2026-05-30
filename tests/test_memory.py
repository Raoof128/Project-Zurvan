import pytest
import os
from scripts.safe_write import is_safe_path, escape_yaml_string, write_file_safely
from pathlib import Path

def test_is_safe_path():
    root = Path(os.getcwd()).resolve()
    
    # Safe paths
    assert is_safe_path(root / "wiki" / "test.md") == True
    assert is_safe_path(root / "data" / "db.sqlite") == True
    
    # Unsafe paths
    assert is_safe_path(root / "raw" / "test.txt") == False
    assert is_safe_path(root / "raw" / "papers" / "doc.pdf") == False
    assert is_safe_path(Path("/etc/passwd")) == False
    assert is_safe_path(root.parent / "outside.txt") == False

def test_escape_yaml_string():
    assert escape_yaml_string("normal") == '"normal"'
    assert escape_yaml_string('has "quotes"') == '"has \\"quotes\\""'
    assert escape_yaml_string("has\nnewlines") == '"has\\nnewlines"'
    assert escape_yaml_string("") == ""

def test_write_file_safely_prevents_raw_writes():
    root = Path(os.getcwd()).resolve()
    target = str(root / "raw" / "hacked.txt")
    assert write_file_safely(target, "malicious") == False
    assert not os.path.exists(target)
