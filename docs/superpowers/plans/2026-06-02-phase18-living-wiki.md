# Phase 18: Living Wiki + Provider Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four largest gaps in Zurvan's compounding-wiki loop: add Anthropic as an LLM provider, make concept/entity pages update across sources, file query answers back into the wiki, and make the log.md grep-parseable — plus image detection stubs and two output format renders.

**Architecture:** Three sequential sub-phases (18a → 18b → 18c). 18a refactors `llm.py` into a provider registry. 18b introduces `wiki_merge.py` as the canonical concept/entity writer, adds `--save` to context export, and standardises the log format. 18c adds image-skip detection and `--format table|marp` rendering. Each sub-phase ends with `check.sh` green.

**Tech Stack:** Python 3.12, pytest, raw `urllib.request` (no new SDK dependencies), existing `safe_write.py` for all file writes.

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `scripts/llm.py` | Modify | Provider registry + Anthropic provider |
| `scripts/wiki_merge.py` | **Create** | Canonical concept/entity writer; shared log formatter |
| `scripts/extract.py` | Modify | Route concept/entity pages through `merge_extraction()` |
| `scripts/ingest.py` | Modify | Use `append_log_ingest()` from wiki_merge; image detection |
| `scripts/context_export.py` | Modify | Add `save`, `fmt` params; `--format table/marp` rendering |
| `scripts/cli.py` | Modify | Add `--save`, `--format` flags to context/search parsers |
| `tests/test_llm.py` | Modify | Extend with 18a provider tests |
| `tests/test_wiki_merge.py` | **Create** | Merge logic + log format tests |
| `tests/test_context_export.py` | Modify | `--save` and format rendering tests |
| `tests/test_ingest.py` | Modify | Image detection tests + log format test |

---

## Task 1: 18a — Provider registry + Anthropic (TDD)

**Files:**
- Modify: `tests/test_llm.py`
- Modify: `scripts/llm.py`

- [ ] **Step 1: Write failing tests for 18a**

Append to `tests/test_llm.py`:

```python
import json
from unittest.mock import patch, MagicMock

def test_provider_registry_contains_all_providers(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "mock")
    from scripts.llm import _PROVIDERS
    assert set(_PROVIDERS.keys()) == {"mock", "openai", "ollama", "anthropic"}

def test_unknown_provider_lists_valid_names(monkeypatch):
    monkeypatch.setenv("ZURVAN_LLM_PROVIDER", "gopher")
    from scripts import llm as llm_mod
    import importlib; importlib.reload(llm_mod)
    with pytest.raises(ValueError) as exc:
        llm_mod.run_llm("test")
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
        body = json.loads(req.data.decode())
        captured["body"] = body
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=. pytest tests/test_llm.py -v 2>&1 | tail -20
```

Expected: 7 new tests fail (ImportError on `_PROVIDERS` and `RuntimeError` for Anthropic).

- [ ] **Step 3: Rewrite `scripts/llm.py`**

Replace the entire file:

```python
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
    """Send prompt to the configured LLM provider and return raw text."""
    provider = os.environ.get("ZURVAN_LLM_PROVIDER", "").lower()
    if not provider:
        raise ValueError(
            "ZURVAN_LLM_PROVIDER is not set. "
            f"Valid providers: {', '.join(_PROVIDERS.keys())}"
        )
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
```

- [ ] **Step 4: Run all llm tests**

```bash
PYTHONPATH=. pytest tests/test_llm.py -v 2>&1 | tail -20
```

Expected: all 11 tests pass.

- [ ] **Step 5: Run full suite to confirm nothing regressed**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 6: Commit 18a**

```bash
git add scripts/llm.py tests/test_llm.py
git commit -m "feat(18a): Add Anthropic provider and provider registry to llm.py"
```

---

## Task 2: 18b — Shared log formatter + wiki_merge.py skeleton (TDD)

**Files:**
- Create: `tests/test_wiki_merge.py`
- Create: `scripts/wiki_merge.py`

- [ ] **Step 1: Create `tests/test_wiki_merge.py` with log format tests**

