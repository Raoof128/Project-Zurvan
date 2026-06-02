import os
import json
import urllib.request
from urllib.error import URLError, HTTPError


def _call_openai(prompt: str, model: str, temperature: float) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required for OpenAI provider.")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You must output valid JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except HTTPError as e:
        raise RuntimeError(f"OpenAI API error: {e.code} {e.reason}")
    except URLError as e:
        raise RuntimeError(f"Failed to connect to OpenAI API: {e.reason}")


def _call_ollama(prompt: str, model: str, temperature: float) -> str:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    url = f"{base_url}/api/generate"
    data = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["response"]
    except HTTPError as e:
        raise RuntimeError(f"Ollama API error: {e.code} {e.reason}")
    except URLError as e:
        raise RuntimeError(f"Failed to connect to Ollama API: {e.reason}")


def _call_anthropic(prompt: str, model: str, temperature: float) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required when using the anthropic provider. "
            "Set it with: export ANTHROPIC_API_KEY=sk-ant-..."
        )
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return "".join(
                block["text"]
                for block in result.get("content", [])
                if block.get("type") == "text"
            )
    except HTTPError as e:
        raise RuntimeError(f"Anthropic API error: {e.code} {e.reason}")
    except URLError as e:
        raise RuntimeError(f"Failed to connect to Anthropic API: {e.reason}")


def _call_mock(prompt: str, model: str, temperature: float) -> str:
    dummy_response = {
        "source_id": "dummy_source",
        "summary": {
            "short": "A short summary",
            "detailed": "A more detailed summary of the source.",
        },
        "claims": [
            {
                "claim_id": "claim-dummy-001",
                "text": "Zurvan stores extracted knowledge as Markdown files.",
                "claim_type": "fact",
                "confidence": "high",
                "evidence": [
                    {
                        "quote": "Zurvan turns raw sources into a persistent Markdown wiki...",
                        "location": "line 1",
                    }
                ],
                "tags": ["ai", "retrieval"],
            }
        ],
        "concepts": [],
        "entities": [],
        "open_questions": [],
        "possible_contradictions": [],
    }
    return json.dumps(dummy_response)


_PROVIDER_DEFAULTS = {
    "mock": "mock",
    "openai": "gpt-4o",
    "ollama": "llama3",
    "anthropic": "claude-sonnet-4-6",
}

_PROVIDERS = {
    "mock": _call_mock,
    "openai": _call_openai,
    "ollama": _call_ollama,
    "anthropic": _call_anthropic,
}


def run_llm(prompt: str, model: str = None, temperature: float = 0.0) -> str:
    """Send prompt to the configured LLM provider and return raw text.
    Defaults to mock when ZURVAN_LLM_PROVIDER is unset."""
    provider = os.environ.get("ZURVAN_LLM_PROVIDER", "mock").lower()
    if provider not in _PROVIDERS:
        raise ValueError(
            f"Unknown ZURVAN_LLM_PROVIDER: '{provider}'. "
            f"Valid providers: {', '.join(_PROVIDERS.keys())}"
        )
    resolved_model = (
        model
        or os.environ.get("ZURVAN_LLM_MODEL")
        or _PROVIDER_DEFAULTS[provider]
    )
    return _PROVIDERS[provider](prompt, resolved_model, temperature)
