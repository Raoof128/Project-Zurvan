import pytest
import os
import json
from unittest.mock import patch, MagicMock
from scripts.llm import run_llm

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

def test_unset_provider_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("ZURVAN_LLM_PROVIDER", raising=False)
    from scripts.llm import run_llm
    result = run_llm("test")
    assert "dummy_source" in result

def test_provider_registry_contains_all_providers():
    from scripts.llm import _PROVIDERS
    assert set(_PROVIDERS.keys()) == {"mock", "openai", "ollama", "anthropic"}

def test_unknown_provider_lists_valid_names(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "gopher")
    from scripts.llm import run_llm
    with pytest.raises(ValueError) as exc:
        run_llm("test")
    msg = str(exc.value)
    assert "anthropic" in msg
    assert "mock" in msg

def test_mock_makes_zero_network_calls(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "mock")
    with patch("urllib.request.urlopen") as mock_open:
        from scripts.llm import run_llm
        run_llm("test prompt")
        mock_open.assert_not_called()

def test_anthropic_missing_key_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from scripts.llm import run_llm
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        run_llm("test")

def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_urlopen(req):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode())
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": '{"result": "ok"}'}]
        }).encode()
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        from scripts.llm import run_llm
        run_llm("hello")

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"].get("X-api-key") == "sk-test"
    assert captured["headers"].get("Anthropic-version") == "2023-06-01"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert "max_tokens" in captured["body"]
    assert "temperature" not in captured["body"]

def test_anthropic_response_joins_multiple_text_blocks(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_urlopen(req):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps({
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": " World"},
            ]
        }).encode()
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        from scripts.llm import run_llm
        result = run_llm("test")
    assert result == "Hello World"

def test_zurvan_llm_model_overrides_anthropic_default(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ZURVAN_LLM_MODEL", "claude-opus-4-8")
    captured = {}

    def fake_urlopen(req):
        captured["body"] = json.loads(req.data.decode())
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "ok"}]
        }).encode()
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        from scripts.llm import run_llm
        run_llm("test")
    assert captured["body"]["model"] == "claude-opus-4-8"

def test_anthropic_zero_text_blocks_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_urlopen(req):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        # Only tool_use block, no text block
        mock_resp.read.return_value = json.dumps({
            "content": [{"type": "tool_use", "id": "x"}]
        }).encode()
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        from scripts.llm import run_llm
        with pytest.raises(RuntimeError, match="no text content"):
            run_llm("test")

def test_anthropic_http_error_raises_runtime_error(monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_urlopen(req):
        raise HTTPError(req.full_url, 529, "Overloaded", {}, None)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        from scripts.llm import run_llm
        with pytest.raises(RuntimeError, match="529"):
            run_llm("test")