```python
import os
import re
import pytest
from pathlib import Path


def test_log_event_matches_grep_pattern(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_event
    append_log_event("ingest", "example.pdf")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[", log, re.MULTILINE)
    assert "ingest" in log
    assert "example.pdf" in log


def test_log_event_escapes_pipe_in_parts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_event
    append_log_event("query-save", "my|topic|with|pipes")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "my\\|topic\\|with\\|pipes" in log


def test_log_ingest_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_ingest
    append_log_ingest("notes.txt")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "ingest" in log
    assert "notes.txt" in log


def test_log_merge_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_merge
    append_log_merge("RAG", 3)

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "merge" in log
    assert "RAG" in log
    assert "3 sources" in log


def test_log_save_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_save
    append_log_save("vector-search-reliability")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "query-save" in log
    assert "vector-search-reliability" in log


def test_log_image_skip_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_image_skip
    append_log_image_skip("diagram.png")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "image-skip" in log
    assert "diagram.png" in log
    assert "pending visual extraction" in log
```

- [ ] **Step 2: Run to verify all fail**

```bash
PYTHONPATH=. pytest tests/test_wiki_merge.py -v 2>&1 | tail -15
```

Expected: 6 tests fail with `ModuleNotFoundError: No module named 'scripts.wiki_merge'`.

- [ ] **Step 3: Create `scripts/wiki_merge.py` with log helpers only**

```python
import os
import datetime
from typing import Dict, Any, List

from scripts.safe_write import append_file_safely, write_file_safely


# ── Log helpers ────────────────────────────────────────────────────────────────

def append_log_event(kind: str, *parts: str) -> None:
    """Shared log formatter. Produces grep-parseable ## [YYYY-MM-DD] kind | parts entries."""
    log_path = os.path.join("wiki", "log.md")
    date = datetime.date.today().isoformat()
    safe_parts = [str(p).replace("|", "\\|") for p in parts]
    entry = f"\n## [{date}] {kind} | {' | '.join(safe_parts)}\n"
    append_file_safely(log_path, entry)


def append_log_ingest(path: str) -> None:
    append_log_event("ingest", path)


def append_log_merge(name: str, source_count: int) -> None:
    append_log_event("merge", name, f"{source_count} sources")


def append_log_save(slug: str) -> None:
    append_log_event("query-save", slug)


def append_log_image_skip(filename: str) -> None:
    append_log_event("image-skip", filename, "pending visual extraction")
```

- [ ] **Step 4: Run log tests**

```bash
PYTHONPATH=. pytest tests/test_wiki_merge.py -v 2>&1 | tail -15
```

Expected: all 6 pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/wiki_merge.py tests/test_wiki_merge.py
git commit -m "feat(18b): Add wiki_merge.py with shared log formatter"
```

---

## Task 3: 18b — Concept/entity merge logic (TDD)

**Files:**
- Modify: `tests/test_wiki_merge.py` (append tests)
- Modify: `scripts/wiki_merge.py` (add merge logic)

- [ ] **Step 1: Append merge tests to `tests/test_wiki_merge.py`**

```python
def _make_extraction(source_id, concepts=None, entities=None):
    return {
        "source_id": source_id,
        "concepts": concepts or [],
        "entities": entities or [],
    }


def test_merge_creates_new_concept_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    data = _make_extraction(
        "source_a",
        concepts=[{"name": "RAG", "definition": "Retrieval-Augmented Generation"}],
    )
    merge_extraction(data, wiki_dir=str(tmp_path / "wiki"))

    page = tmp_path / "wiki" / "concepts" / "RAG.md"
    assert page.exists()
    content = page.read_text()
    assert "source_a" in content
    assert "source_count: 1" in content


def test_merge_additive_two_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    for sid in ("source_a", "source_b"):
        merge_extraction(
            _make_extraction(sid, concepts=[{"name": "RAG", "definition": f"Def from {sid}"}]),
            wiki_dir=str(tmp_path / "wiki"),
        )

    content = (tmp_path / "wiki" / "concepts" / "RAG.md").read_text()
    assert "Evidence from source_a" in content
    assert "Evidence from source_b" in content
    assert "source_count: 2" in content


def test_merge_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    data = _make_extraction("source_a", concepts=[{"name": "RAG", "definition": "def"}])
    merge_extraction(data, wiki_dir=str(tmp_path / "wiki"))
    first = (tmp_path / "wiki" / "concepts" / "RAG.md").read_text()
    merge_extraction(data, wiki_dir=str(tmp_path / "wiki"))
    second = (tmp_path / "wiki" / "concepts" / "RAG.md").read_text()

    assert first == second


def test_merge_preserves_existing_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")
    concepts = wiki / "concepts"
    concepts.mkdir()
    (concepts / "RAG.md").write_text(
        "---\ntype: concept\nsources: source_a\nsource_count: 1\nlast_updated: 2026-01-01\n---\n\n# RAG\n\n## Definition\nOriginal definition.\n"
    )

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction("source_b", concepts=[{"name": "RAG", "definition": "New evidence"}]),
        wiki_dir=str(wiki),
    )

    content = (concepts / "RAG.md").read_text()
    assert "Original definition." in content
    assert "Evidence from source_b" in content


