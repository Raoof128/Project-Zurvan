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
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You must output valid JSON only."},
            {"role": "user", "content": prompt}
        ]
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
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
        "options": {
            "temperature": temperature
        }
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["response"]
    except HTTPError as e:
        raise RuntimeError(f"Ollama API error: {e.code} {e.reason}")
    except URLError as e:
        raise RuntimeError(f"Failed to connect to Ollama API: {e.reason}")

def _call_mock(prompt: str, model: str, temperature: float) -> str:
    # Dummy JSON for pipeline testing without an active provider
    dummy_response = {
      "source_id": "dummy_source",
      "summary": {
        "short": "A short summary",
        "detailed": "A more detailed summary of the source."
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
              "location": "line 1"
            }
          ],
          "tags": ["ai", "retrieval"]
        }
      ],
      "concepts": [],
      "entities": [],
      "open_questions": [],
      "possible_contradictions": []
    }
    return json.dumps(dummy_response)

def run_llm(prompt: str, model: str = None, temperature: float = 0.0) -> str:
    """
    Send prompt to selected LLM provider and return raw text.
    """
    provider = os.environ.get("ZURVAN_LLM_PROVIDER")
    if not provider:
        raise ValueError("ZURVAN_LLM_PROVIDER environment variable is missing. Choose 'openai', 'ollama', or 'mock'.")
        
    model = model or os.environ.get("ZURVAN_LLM_MODEL", "default")
    
    if provider.lower() == "openai":
        return _call_openai(prompt, model, temperature)
    elif provider.lower() == "ollama":
        return _call_ollama(prompt, model, temperature)
    elif provider.lower() == "mock":
        return _call_mock(prompt, model, temperature)
    else:
        raise ValueError(f"Unknown ZURVAN_LLM_PROVIDER: '{provider}'. Supported: 'openai', 'ollama', 'mock'.")
