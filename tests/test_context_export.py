import pytest
import os
from scripts.context_export import export_context, search_memory
import io
import sys

def test_search_memory(capsys):
    # Ensure there's a file to find
    test_file = "wiki/test_search.md"
    os.makedirs("wiki", exist_ok=True)
    with open(test_file, "w") as f:
        f.write("unique_search_term_123")
        
    search_memory("unique_search_term_123")
    captured = capsys.readouterr()
    assert "Found" in captured.out
    assert "test_search.md" in captured.out
    
    os.remove(test_file)

def test_export_context(capsys):
    test_file = "wiki/test_context.md"
    os.makedirs("wiki", exist_ok=True)
    with open(test_file, "w") as f:
        f.write("export_target_keyword")
        
    output = export_context("export_target_keyword")
    assert "Zurvan Context Bundle" in output
    assert "test_context.md" in output
    
    os.remove(test_file)
