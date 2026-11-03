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
    
def test_get_embedding_missing_sentence_transformers_is_an_error_not_mock(monkeypatch):
    # Audit 2026-10-22 F4: a requested real provider that cannot load used to
    # come back as mock vectors labelled a success. It is a typed failure now.
    import sys
    import scripts.embed as embed
    from scripts.embed import EmbeddingUnavailable
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "sentence_transformers")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    embed._MODEL_CACHE.clear()
    with pytest.raises(EmbeddingUnavailable):
        get_embedding("test")
