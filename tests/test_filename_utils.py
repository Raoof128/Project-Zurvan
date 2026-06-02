from scripts.filename_utils import sanitize_filename

def test_alphanumeric_passthrough():
    assert sanitize_filename("RAG") == "RAG"

def test_spaces_become_underscores():
    assert sanitize_filename("knowledge graph") == "knowledge_graph"

def test_special_chars_become_underscores():
    assert sanitize_filename("hello/world:test") == "hello_world_test"

def test_hyphens_and_underscores_preserved():
    assert sanitize_filename("my-concept_name") == "my-concept_name"

def test_empty_string():
    assert sanitize_filename("") == ""
