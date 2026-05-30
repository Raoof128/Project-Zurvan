import pytest
import os
from scripts.llm import run_llm

def test_missing_provider(monkeypatch):
    monkeypatch.delenv("ZURVAN_LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="ZURVAN_LLM_PROVIDER environment variable is missing"):
        run_llm("test")

def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "unknown_provider")
    with pytest.raises(ValueError, match="Unknown ZURVAN_LLM_PROVIDER"):
        run_llm("test")

def test_openai_missing_key(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY environment variable is required"):
        run_llm("test")

def test_mock_provider(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "mock")
    response = run_llm("test")
    assert "claim-dummy-001" in response