def test_source_count_is_derived_not_incremented(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")
    concepts = wiki / "concepts"
    concepts.mkdir()
    # source_count is wrong on purpose — should be corrected to len(sources)
    (concepts / "RAG.md").write_text(
        "---\ntype: concept\nsources: source_a\nsource_count: 999\nlast_updated: 2026-01-01\n---\n\n# RAG\n\n## Definition\nOriginal.\n"
    )

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction("source_b", concepts=[{"name": "RAG", "definition": "new"}]),
        wiki_dir=str(wiki),
    )

    content = (concepts / "RAG.md").read_text()
    assert "source_count: 2" in content
    assert "source_count: 999" not in content


def test_existing_source_id_skips_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    data = _make_extraction("source_a", concepts=[{"name": "RAG", "definition": "def"}])
    merge_extraction(data, wiki_dir=str(wiki))
    before = (wiki / "concepts" / "RAG.md").read_text()
    merge_extraction(data, wiki_dir=str(wiki))
    after = (wiki / "concepts" / "RAG.md").read_text()

    assert before == after


def test_merge_entity_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    data = _make_extraction(
        "source_a",
        entities=[{"name": "Karpathy", "description": "AI researcher", "entity_type": "person"}],
    )
    merge_extraction(data, wiki_dir=str(wiki))

    page = wiki / "entities" / "Karpathy.md"
    assert page.exists()
    content = page.read_text()
    assert "entity_type: person" in content
    assert "source_a" in content
```

- [ ] **Step 2: Run to verify new tests fail**

```bash
PYTHONPATH=. pytest tests/test_wiki_merge.py -v 2>&1 | tail -15
```

Expected: 7 new tests fail with `ImportError` or `AttributeError` on `merge_extraction`.

- [ ] **Step 3: Add merge logic to `scripts/wiki_merge.py`**

Append after the log helpers:

```python

# ── Frontmatter helpers ────────────────────────────────────────────────────────

def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)


def _parse_fm(content: str):
    """Returns (fm_dict, body_str). Values are raw strings."""
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]
    fm = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, body


