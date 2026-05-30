import pytest
import os
from scripts.query import keyword_search

def test_keyword_search(tmp_path):
    # Setup dummy wiki
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    f1 = wiki_dir / "test1.md"
    f1.write_text("This contains the magic keyword.")
    f2 = wiki_dir / "test2.md"
    f2.write_text("This is unrelated.")
    
    results = keyword_search("magic", str(wiki_dir))
    assert len(results) == 1
    assert "test1.md" in results[0]
