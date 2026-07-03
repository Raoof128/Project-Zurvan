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

def test_add_claim_resolves_source_against_project_root(tmp_path, monkeypatch):
    # Regression: add_claim checked the source path relative to the CWD, so
    # zurvan_claim_add via MCP (launched from any other directory) rejected
    # valid repo-relative sources like "wiki/foo.md".
    import scripts.memory as memory
    import scripts.safe_write as safe_write

    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "src.md").write_text("the exact evidence line")
    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)
    # A CWD from which the relative path does NOT exist:
    monkeypatch.chdir(tmp_path / "wiki")

    ok = memory.add_claim(
        "test claim", "wiki/src.md", "the exact evidence line", "high", ["t"]
    )

    assert ok is True
    claims = list((tmp_path / "wiki" / "claims").glob("claim-*.md"))
    assert len(claims) == 1