def _build_fm(fm: dict) -> str:
    lines = [f"{k}: {v}" for k, v in fm.items()]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def _parse_sources(raw: str) -> List[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── Core merge logic ───────────────────────────────────────────────────────────

def _merge_page(
    page_path: str,
    name: str,
    page_type: str,
    definition: str,
    source_id: str,
    extra_fm: dict = None,
) -> None:
    os.makedirs(os.path.dirname(page_path), exist_ok=True)

    if not os.path.exists(page_path):
        fm = {
            "type": page_type,
            "sources": source_id,
            "source_count": "1",
            "last_updated": datetime.date.today().isoformat(),
        }
        if extra_fm:
            fm.update(extra_fm)
        body = f"# {name}\n\n## Definition\n{definition}\n"
        write_file_safely(page_path, _build_fm(fm) + body)
        append_log_merge(name, 1)
        return

    with open(page_path, "r", encoding="utf-8") as fh:
        existing = fh.read()

    fm, body = _parse_fm(existing)
    sources = _parse_sources(fm.get("sources", ""))

    if source_id in sources:
        return  # Idempotent

    sources.append(source_id)
    fm["sources"] = ", ".join(sources)
    fm["source_count"] = str(len(sources))   # always derived, never blind increment
    fm["last_updated"] = datetime.date.today().isoformat()

    new_section = f"\n## Evidence from {source_id}\n\n{definition}\n"
    write_file_safely(page_path, _build_fm(fm) + body + new_section)
    append_log_merge(name, len(sources))


def merge_extraction(data: Dict[str, Any], wiki_dir: str = "wiki") -> None:
    """
    Canonical writer for concept and entity wiki pages.
    Called by extract.py instead of writing pages directly.
    Purely additive and idempotent.
    """
    source_id = data.get("source_id", "unknown")

    for concept in data.get("concepts", []):
        name = concept.get("name", "")
        if not name:
            continue
        slug = _sanitize(name)
        _merge_page(
            page_path=os.path.join(wiki_dir, "concepts", f"{slug}.md"),
            name=name,
            page_type="concept",
            definition=concept.get("definition", ""),
            source_id=source_id,
        )

    for entity in data.get("entities", []):
        name = entity.get("name", "")
        if not name:
            continue
        slug = _sanitize(name)
        _merge_page(
            page_path=os.path.join(wiki_dir, "entities", f"{slug}.md"),
            name=name,
            page_type="entity",
            definition=entity.get("description", ""),
            source_id=source_id,
            extra_fm={"entity_type": entity.get("entity_type", "other")},
        )
```

- [ ] **Step 4: Run all wiki_merge tests**

```bash
PYTHONPATH=. pytest tests/test_wiki_merge.py -v 2>&1 | tail -20
```

Expected: all 13 tests pass.

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add scripts/wiki_merge.py tests/test_wiki_merge.py
git commit -m "feat(18b): Add merge_extraction() — canonical compounding wiki writer"
```

---

## Task 4: 18b — Route extract.py through wiki_merge

**Files:**
- Modify: `scripts/extract.py`

- [ ] **Step 1: Remove the inline concept/entity writing blocks from `scripts/extract.py`**

Find and replace these two blocks (around lines 55–85):

Old — **Concepts block** (remove entirely):
```python
    # Concepts
    concepts_dir = os.path.join("wiki", "concepts")
    os.makedirs(concepts_dir, exist_ok=True)
    for concept in data.get("concepts", []):
        cname = sanitize_filename(concept["name"])
        concept_file = os.path.join(concepts_dir, f"{cname}.md")
        if is_safe_filename(concept_file):
            with open(concept_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: concept\nsource_id: {source_id}\n---\n\n# {concept['name']}\n\n## Definition\n{concept['definition']}\n")
```

Old — **Entities block** (remove entirely):
```python
    # Entities
    entities_dir = os.path.join("wiki", "entities")
    os.makedirs(entities_dir, exist_ok=True)
    for ent in data.get("entities", []):
        ename = sanitize_filename(ent["name"])
        ent_file = os.path.join(entities_dir, f"{ename}.md")
        if is_safe_filename(ent_file):
            with open(ent_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: entity\nentity_type: {ent.get('entity_type', 'other')}\nsource_id: {source_id}\n---\n\n# {ent['name']}\n\n{ent['description']}\n")
```

Replace both blocks with:
```python
    # Concepts and Entities: routed through wiki_merge for compounding wiki updates
    from scripts.wiki_merge import merge_extraction
    merge_extraction(data)
```

- [ ] **Step 2: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 3: Commit**

```bash
git add scripts/extract.py
git commit -m "feat(18b): Route extract.py concept/entity writing through merge_extraction()"
```

---

## Task 5: 18b — Update ingest.py to use new log format

**Files:**
- Modify: `tests/test_ingest.py` (append test)
- Modify: `scripts/ingest.py`

- [ ] **Step 1: Append log format test to `tests/test_ingest.py`**

```python
import re

def test_append_log_ingest_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_ingest
    append_log_ingest("example.pdf")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[", log, re.MULTILINE)
    assert "ingest" in log
    assert "example.pdf" in log
```

- [ ] **Step 2: Run to verify test passes** (it uses `wiki_merge.append_log_ingest`, which already exists)

```bash
PYTHONPATH=. pytest tests/test_ingest.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 3: Update `append_log()` in `scripts/ingest.py`**

Replace:
```python
def append_log(filename):
    log_path = os.path.join("wiki", "log.md")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n- **{timestamp}**: Ingested `{filename}`\n"
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(entry)
```

With:
```python
def append_log(filename):
    from scripts.wiki_merge import append_log_ingest
    append_log_ingest(filename)
```

- [ ] **Step 4: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add scripts/ingest.py tests/test_ingest.py
git commit -m "feat(18b): Update ingest.py log format to grep-parseable ## [date] pattern"
```

---

## Task 6: 18b — Add `--save` to context_export.py (TDD)

**Files:**
- Modify: `tests/test_context_export.py` (append tests)
- Modify: `scripts/context_export.py`

- [ ] **Step 1: Append `--save` tests to `tests/test_context_export.py`**

```python
def test_save_writes_synthesis_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "test_save_kw.md").write_text("save_unique_kw_abc123")

    from scripts.context_export import export_context
    export_context("save_unique_kw_abc123", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 1


def test_save_synthesis_has_required_frontmatter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "test_fm_kw.md").write_text("fm_unique_kw_xyz789")

    from scripts.context_export import export_context
    export_context("fm_unique_kw_xyz789", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    content = syntheses[0].read_text()
    assert "type: synthesis" in content
    assert "query:" in content
    assert "created_at:" in content
    assert "tags: synthesis, query-derived" in content


def test_save_no_overwrite_same_query(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "test_overwrite_kw.md").write_text("overwrite_kw_unique123")

    from scripts.context_export import export_context
    export_context("overwrite_kw_unique123", save=True)
    export_context("overwrite_kw_unique123", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 2


def test_save_false_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_nosave.md").write_text("nosave_kw")

    from scripts.context_export import export_context
    export_context("nosave_kw", save=False)

    synth_dir = tmp_path / "wiki" / "syntheses"
    assert not synth_dir.exists() or not list(synth_dir.glob("*.md"))
```

- [ ] **Step 2: Run to verify all 4 fail**

```bash
PYTHONPATH=. pytest tests/test_context_export.py -v 2>&1 | tail -15
```

Expected: 4 new tests fail (`TypeError: export_context() got an unexpected keyword argument 'save'`).

- [ ] **Step 3: Add `save` parameter and `_save_synthesis()` to `scripts/context_export.py`**

At the top of the file, add `import datetime` (it's not currently imported).

Add this helper function before `export_context`:

```python
def _save_synthesis(topic: str, markdown_content: str, source_paths: list) -> None:
    """Write a canonical Markdown synthesis page to wiki/syntheses/."""
    import datetime
    from scripts.wiki_merge import append_log_save
    from scripts.safe_write import write_file_safely

    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in topic)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    synth_dir = os.path.join("wiki", "syntheses")
    os.makedirs(synth_dir, exist_ok=True)
    filename = f"{datetime.date.today().isoformat()}-{timestamp}-{slug}.md"
    synth_path = os.path.join(synth_dir, filename)

    fm = (
        f"---\n"
        f"type: synthesis\n"
        f"query: {topic}\n"
        f"sources: {', '.join(source_paths)}\n"
        f"created_at: {datetime.datetime.now().isoformat()}\n"
        f"tags: synthesis, query-derived\n"
        f"---\n\n"
    )
    write_file_safely(synth_path, fm + markdown_content)
    append_log_save(slug)
```

Update `export_context` signature and body — change:
```python
def export_context(topic: str, limit: int = 10, hybrid: bool = False, graph: bool = False, depth: int = 1) -> str:
```
To:
```python
def export_context(topic: str, limit: int = 10, hybrid: bool = False, graph: bool = False, depth: int = 1, save: bool = False, fmt: str = "markdown") -> str:
```

Add at the end of `export_context`, before `return "\n".join(output)`:

```python
    base_output = "\n".join(output)

    if save:
        _save_synthesis(topic, base_output, seed_paths)

    return base_output
```

(Remove the existing `return "\n".join(output)` line and replace with the block above.)

- [ ] **Step 4: Run context_export tests**

```bash
PYTHONPATH=. pytest tests/test_context_export.py -v 2>&1 | tail -15
```

Expected: all 6 tests pass.

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_export.py tests/test_context_export.py
git commit -m "feat(18b): Add --save flag to export_context() — file answers back into wiki"
```

---

## Task 7: 18b — Wire `--save` into cli.py + run check.sh

**Files:**
- Modify: `scripts/cli.py`

- [ ] **Step 1: Add `--save` to the context and search parsers in `scripts/cli.py`**

Find the context parser block (line ~278):
```python
    context_parser.add_argument("--depth", type=int, default=1, help="Graph expansion depth")
```

Add after it:
```python
    context_parser.add_argument("--save", action="store_true", help="File answer back into wiki/syntheses/")
```

Find the search parser block (line ~273):
```python
    search_parser.add_argument("--hybrid", action="store_true", help="Use hybrid search")
```

Add after it:
```python
    search_parser.add_argument("--save", action="store_true", help="File results into wiki/syntheses/")
```

- [ ] **Step 2: Update the context handler (line ~683)**

Change:
```python
    elif args.command == "context":
        from scripts.context_export import export_context
        bundle = export_context(args.topic, args.limit, args.hybrid, args.graph, args.depth)
        print(bundle)
```

To:
```python
    elif args.command == "context":
        from scripts.context_export import export_context
        bundle = export_context(
            args.topic, args.limit, args.hybrid, args.graph, args.depth,
            save=getattr(args, "save", False),
        )
        print(bundle)
```

- [ ] **Step 3: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 4: Run check.sh — end of 18b milestone**

```bash
PYTHONPATH=. bash scripts/check.sh 2>&1 | tail -10
```

Expected: `🎉 All Zurvan checks passed successfully.`

- [ ] **Step 5: Commit 18b complete**

```bash
git add scripts/cli.py
git commit -m "feat(18b): Wire --save into CLI context/search parsers — 18b complete"
```

---

## Task 8: 18c — Image-aware skeleton (TDD)

**Files:**
- Modify: `tests/test_ingest.py` (append tests)
- Modify: `scripts/ingest.py`
- Modify: `scripts/extract.py`

- [ ] **Step 1: Append image detection tests to `tests/test_ingest.py`**

```python
def test_image_file_produces_pending_visual_stub(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "diagram.png"
    img.write_bytes(b"fakepng")

    ingest_image_stub(str(img))

    stub = tmp_path / "wiki" / "sources" / "diagram_png.md"
    assert stub.exists()
    content = stub.read_text()
    assert "status: pending-visual" in content
    assert "type: image-stub" in content


def test_image_stub_path_is_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "chart.jpg"
    img.write_bytes(b"fakejpg")

    ingest_image_stub(str(img))

    stub = tmp_path / "wiki" / "sources" / "chart_jpg.md"
    content = stub.read_text()
    import re
    path_match = re.search(r"path: (.+)", content)
    assert path_match
    assert not os.path.isabs(path_match.group(1).strip())


def test_image_stub_logs_image_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "photo.png"
    img.write_bytes(b"data")

    ingest_image_stub(str(img))

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "image-skip" in log
    assert "photo.png" in log


def test_image_stub_prints_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "icon.gif"
    img.write_bytes(b"data")

    ingest_image_stub(str(img))

    captured = capsys.readouterr()
    assert "Image detected but not processed" in captured.out
    assert "icon.gif" in captured.out


def test_image_stub_handles_filename_collision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    # Call twice with same name
    img = tmp_path / "image.png"
    img.write_bytes(b"data")

    ingest_image_stub(str(img))
    ingest_image_stub(str(img))

    stubs = list((tmp_path / "wiki" / "sources").glob("image_png*.md"))
    assert len(stubs) == 2


def test_is_image_file_detects_extensions():
    from scripts.ingest import is_image_file
    assert is_image_file("photo.png")
    assert is_image_file("diagram.JPG")
    assert is_image_file("chart.webp")
    assert not is_image_file("notes.txt")
    assert not is_image_file("paper.pdf")
```

- [ ] **Step 2: Run to verify all fail**

```bash
PYTHONPATH=. pytest tests/test_ingest.py -v 2>&1 | tail -15
```

Expected: 6 new tests fail.

- [ ] **Step 3: Add image detection functions to `scripts/ingest.py`**

Add after the existing imports:

```python
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def is_image_file(filepath: str) -> bool:
    return os.path.splitext(filepath)[1].lower() in IMAGE_EXTENSIONS


def ingest_image_stub(filepath: str) -> None:
    """Create a pending-visual stub page for image files. No extraction, no OCR."""
    from scripts.wiki_merge import append_log_image_skip
    from scripts.safe_write import write_file_safely

    basename = os.path.basename(filepath)
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in basename)

    source_dir = os.path.join("wiki", "sources")
    os.makedirs(source_dir, exist_ok=True)

    # Collision protection
    candidate = os.path.join(source_dir, f"{slug}.md")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(source_dir, f"{slug}-{counter}.md")
        counter += 1

    relative_path = f"sources/{slug}.md"
    content = (
        f"---\n"
        f"type: image-stub\n"
        f"status: pending-visual\n"
        f"path: {relative_path}\n"
        f"original: {basename}\n"
        f"ingested_at: {datetime.datetime.now().isoformat()}\n"
        f"---\n\n"
        f"# {basename}\n\n"
        f"Image detected but not processed. Pending visual extraction.\n"
    )
    write_file_safely(candidate, content)
    append_log_image_skip(basename)
    print(f"⚠ Image detected but not processed: {basename}")
```

Also update `main()` in `ingest.py` to check for images before `extract_text()`:

```python
    # Check for image files before attempting text extraction
    if is_image_file(args.filepath):
        ingest_image_stub(args.filepath)
        return
```

Add this block immediately before the line `file_hash = calculate_hash(args.filepath)`.

- [ ] **Step 4: Add image guard to `scripts/extract.py`**

At the top of `extract_source()`, after `if not os.path.exists(filepath):`, add:

```python
    from scripts.ingest import is_image_file
    if is_image_file(filepath):
        print(f"⚠ Skipping LLM extraction for image file: {filepath}")
        return
```

- [ ] **Step 5: Run all ingest tests**

```bash
PYTHONPATH=. pytest tests/test_ingest.py -v 2>&1 | tail -15
```

Expected: all tests pass.

- [ ] **Step 6: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add scripts/ingest.py scripts/extract.py tests/test_ingest.py
git commit -m "feat(18c): Add image-aware skeleton — detect, stub, log, no OCR"
```

---

## Task 9: 18c — Output format rendering + `--format` CLI flag (TDD)

**Files:**
- Modify: `tests/test_context_export.py` (append tests)
- Modify: `scripts/context_export.py`
- Modify: `scripts/cli.py`

- [ ] **Step 1: Append format rendering tests to `tests/test_context_export.py`**

```python
def test_format_table_produces_markdown_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_table.md").write_text("table_kw_unique_abc")

    from scripts.context_export import export_context
    output = export_context("table_kw_unique_abc", fmt="table")

    assert "| Source |" in output
    assert "|---|" in output


def test_format_table_escapes_pipes_in_excerpts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "pipe_test.md").write_text("pipe_kw_unique | has | pipes")

    from scripts.context_export import export_context
    output = export_context("pipe_kw_unique", fmt="table")

    # The structural separator pipes in the table header are fine
    # but content pipes in the excerpt must be escaped
    lines = [l for l in output.splitlines() if "has" in l]
    if lines:
        assert "\\|" in lines[0]


def test_format_marp_starts_with_frontmatter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_marp.md").write_text("marp_kw_unique_xyz")

    from scripts.context_export import export_context
    output = export_context("marp_kw_unique_xyz", fmt="marp")

    assert output.startswith("---\nmarp: true\n---")


def test_format_table_empty_results_graceful(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()

    from scripts.context_export import export_context
    output = export_context("absolutely_no_match_xyzxyz", fmt="table")

    assert output  # does not crash
    assert "No results" in output


def test_format_marp_empty_results_graceful(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()

    from scripts.context_export import export_context
    output = export_context("absolutely_no_match_xyzxyz", fmt="marp")

    assert output
    assert "marp: true" in output


def test_format_markdown_default_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "default_fmt.md").write_text("default_fmt_kw_abc")

    from scripts.context_export import export_context
    output = export_context("default_fmt_kw_abc", fmt="markdown")

    assert "Zurvan Context Bundle" in output


def test_save_with_marp_format_writes_canonical_markdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "marp_save.md").write_text("marp_save_kw_unique")

    from scripts.context_export import export_context
    export_context("marp_save_kw_unique", save=True, fmt="marp")

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 1
    content = syntheses[0].read_text()
    assert "type: synthesis" in content
    assert not content.startswith("---\nmarp: true")
```

- [ ] **Step 2: Run to verify new tests fail**

```bash
PYTHONPATH=. pytest tests/test_context_export.py -v 2>&1 | tail -20
```

Expected: 7 new tests fail (`TypeError` on unknown `fmt` kwarg).

- [ ] **Step 3: Add `_format_table()`, `_format_marp()`, and `fmt` param to `scripts/context_export.py`**

Add these two helpers before `export_context`:

```python
def _format_table(results: list) -> str:
    if not results:
        return "No results found.\n"
    rows = ["| Source | Score | Excerpt |", "|---|---|---|"]
    for r in results:
        source = r["source_path"]
        score = f"{r.get('hybrid_score', 0):.2f}"
        excerpt = r["text"][:120].replace("\n", " ").replace("|", "\\|")
        rows.append(f"| {source} | {score} | {excerpt} |")
    return "\n".join(rows)


def _format_marp(topic: str, results: list) -> str:
    if not results:
        return f"---\nmarp: true\n---\n\n# Context: {topic}\n\nNo results found.\n"
    slides = ["---\nmarp: true\n---", f"\n# Context: {topic}\n"]
    for r in results:
        path = r["source_path"]
        score = r.get("hybrid_score", 0)
        excerpt = r["text"][:300].replace("\n", " ")
        slides.append(f"\n---\n\n## {path} ({score:.2f})\n\n{excerpt}\n")
    return "\n".join(slides)
```

The `fmt` parameter is already in the signature from Task 6. Now add the format dispatch at the end of `export_context`, replacing the `return base_output` line:

```python
    if save:
        _save_synthesis(topic, base_output, seed_paths)

    if fmt == "table":
        return _format_table(results)
    elif fmt == "marp":
        return _format_marp(topic, results)
    return base_output
```

- [ ] **Step 4: Run all context_export tests**

```bash
PYTHONPATH=. pytest tests/test_context_export.py -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Add `--format` flag to cli.py context parser**

Find the context parser block. After the `--save` line added in Task 7, add:

```python
    context_parser.add_argument(
        "--format",
        choices=["markdown", "table", "marp"],
        default="markdown",
        dest="output_format",
        help="Output format for stdout (markdown/table/marp). --save always writes canonical markdown.",
    )
```

Update the context handler:

```python
    elif args.command == "context":
        from scripts.context_export import export_context
        bundle = export_context(
            args.topic, args.limit, args.hybrid, args.graph, args.depth,
            save=getattr(args, "save", False),
            fmt=getattr(args, "output_format", "markdown"),
        )
        print(bundle)
```

- [ ] **Step 6: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -5
```

Expected: 131+ passed, 0 failed.

- [ ] **Step 7: Run check.sh — end of 18c milestone**

```bash
PYTHONPATH=. bash scripts/check.sh 2>&1 | tail -10
```

Expected: `🎉 All Zurvan checks passed successfully.`

- [ ] **Step 8: Commit 18c complete**

```bash
git add scripts/context_export.py scripts/cli.py tests/test_context_export.py
git commit -m "feat(18c): Add --format table/marp to context export — 18c complete"
```

---

## Task 10: Final — Update docs and AGENTS/CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/workflows_and_plans.md`

- [ ] **Step 1: Prepend Phase 18 entry to `CHANGELOG.md`**

Use the `Raouf:` template. Add after `## Change Log`:

```markdown
### 2026-06-02 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 18: Living Wiki + Provider Expansion
- **Summary:** Closed the four largest gaps vs the Karpathy LLM Wiki vision. (18a) Refactored llm.py into a provider registry; added Anthropic/Claude as optional provider via raw urllib (no SDK). (18b) Created wiki_merge.py as canonical concept/entity writer — pages now compound across sources instead of accumulating as per-source fossils; added --save flag to zurvan context to file answers back into wiki/syntheses/; standardised log.md to grep-parseable ## [date] format. (18c) Added image-aware skeleton — image files detected, stubbed as pending-visual, logged, and skipped cleanly with no OCR or network calls; added --format table/marp stdout rendering with --save always writing canonical Markdown.
- **Files Changed:**
  - `scripts/llm.py` — Provider registry + Anthropic provider
  - `scripts/wiki_merge.py` — New: canonical merge writer + shared log formatter
  - `scripts/extract.py` — Route concept/entity writing through merge_extraction()
  - `scripts/ingest.py` — Use new log format; add image detection
  - `scripts/context_export.py` — Add --save and --format rendering
  - `scripts/cli.py` — Add --save and --format flags
  - `tests/test_llm.py`, `tests/test_wiki_merge.py`, `tests/test_context_export.py`, `tests/test_ingest.py` — New/extended tests
- **Verification:** pytest → 131+ passed, 0 failed. check.sh passed after each sub-phase.
- **Follow-ups:** Phase 19: Review OpenAI model default (GPT-5.x). Phase 19+: Image extraction via local vision or OCR provider.
```

- [ ] **Step 2: Prepend same entry to `AGENTS.md`**

Add at the top of the entries section (after the Rules block).

- [ ] **Step 3: Mark Phase 18 complete in `README.md` and `docs/workflows_and_plans.md`**

In both files, find the Phase 17 entry and add below it:
```markdown
- **Phase 18 ✅** — Living Wiki + Provider Expansion (provider registry + Anthropic, cross-source merge, --save, log format, image skeleton, --format table/marp)
```

- [ ] **Step 4: Commit docs**

```bash
git add CHANGELOG.md AGENTS.md README.md docs/workflows_and_plans.md
git commit -m "docs: Mark Phase 18 complete in changelog, AGENTS, README, and workflows"
```

- [ ] **Step 5: Final full check**

```bash
PYTHONPATH=. pytest tests/ -q && bash scripts/check.sh 2>&1 | tail -5
```

Expected: all green.

---

## Self-review notes

- All `_PROVIDERS` and `_PROVIDER_DEFAULTS` keys match in Task 1 (mock/openai/ollama/anthropic) ✅
- `merge_extraction(data, wiki_dir)` signature is consistent across Task 3, Task 4, and all test calls ✅
- `export_context(topic, ..., save=False, fmt="markdown")` signature matches all test calls ✅
- `_save_synthesis` always called with `base_output` (canonical markdown), never with the fmt-transformed output ✅
- `is_image_file` and `ingest_image_stub` are imported in tests from `scripts.ingest` ✅
- `append_log_image_skip` is defined in `scripts/wiki_merge.py` and used in `ingest.py` via import ✅
- cli.py uses `dest="output_format"` for `--format` to avoid collision with Python built-in `format` ✅
