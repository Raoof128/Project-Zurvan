import pytest
import os
from scripts.embed import get_embedding, generate_mock_embedding

def test_mock_embedding_deterministic():
    text = "Hello Zurvan"
    vec1 = generate_mock_embedding(text, dim=10)
    vec2 = generate_mock_embedding(text, dim=10)
    assert vec1 == vec2
    assert len(vec1) == 10
    
def test_get_embedding_defaults_to_mock(monkeypatch):
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    res = get_embedding("test")
    assert res['provider'] == "mock"
    assert len(res['vector']) == 384
    
def test_get_embedding_handles_missing_sentence_transformers(monkeypatch):
    # If sentence_transformers isn't installed (or we force ImportError), it should fallback to mock
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "sentence_transformers")
    # Even if installed, we are just testing the fallback mechanics or if it loads.
    # Since we didn't add it to requirements, it will fallback gracefully.
    res = get_embedding("test")
    assert res['provider'] in ["mock", "sentence_transformers"]
