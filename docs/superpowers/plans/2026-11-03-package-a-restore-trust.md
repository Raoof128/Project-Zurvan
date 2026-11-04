# Package A — Restore Trust (F1–F6, embedding identity, status index) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five reproduced correctness defects (F1–F5), bind every Rust response to the generation it actually served (F6), make embedding identity typed, and publish a generated status index — each defect failing before its fix and passing after, with no live data used as a fixture.

**Architecture:** Python control/write layer (`scripts/`) gets one shared evidence-admission function, content-identified extraction output, an explicit store handle for federated search, typed embedding failure/identity, and a single versioned fusion policy shared by scorer and trace. The Rust read engine (`rust/zurvan-mcp`) returns a served-generation envelope and derives the stamp from it. Nothing changes any ranking: F3 changes *which* index is searched for a foreign project; F4/F5 change failure and trace behaviour only.

**Tech Stack:** Python 3 (pytest), Rust (cargo test, clippy `-D warnings`), SQLite. Repo quality gate: `PYTHONPATH=. python -m pytest -q`, `python scripts/public_repo_guard.py`, `git diff --check`.

**Spec:** `docs/superpowers/specs/2026-10-22-zurvan-audit-and-upgrade-roadmap.md` §2 (graph reader, status index), §3 (F1–F6), §12 row A. Audited commit `e65d4bb`; `main` is one tests-only commit ahead (`63f3e33`), so the findings describe today's code.

## Global Constraints

- `raw/` is immutable; never execute source content. Never fabricate a citation.
- Frozen artifacts untouched: `eval/provenance_real_*.jsonl`, `data/traces/` committed traces, the 86%/79% metrics.
- No ranking or indexing change in this package. If a task's diff touches `KEYWORD_WEIGHT`, `MIN_HYBRID_SCORE`, FTS query construction, cosine mapping, or chunking, stop: that needs a before/after `eval_search` run in `CHANGELOG.md` and is out of scope here.
- Production knowledge state is never a test fixture: every test builds under `tmp_path` and sets `ZURVAN_ROOT` / `ZURVAN_SEARCH_DB` / `monkeypatch.setattr(memory, "PROJECT_ROOT", ...)` as `tests/test_memory.py::_op` does.
- Canonical index: one write-mode `sqlite3.connect` in the codebase (the build path). Every new reader opens `mode=ro`.
- No `print()` to stdout from any code reachable by an MCP tool; diagnostics go to `sys.stderr`.
- No `git push`. Local commits only, ending with the project's `Co-Authored-By` trailer.
- Change protocol on completion: dated `Raouf:` entry (Australia/Sydney) in both `AGENTS.md` and `CHANGELOG.md`, older `AGENTS.md` entries rotated verbatim into `docs/agents-history.md` (slice to the next `###` heading, never by date prefix).
- Rust: `cargo test` and `cargo clippy -- -D warnings` clean in `rust/zurvan-mcp`; the live parity harness (`scripts/mcp_parity.py`) strips lines starting `[index rev `, so the stamp's *content* may change but its first characters may not.
- Work on a branch (`package-a`) in an isolated worktree per `superpowers:using-git-worktrees`; merge to `main` only after the whole-branch review.

## Review Focus

Inputs the spec implies but no finding reproduced; each is pinned to a test in the task named.

1. A quote that is in the source only after Unicode/whitespace normalisation (NBSP, `fi` ligature from PDF extraction) — must be **rejected** (byte-exact contract), with the rejection naming "verbatim". → Task 1.
2. Two extraction sources with the same basename in different directories and *different* content — must produce two distinct claim/summary/extraction files, never one. → Task 2.
3. Federated search with `ZURVAN_SEARCH_DB` set in the environment: the override applies to the home store only; a foreign project's root must still resolve to `<root>/data/search.sqlite`. → Task 3.
4. An index built before `embedding_identity` existed (no such key in `index_meta`): the query path must still work, comparing only provider/model/dimension, and must not raise "mismatch" for the missing fields. → Task 4.
5. A promote landing between retrieval and the graph open in `zurvan_context`: the bundle must either report one coherent pair of generations or say the graph generation differs — never silently stamp one and serve the other. → Task 7.

---

### Task 1: Shared evidence admission (F2)

**Files:**
- Create: `scripts/evidence_admission.py`
- Modify: `scripts/memory.py:259-273` (`add_claim`)
- Modify: `scripts/validate_extraction.py:30-37`
- Test: `tests/test_evidence_admission.py`

**Interfaces:**
- Produces: `class EvidenceRejected(ValueError)`; `def admit_quote(quote: str | None, source_text: str) -> str` — returns `quote` unchanged when it is non-blank (`quote.strip() != ""`) and `quote in source_text` byte-for-byte; raises `EvidenceRejected("evidence quote is empty")` for `None`, `""`, or whitespace-only; raises `EvidenceRejected("evidence quote not found verbatim in source")` otherwise. No normalisation of either argument.

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/test_evidence_admission.py
import pytest
from scripts.evidence_admission import EvidenceRejected, admit_quote

SRC = "Zurvan turns raw sources into a persistent Markdown wiki.\nSecond line.\n"

@pytest.mark.parametrize("quote", [None, "", "   ", "\n\t", " \u00a0 "])
def test_blank_quotes_are_rejected(quote):
    with pytest.raises(EvidenceRejected, match="empty"):
        admit_quote(quote, SRC)

def test_quote_must_be_verbatim_not_normalised():
    # NBSP vs space, and a PDF ligature: in the source only after normalisation.
    with pytest.raises(EvidenceRejected, match="verbatim"):
        admit_quote("persistent\u00a0Markdown", SRC)
    with pytest.raises(EvidenceRejected, match="verbatim"):
        admit_quote("\ufb01rst", "first line")

def test_verbatim_quote_is_returned_unchanged():
    assert admit_quote("persistent Markdown wiki", SRC) == "persistent Markdown wiki"
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_evidence_admission.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.evidence_admission`

- [ ] **Step 3: Implement `scripts/evidence_admission.py`** with the interface above. Module docstring: one paragraph stating that "quote exists in source" is a membership check, not entailment (spec F2).

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_evidence_admission.py -q`
Expected: 7 passed

- [ ] **Step 5: Write the failing path tests (library, MCP, extraction)**

Append to `tests/test_evidence_admission.py`, using `tests/test_memory.py::_op`'s isolation pattern (copy the helper locally; set `memory.PROJECT_ROOT` and `safe_write.get_project_root`):

```python
def test_add_claim_rejects_empty_evidence_and_writes_nothing(tmp_path, monkeypatch):
    import scripts.memory as memory, scripts.safe_write as safe_write
    (tmp_path / "wiki").mkdir(); (tmp_path / "wiki" / "src.md").write_text("line one\n")
    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)
    with _op(tmp_path, monkeypatch):
        assert memory.add_claim("c", "wiki/src.md", "", "high", []) is False
        assert memory.add_claim("c", "wiki/src.md", "   ", "high", []) is False
    claims = tmp_path / "wiki" / "claims"
    assert not claims.exists() or list(claims.glob("*.md")) == []

def test_mcp_claim_add_rejects_empty_evidence(tmp_path, monkeypatch):
    # Same isolation as above, then:
    from scripts.mcp_tools import tool_zurvan_claim_add
    with _op(tmp_path, monkeypatch):
        out = tool_zurvan_claim_add("c", "wiki/src.md", "", "high", [])
    assert out.startswith("Error")

def test_extraction_validator_rejects_empty_quote():
    import json
    from scripts.validate_extraction import validate_extraction_json
    data = {"source_id": "s", "summary": {}, "claims": [{"claim_id": "claim-1", "claim_type": "fact",
            "confidence": "high", "text": "t", "evidence": [{"quote": ""}]}],
            "concepts": [], "entities": [], "open_questions": [], "possible_contradictions": []}
    with pytest.raises(ValueError, match="empty"):
        validate_extraction_json(json.dumps(data), "any source text")
```

- [ ] **Step 6: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_evidence_admission.py -q`
Expected: the three path tests FAIL (claim file created / "Created" text / no ValueError)

- [ ] **Step 7: Route the three paths through `admit_quote`**

- `scripts/memory.py:271-273`: replace the `if evidence not in source_text` check with `try: admit_quote(evidence, source_text) except EvidenceRejected as e: print(f"Error: {e}"); return False`.
- `scripts/validate_extraction.py:30-37`: replace the `if quote and quote not in source_text` block with `try: admit_quote(evidence.get("quote"), source_text) except EvidenceRejected as e: raise ValueError(f"Evidence admission failed: Claim '{cid}': {e}")`.
- `tool_zurvan_claim_add` already returns an `Error` string when `add_claim` returns `False`; confirm, change nothing there.

- [ ] **Step 8: Run the file and the neighbours**

Run: `PYTHONPATH=. python -m pytest tests/test_evidence_admission.py tests/test_memory.py tests/test_extract.py tests/test_mcp_operation_boundary.py tests/test_fail_closed_writes.py -q`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add scripts/evidence_admission.py scripts/memory.py scripts/validate_extraction.py tests/test_evidence_admission.py
git commit -m "fix(F2): one evidence admission layer; blank quotes rejected on every path"
```

---

### Task 2: Extraction output is content-identified and conflict-checked (F1)

**Files:**
- Modify: `scripts/extract.py:20-21, 77-108`
- Modify: `scripts/validate_extraction.py:19-24`
- Test: `tests/test_extract.py`, new `tests/test_extract_identity.py`

**Interfaces:**
- Consumes: `admit_quote` (Task 1), `scripts.filename_utils.sanitize_filename`.
- Produces in `scripts/extract.py`:
  - `def source_identity(filepath: str, source_text: str) -> tuple[str, str]` → `(source_id, content_hash)` where `content_hash = sha256(source_text.encode("utf-8")).hexdigest()` and `source_id = f"{sanitize_filename(stem)}-{content_hash[:12]}"`.
  - `def claim_page_name(source_id: str, claim_id: str) -> str` → `f"{source_id}--{sanitize_filename(claim_id)}.md"`.
  - `class ExtractionConflict(RuntimeError)`.
  - `def plan_claim_writes(data: dict, source_id: str, content_hash: str, claims_dir: str) -> list[tuple[str, str]]` → `[(path, content), ...]`; raises `ExtractionConflict` naming the page when a target page exists whose frontmatter `source_hash:` differs from `content_hash`. A target whose `source_hash` equals `content_hash` is a re-extraction of identical bytes and may be rewritten. Nothing is written by this function.
- Claim frontmatter gains `source_hash: <content_hash>` and `source_file: <basename>`; `data/extractions/<source_id>.json` and `wiki/summaries/<source_id>_summary.md` use the new `source_id`.
- **Planner's call:** `wiki_merge.merge_extraction(data)` also receives the content-identified `source_id`, so concept pages read `Evidence from <stem>-<hash12>` and two revisions of one file are two `sources:` entries. That is the spec's content-vs-occurrence distinction made visible; `tests/test_wiki_merge.py` builds its own extractions and is unaffected.
- Validator: uniqueness is checked on `sanitize_filename(claim_id)`, so `claim-1` and `claim 1` collide and are rejected with the existing "Claim IDs are unique failed" message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract_identity.py  (reuse tests/test_extract.py::extraction_workspace, parametrised to write two sources)
def test_two_sources_returning_the_same_claim_id_keep_both_claims(extraction_workspace):
    # Spec F1 reproduction: both mock extractions return claim-dummy-001.
    from scripts.extract import extract_source
    (extraction_workspace / "a.md").write_text("Zurvan turns raw sources into a persistent Markdown wiki...\nA.\n")
    (extraction_workspace / "b.md").write_text("Zurvan turns raw sources into a persistent Markdown wiki...\nB.\n")
    extract_source("a.md"); extract_source("b.md")
    claims = sorted(p.name for p in (extraction_workspace / "wiki" / "claims").glob("*.md"))
    assert len(claims) == 2 and all("claim-dummy-001" in n for n in claims)

def test_same_basename_different_dirs_do_not_collide(extraction_workspace):
    (extraction_workspace / "x").mkdir(); (extraction_workspace / "y").mkdir()
    (extraction_workspace / "x" / "source.md").write_text("Zurvan turns raw sources into a persistent Markdown wiki...\nX.\n")
    (extraction_workspace / "y" / "source.md").write_text("Zurvan turns raw sources into a persistent Markdown wiki...\nY.\n")
    extract_source("x/source.md"); extract_source("y/source.md")
    assert len(list((extraction_workspace / "data" / "extractions").glob("source-*.json"))) == 2
    assert len(list((extraction_workspace / "wiki" / "summaries").glob("source-*_summary.md"))) == 2

def test_retry_on_identical_bytes_creates_no_duplicates(extraction_workspace):
    extract_source("source.md"); extract_source("source.md")
    assert len(list((extraction_workspace / "wiki" / "claims").glob("*.md"))) == 1

def test_cross_owner_collision_is_an_explicit_conflict_and_writes_nothing(extraction_workspace, tmp_path):
    from scripts.extract import plan_claim_writes, ExtractionConflict
    claims_dir = extraction_workspace / "wiki" / "claims"; claims_dir.mkdir(parents=True)
    page = claims_dir / "src-aaaaaaaaaaaa--claim-1.md"
    page.write_text("---\ntype: claim\nsource_hash: bbbbbbbb\n---\nold\n")
    data = {"claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high", "text": "new",
                        "evidence": [{"quote": "q"}], "tags": []}]}
    with pytest.raises(ExtractionConflict, match="claim-1"):
        plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc", str(claims_dir))
    assert page.read_text().endswith("old\n")

def test_sanitisation_collisions_are_rejected_by_the_validator():
    import json
    from scripts.validate_extraction import validate_extraction_json
    base = {"source_id": "s", "summary": {}, "concepts": [], "entities": [], "open_questions": [], "possible_contradictions": []}
    claim = lambda cid: {"claim_id": cid, "claim_type": "fact", "confidence": "high", "text": "t", "evidence": [{"quote": "any"}]}
    with pytest.raises(ValueError, match="unique"):
        validate_extraction_json(json.dumps({**base, "claims": [claim("claim-1"), claim("claim 1")]}), "any")
```

Update `tests/test_extract.py::test_extract_writes_real_newlines_in_claim_tags` to glob `wiki/claims/*--claim-dummy-001.md` and `test_extract_creates_output_dirs_on_fresh_tree` to glob `data/extractions/source-*.json` and `wiki/summaries/source-*_summary.md`.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_extract_identity.py tests/test_extract.py -q`
Expected: FAIL (one claim file; ImportError for `plan_claim_writes`; no ValueError for `claim 1`)

- [ ] **Step 3: Implement** `source_identity`, `claim_page_name`, `ExtractionConflict`, `plan_claim_writes` in `scripts/extract.py`; in `extract_source` compute `(source_id, content_hash)` after `extract_text`, call `plan_claim_writes` *before* writing the JSON, summary, or any claim page, and only then write everything. Validator: compare `sanitize_filename(cid)` in the uniqueness set. Keep the `# {cid}` heading as the raw claim id; the `Source:` wikilink keeps `[[sources/{basename}]]`.

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_extract_identity.py tests/test_extract.py tests/test_validate_extraction*.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add scripts/extract.py scripts/validate_extraction.py tests/test_extract.py tests/test_extract_identity.py
git commit -m "fix(F1): extraction pages are content-identified; cross-owner collisions are explicit conflicts"
```

---

### Task 3: Federated hybrid search resolves an explicit store handle (F3)

**Files:**
- Create: `scripts/store_handle.py`
- Modify: `scripts/context_export.py:19-30`
- Modify: `scripts/cross_project_search.py:41-56`
- Modify: `scripts/hybrid_search.py:40-44` (accept the handle's path; no scoring change)
- Test: `tests/test_store_handle.py`, `tests/test_cross_project_search.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class StoreHandle: project: str | None; root: Path; search_db: Path; graph_db: Path`.
  `def resolve_store(root: str | Path | None = None, project: str | None = None) -> StoreHandle`:
  - `root is None` → home store: `root=PROJECT_ROOT`, `search_db=get_search_db_path()` (so `ZURVAN_SEARCH_DB` applies), `graph_db=PROJECT_ROOT/data/graph.sqlite`, `project=project`.
  - `root` given → `search_db=Path(root)/"data"/"search.sqlite"`, `graph_db=Path(root)/"data"/"graph.sqlite"`; the environment override is **not** consulted (its meaning is "the home store lives elsewhere").
- `_search_internal(query, hybrid, limit, root=None, timings=None, store: StoreHandle | None = None)`: when `store` is None it is `resolve_store(root)`; hybrid mode calls `search_hybrid(query, limit, db_path=str(store.search_db), timings=timings)`. Every returned result dict gains `"index_path": str(store.search_db)` (hybrid) or `"index_path": None` (keyword) and `"project": store.project`.
- `cross_project_search` passes `store=resolve_store(p["path"], project=p["name"])` and takes `"project"` from the result, not from the loop variable.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store_handle.py
def test_foreign_root_ignores_the_home_override(tmp_path, monkeypatch):
    from scripts.store_handle import resolve_store
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "elsewhere.sqlite"))
    h = resolve_store(tmp_path / "other", project="other")
    assert h.search_db == tmp_path / "other" / "data" / "search.sqlite" and h.project == "other"

def test_home_store_honours_the_override(tmp_path, monkeypatch):
    from scripts.store_handle import resolve_store
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "elsewhere.sqlite"))
    assert resolve_store().search_db == tmp_path / "elsewhere.sqlite"
```

```python
# tests/test_cross_project_search.py — add:
def _indexed_project(tmp_path, name, note_text, monkeypatch):
    """A project with its OWN <root>/data/search.sqlite, built under the mock provider."""
    from scripts.rebuild_search_index import rebuild_search_index
    root = tmp_path / name; (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "note.md").write_text(f"# Note\n{note_text}\n")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock"); monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    rebuild_search_index(db_path=str(root / "data" / "search.sqlite"), root=root)
    return {"name": name, "path": str(root), "has_search": True, "has_graph": False}

@patch("scripts.cross_project_search.get_federated_projects")
def test_hybrid_federation_searches_the_selected_project_index_only(mock_get_proj, tmp_path, monkeypatch):
    home = _indexed_project(tmp_path, "home", "SENTINEL_HOME alpha", monkeypatch)
    other = _indexed_project(tmp_path, "other", "SENTINEL_OTHER alpha", monkeypatch)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "home" / "data" / "search.sqlite"))
    mock_get_proj.return_value = [other]
    res = cross_project_search("alpha", hybrid=True, limit=10, projects=["other"])
    assert res["results"], res["warnings"]
    assert {r["project"] for r in res["results"]} == {"other"}
    assert all("SENTINEL_HOME" not in r["snippet"] for r in res["results"])
    assert all(r["index_path"].endswith("other/data/search.sqlite") for r in res["results"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_store_handle.py tests/test_cross_project_search.py -q`
Expected: FAIL — ImportError; federation returns `SENTINEL_HOME` labelled `other`

- [ ] **Step 3: Implement** `scripts/store_handle.py` and wire the three call sites as specified. `search_hybrid` already accepts `db_path`; do not touch its scoring.

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_store_handle.py tests/test_cross_project_search.py tests/test_cross_project_context.py tests/test_hybrid_search.py tests/test_mcp_tools*.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add scripts/store_handle.py scripts/context_export.py scripts/cross_project_search.py scripts/hybrid_search.py tests/test_store_handle.py tests/test_cross_project_search.py
git commit -m "fix(F3): federated hybrid search binds results to the selected project's store handle"
```

---

### Task 4: Typed embedding failure and identity (F4)

**Files:**
- Modify: `scripts/embed.py` (whole file is 63 lines)
- Modify: `scripts/hybrid_search.py:97-101`
- Modify: `scripts/rebuild_search_index.py:219-224, 263` (stamp `embedding_identity`)
- Modify: `scripts/context_export.py:19-30` and `scripts/mcp_tools.py:166-175` (labelled fallback)
- Test: `tests/test_embed.py`, `tests/test_hybrid_search.py`, new `tests/test_embedding_identity.py`

**Interfaces:**
- Produces in `scripts/embed.py`:
  - `class EmbeddingUnavailable(RuntimeError)` — a *requested* real provider could not load or encode. Message includes the provider, model and the underlying exception text. **No fallback to mock.**
  - `class EmbeddingSpaceMismatch(RuntimeError)` — the vector returned is not in the space the caller required.
  - `@dataclass(frozen=True) class EmbeddingIdentity: provider: str; model: str; dimension: int; max_seq_length: int | None = None; pooling: str | None = None; normalize: bool | None = None`, with `to_json() -> str` / `from_json(s) -> EmbeddingIdentity`.
  - `def identity_for(provider: str, model: str) -> EmbeddingIdentity` — for `sentence_transformers` read `max_seq_length`, the pooling module's mode (`pooling_mode_mean_tokens` → `"mean"`, etc.) and whether a `Normalize` module is present from the loaded model; for `mock` return `EmbeddingIdentity("mock", "deterministic_hash", 384)`.
  - `get_embedding(text, provider=None, model=None) -> dict` keeps its keys and adds `"identity": EmbeddingIdentity`. Behaviour: provider unset → `"mock"` as today (the canonical rebuild guard covers that case); provider `"sentence_transformers"` and load/encode fails → raise `EmbeddingUnavailable`; the warning `print` is removed.
  - `def require_space(result: dict, expected: EmbeddingIdentity) -> None` — raises `EmbeddingSpaceMismatch` unless `(provider, model, dimension)` match; compares `max_seq_length`/`pooling`/`normalize` only where `expected` has them (older indexes stamp none).
- `rebuild_search_index`: `_stamp_index(..., {**stamp, "embedding_identity": probe["identity"].to_json()})`.
- `hybrid_search.search_hybrid`: builds `expected` from `index_meta.embedding_identity` when present else from `_index_embedding_config` + stored dimension; calls `require_space` on the query embedding; `EmbeddingUnavailable`/`EmbeddingSpaceMismatch` propagate (no silent mock).
- `context_export._search_internal(hybrid=True)`: propagates the exception. `mcp_tools.tool_zurvan_search` and `tool_zurvan_orient`: catch those two, run the keyword path, and prefix the output with `Semantic search unavailable: {e} — keyword results only.` (the Rust server's exact notice text, `rust/zurvan-mcp/src/search.rs:478`). `scripts/cli.py:1051` and `:1066` currently catch only `ValueError`; widen both to `except (ValueError, EmbeddingUnavailable, EmbeddingSpaceMismatch) as exc:` and print the two new ones to `sys.stderr` (the `--json` payload is on stdout), then `sys.exit(1)`.
- `tests/test_embed.py::test_get_embedding_handles_missing_sentence_transformers` asserts the fallback this task removes. Rewrite it: with `sys.modules["sentence_transformers"] = None` and the cache cleared, `get_embedding("test", provider="sentence_transformers")` raises `EmbeddingUnavailable`; with the env provider unset, `get_embedding("test")["provider"] == "mock"` (the documented default stays).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_embedding_identity.py
def test_requested_real_provider_failure_raises_not_mock(monkeypatch):
    import scripts.embed as embed
    monkeypatch.setitem(embed._MODEL_CACHE, "all-MiniLM-L6-v2", None)
    import sys; monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # import fails
    with pytest.raises(embed.EmbeddingUnavailable, match="sentence_transformers"):
        embed.get_embedding("q", provider="sentence_transformers", model="all-MiniLM-L6-v2")

def test_require_space_rejects_wrong_dimension_and_accepts_missing_optional_fields():
    from scripts.embed import EmbeddingIdentity, EmbeddingSpaceMismatch, require_space, get_embedding
    r = get_embedding("q", provider="mock")
    require_space(r, EmbeddingIdentity("mock", "deterministic_hash", 384))          # optional fields absent: ok
    with pytest.raises(EmbeddingSpaceMismatch):
        require_space(r, EmbeddingIdentity("sentence_transformers", "all-MiniLM-L6-v2", 384))

def _minilm_labelled_mock_index(tmp_path, monkeypatch):
    """A tmp index built with mock vectors, then RELABELLED as the MiniLM space
    (fixture only — never a live store), with the real model made unloadable."""
    import sqlite3, sys
    from scripts.embed import EmbeddingIdentity
    from scripts.rebuild_search_index import rebuild_search_index
    corpus = tmp_path / "corpus"; (corpus / "wiki").mkdir(parents=True)
    (corpus / "wiki" / "citation.md").write_text("# Citation Rules\n\nEvery citation needs a verbatim quote.\n")
    (corpus / "wiki" / "graph.md").write_text("# Graph\n\nNodes and edges from wikilinks.\n")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock"); monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    db = str(tmp_path / "search.sqlite")
    rebuild_search_index(db_path=db, root=corpus)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE embeddings SET provider='sentence_transformers', model='all-MiniLM-L6-v2'")
    conn.execute("UPDATE index_meta SET value=? WHERE key='embedding_identity'",
                 (EmbeddingIdentity("sentence_transformers", "all-MiniLM-L6-v2", 384).to_json(),))
    conn.commit(); conn.close()
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    import scripts.embed as embed; embed._MODEL_CACHE.clear()
    return db

def test_hybrid_query_refuses_a_mock_vector_against_a_minilm_index(tmp_path, monkeypatch):
    db = _minilm_labelled_mock_index(tmp_path, monkeypatch)
    with pytest.raises(embed.EmbeddingUnavailable):
        search_hybrid("citation", limit=3, db_path=db)

def test_mcp_search_labels_the_keyword_fallback(tmp_path, monkeypatch):
    db = _minilm_labelled_mock_index(tmp_path, monkeypatch)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", db)
    monkeypatch.setattr("scripts.store_handle.PROJECT_ROOT", tmp_path / "corpus")   # keyword fallback scans the fixture wiki (Task 3's resolver)
    out = tool_zurvan_search("citation", hybrid=True, limit=3)
    assert out.startswith("Semantic search unavailable:") and "keyword results only" in out
    assert "citation.md" in out

def test_rebuild_stamps_embedding_identity(tmp_index):          # extend tests/test_hybrid_search.py
    from scripts.rebuild_search_index import read_index_meta
    from scripts.embed import EmbeddingIdentity
    ident = EmbeddingIdentity.from_json(read_index_meta(tmp_index)["embedding_identity"])
    assert (ident.provider, ident.model, ident.dimension) == ("mock", "deterministic_hash", 384)
```

Also update any test in `tests/test_embed.py` that asserts the mock fallback *after* a sentence-transformers failure: it now asserts `EmbeddingUnavailable`.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_embedding_identity.py tests/test_embed.py -q`
Expected: FAIL — AttributeError on the new names; mock vector returned where an exception is expected

- [ ] **Step 3: Implement** per the interfaces. Keep `generate_mock_embedding` unchanged.

- [ ] **Step 4: Run to verify they pass, plus the rebuild and MCP neighbours**

Run: `PYTHONPATH=. python -m pytest tests/test_embedding_identity.py tests/test_embed.py tests/test_hybrid_search.py tests/test_index_integrity.py tests/test_index_health*.py tests/test_mcp_tools*.py tests/test_rebuild*.py -q`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add scripts/embed.py scripts/hybrid_search.py scripts/rebuild_search_index.py scripts/context_export.py scripts/mcp_tools.py scripts/cli.py tests/test_embedding_identity.py tests/test_embed.py tests/test_hybrid_search.py
git commit -m "fix(F4): embedding failure and space mismatch are typed; identity stamped in the index; fallback labelled"
```

---

### Task 5: One versioned fusion policy for scorer and trace (F5)

**Files:**
- Create: `scripts/fusion_policy.py`
- Modify: `scripts/hybrid_search.py:245-262` (import the constants from the policy; formula unchanged)
- Modify: `scripts/context_export.py:187-189, 234-252`
- Modify: `docs/evaluation/provenance.md` (documents the `retrieval.fusion` event) — add the historical-annotation paragraph
- Test: `tests/test_fusion_policy.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class FusionPolicy: policy_id: str; keyword_weight: float; min_score: float`; `def score(self, keyword: float, semantic: float) -> float` = `keyword_weight * keyword + (1 - keyword_weight) * semantic`; `def as_payload(self) -> dict` = `{"fusion": "weighted_sum", "policy_id": ..., "weights": {"fts": keyword_weight, "embedding": 1 - keyword_weight}, "min_score": ...}`.
  `CURRENT = FusionPolicy("weighted_sum_v1_kw0.2", 0.2, 0.2)`.
- `hybrid_search.KEYWORD_WEIGHT = CURRENT.keyword_weight`, `MIN_HYBRID_SCORE = CURRENT.min_score` (names kept; the measurement comment stays). `_fuse` and `_score_semantic_top_k` call `CURRENT.score(...)`.
- `context_export._fusion_payload` returns `{**CURRENT.as_payload(), "mode": "hybrid", "ranked": [...]}`; `_FUSION_WEIGHTS` is deleted.
- Documentation: traces written before this commit declare `weights {"fts": 0.6, "embedding": 0.4}` while the scorer used 0.2/0.8; historical traces are **not** rewritten; a reader must take the policy from `policy_id` when present and otherwise treat the `weights` field as unreliable.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fusion_policy.py
def test_trace_weights_are_the_scorer_weights():
    from scripts.fusion_policy import CURRENT
    from scripts import hybrid_search
    from scripts.context_export import _fusion_payload
    p = _fusion_payload([])
    assert p["weights"] == {"fts": hybrid_search.KEYWORD_WEIGHT, "embedding": 1 - hybrid_search.KEYWORD_WEIGHT}
    assert p["policy_id"] == CURRENT.policy_id == "weighted_sum_v1_kw0.2"

def test_fused_score_reconstructs_from_recorded_components():
    from scripts.fusion_policy import CURRENT
    from scripts.context_export import _fusion_payload
    rows = [{"source_path": "a", "text": "t", "keyword_score": 1.0, "semantic_score": 0.5, "hybrid_score": CURRENT.score(1.0, 0.5)}]
    rec = _fusion_payload(rows)["ranked"][0]
    assert rec["fused_score"] == pytest.approx(0.6)                              # spec F5 example
    w = _fusion_payload(rows)["weights"]
    assert w["fts"] * rec["fts_score"] + w["embedding"] * rec["embedding_score"] == pytest.approx(rec["fused_score"])

def test_hybrid_search_uses_the_policy():
    from scripts.fusion_policy import CURRENT
    from scripts.hybrid_search import _fuse
    out = _fuse([{"chunk_id": "c", "keyword_score": 1.0, "semantic_score": 0.5}])
    assert out[0]["hybrid_score"] == pytest.approx(CURRENT.score(1.0, 0.5)) == pytest.approx(0.6)
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_fusion_policy.py -q`
Expected: FAIL — ImportError; weights 0.6/0.4 ≠ 0.2/0.8; replay gives 0.8

- [ ] **Step 3: Implement** per the interfaces; add the documentation paragraph.

- [ ] **Step 4: Verify no ranking change and the trace suite**

Run: `PYTHONPATH=. python -m pytest tests/test_fusion_policy.py tests/test_hybrid_search.py tests/test_trace_*.py tests/test_z1a_hardening_traces.py -q`
Expected: all passed. Then `git diff scripts/hybrid_search.py` must show only the import and the two `CURRENT.score(...)` substitutions — no numeric change.

- [ ] **Step 5: Commit**

```bash
git add scripts/fusion_policy.py scripts/hybrid_search.py scripts/context_export.py tests/test_fusion_policy.py docs/
git commit -m "fix(F5): scorer and trace share one versioned fusion policy; historical trace weights annotated"
```

---

### Task 6: Secondary graph reader opens read-only (spec §2 gap)

**Files:**
- Modify: `scripts/graph_context.py:9-16`
- Test: `tests/test_graph_context.py`

**Interfaces:**
- `expand_graph_context(seeds, depth=1, db_path=DB_PATH)` opens `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` and returns `[]` (printing the existing hint to **stderr**) when the open raises `sqlite3.OperationalError`; the `os.path.exists` pre-check is removed.

- [ ] **Step 1: Write the failing test**

```python
def test_expand_graph_context_never_creates_a_missing_store(tmp_path, capsys):
    from scripts.graph_context import expand_graph_context
    db = tmp_path / "graph.sqlite"
    assert expand_graph_context(["x"], 1, db_path=str(db)) == []
    assert not db.exists()
    out = capsys.readouterr()
    assert "Graph index missing" in out.err and out.out == ""

def test_expand_graph_context_opens_read_only(tmp_path, monkeypatch):
    import sqlite3
    import scripts.graph_context as gc
    db = tmp_path / "graph.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE nodes(node_id TEXT, path TEXT, slug TEXT, node_type TEXT, title TEXT)")
    c.execute("CREATE TABLE edges(from_node_id TEXT, to_node_id TEXT, edge_type TEXT)")
    c.commit(); c.close()
    seen = []
    real = sqlite3.connect
    monkeypatch.setattr(gc.sqlite3, "connect", lambda target, *a, **k: seen.append((target, k)) or real(target, *a, **k))
    gc.expand_graph_context(["nothing"], 1, db_path=str(db))
    assert seen and seen[0][0].startswith("file:") and "mode=ro" in seen[0][0] and seen[0][1].get("uri") is True
```

- [ ] **Step 2: Run to verify it fails** — `PYTHONPATH=. python -m pytest tests/test_graph_context.py -q` → FAIL (message on stdout; existence check path).

- [ ] **Step 3: Implement.** - [ ] **Step 4: Run to verify it passes.** - [ ] **Step 5: Commit** `fix: graph_context opens the graph store read-only`.

---

### Task 7: Rust response envelope bound to the served generation (F6)

**Files:**
- Modify: `rust/zurvan-mcp/src/search.rs:368-481` (`hybrid_once`, `hybrid_or_unavailable`, `search`)
- Modify: `rust/zurvan-mcp/src/status.rs:149-172` (`stamp`)
- Modify: `rust/zurvan-mcp/src/server.rs:118-167, 195-215` (`orient_tool`, `search_tool`, context arm)
- Modify: `rust/zurvan-mcp/src/context.rs:37-73` (`bundle`)
- Modify: `rust/zurvan-mcp/src/store.rs` (add `open_graph_at`)
- Test: in-module `#[cfg(test)]` in `search.rs`, `status.rs`, `context.rs`; `rust/zurvan-mcp/tests/protocol.rs` for the stamp prefix

**Interfaces:**
- `search.rs`: `pub enum Served { Index(Arc<IndexMeta>), LiveFiles }`; `pub struct SearchResult { pub hits: Vec<Hit>, pub notice: Option<String>, pub served: Served }`.
  `SearchState::search(...) -> Result<SearchResult, String>` (was `(Vec<Hit>, Option<String>)`); `hybrid_once` returns the `meta` it opened the pool with, so `served` is the generation hydrated from, by construction. Keyword mode is `Served::LiveFiles`.
  Test hook: `#[cfg(test)] pub(crate) after_hydrate: Mutex<Option<Box<dyn Fn() + Send>>>` on `SearchState`, invoked once between `hydrate` and return.
- `status.rs`: `pub fn stamp_for(cfg: &Config, served: &Served, cache: &MetaCache) -> String` — for `Served::Index(m)` renders `[index rev {m.built_from_revision} built {m.built_at} · ledger rev N · K writes since · <tail>]` **from `m`**, never from `cache.index()`. `<tail>` is the existing freshness text when `cache.index(cfg)` still has `m.generation`, and the literal `superseded: a newer index was promoted during this call` otherwise. For `LiveFiles` it renders `[live files · ledger rev N]`. The old `stamp(cfg, cache)` stays only for `zurvan_status`.
- `store.rs`: `pub fn open_graph_at(cfg: &Config) -> Result<(Connection, Generation), String>` — stat, open, stat again, `GENERATION_CHANGED` on mismatch (mirror of `open_at`).
- `context.rs`: `bundle(...) -> Result<(String, Option<String>, Served, Option<Generation>), String>`; the graph is opened with `open_graph_at` and its generation returned; the server's context arm appends ` · graph gen {ino}` to the stamp when present. Orient's `## Top pages` header becomes `## Top pages (live files, ledger rev N)`.

- [ ] **Step 1: Write the failing deterministic-barrier test in `search.rs`**

```rust
#[test]
fn the_stamp_names_the_generation_that_was_hydrated_not_the_one_promoted_after() {
    let (_d, cfg) = mock_store();                       // built_from_revision = "1"
    let st = SearchState::new(cfg.clone());
    let cfg2 = cfg.clone();
    *st.after_hydrate.lock().unwrap() = Some(Box::new(move || {
        // promote a new index (built_from_revision = "2") at the same path
        rebuild_fixture_index(&cfg2, "2");
    }));
    let r = st.search("ledger", true, 3, &|_, _| {}).unwrap();
    let Served::Index(m) = &r.served else { panic!("hybrid serves an index") };
    assert_eq!(m.built_from_revision, Some(1));
    let s = crate::status::stamp_for(&cfg, &r.served, &MetaCache::default());
    assert!(s.starts_with("[index rev 1 "), "{s}");
    assert!(!s.contains("index rev 2"));
}
```

`rebuild_fixture_index` writes a new `search_db` to a temp file and `std::fs::rename`s it over `cfg.search_db` (a new inode). Second test in `context.rs`: barrier between retrieval and the graph open promotes the graph store; the bundle either returns the graph generation it actually opened (and the stamp names it) or `GENERATION_CHANGED` retried — assert the returned `Option<Generation>` equals `generation(&cfg.graph_db)` *after* the barrier, and that the index `served` still says rev 1.

- [ ] **Step 2: Run to verify compile failure**

Run: `cargo test -p zurvan-mcp the_stamp_names_the_generation 2>&1 | tail -20` (in `rust/zurvan-mcp`)
Expected: compile error — `Served`, `SearchResult`, `after_hydrate`, `stamp_for` undefined

- [ ] **Step 3: Implement** per the interfaces; update every caller of `search()` (`server.rs` search/orient/context, `context.rs`, and any test using the tuple). Keep `format_results`/`format_results_raw` unchanged.

- [ ] **Step 4: Run the crate gate**

Run: `cargo test && cargo clippy --all-targets -- -D warnings` (in `rust/zurvan-mcp`)
Expected: all passed, clippy clean. The existing `hybrid_never_mixes_generations_under_promotion` must still pass (0 mixed).

- [ ] **Step 5: Live parity check (read-only, no index rebuild)**

Run (after `cargo build --release`): `PYTHONPATH=. python scripts/mcp_parity.py --only search_hybrid,search_keyword,context,orient,status --protocol 2026-07-28` and again with `--protocol 2025-11-25` (the harness's group names; `search` alone is rejected). Default A is the Python server, default B the freshly built `target/release/zurvan-mcp`.
Expected: 0 FAIL; the stamp line is stripped by `STRIP_PREFIXES` so only content parity is compared. Record the exact PASS/EXPECTED_DIVERGENCE counts for the change log.

- [ ] **Step 6: Commit**

```bash
git add rust/zurvan-mcp/src/{search.rs,status.rs,server.rs,context.rs,store.rs}
git commit -m "fix(F6): rust responses carry the served generation; stamp derived from it; graph open pinned"
```

---

### Task 8: Generated status index (spec §2)

**Files:**
- Create: `docs/status/receipts.json`
- Create: `scripts/status_index.py`
- Create: `docs/status/index.md` (generated; tracked)
- Test: `tests/test_status_index.py`

**Interfaces:**
- `receipts.json` schema: `[{ "item": str, "status": "implemented" | "tested_here" | "operationally_closed" | "promoted", "evidence": str (a repo-relative path or `CHANGELOG.md#<date>` anchor), "date": "YYYY-MM-DD", "note": str }]`. Seed it with the §2 table rows from the spec (Rust MCP → `implemented`/`tested_here`; Z1b → `implemented`, note "NOT CLOSED"; hybrid p50/p95 → `tested_here`; retired 50-item instrument → `tested_here`, note "cannot authorise promotion"; F1–F6 → `tested_here` with each task's test file as evidence).
- `python scripts/status_index.py [--check]` renders `docs/status/index.md` grouped by status, newest first; `--check` exits 1 when the rendered text differs from the file (so the quality gate can catch drift). Rejects any `evidence` path that does not exist and any unknown `status`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_status_index.py
import json, pytest
from scripts.status_index import render, check

ROWS = [{"item": "Rust MCP", "status": "implemented", "evidence": "CHANGELOG.md", "date": "2026-10-20", "note": ""},
        {"item": "F2 admission", "status": "tested_here", "evidence": "tests/test_evidence_admission.py", "date": "2026-11-03", "note": ""},
        {"item": "Z1b", "status": "implemented", "evidence": "CHANGELOG.md", "date": "2026-10-06", "note": "NOT CLOSED"}]

def test_render_groups_by_status_in_fixed_order(tmp_path):
    text = render(ROWS, repo_root=tmp_path_with(ROWS, tmp_path))
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    assert heads == ["## Promoted", "## Operationally closed", "## Tested here", "## Implemented"]
    assert text.index("Rust MCP") < text.index("Z1b")          # newest first within a group

@pytest.mark.parametrize("bad", [{**ROWS[0], "status": "done"}, {**ROWS[0], "evidence": "nope.md"}])
def test_bad_rows_are_rejected(bad, tmp_path):
    with pytest.raises(ValueError):
        render([bad], repo_root=tmp_path_with([ROWS[0]], tmp_path))

def test_check_detects_drift(tmp_path):
    root = tmp_path_with(ROWS, tmp_path)
    (root / "docs" / "status" / "index.md").write_text(render(ROWS, repo_root=root))
    assert check(repo_root=root) == 0
    (root / "docs" / "status" / "index.md").write_text("stale\n")
    assert check(repo_root=root) == 1
```

`tmp_path_with(rows, tmp_path)` writes `docs/status/receipts.json` and touches every `evidence` path under `tmp_path`, returning it. `render(rows, repo_root) -> str` and `check(repo_root) -> int` are the module's two public functions; `main()` wires `--check`.
- [ ] **Step 2: Run to verify fail.** - [ ] **Step 3: Implement.** - [ ] **Step 4: Run `PYTHONPATH=. python scripts/status_index.py && python scripts/status_index.py --check`** → exit 0; commit the rendered page.
- [ ] **Step 5: Commit** `docs: generated status index from versioned receipts`.

---

### Task 9: Quality gate, change log, closeout

**Files:**
- Modify: `AGENTS.md`, `CHANGELOG.md`, `docs/agents-history.md`
- Modify: `docs/superpowers/specs/2026-10-22-zurvan-audit-and-upgrade-roadmap.md` — no edits to the audit text; add nothing. Status lives in `docs/status/index.md`.

- [ ] **Step 1: Full Python suite** — `PYTHONPATH=. python -m pytest -q` → 0 failed apart from the one known live-operator test `test_t17_live_gate2_*` (report it as failed, never as passed).
- [ ] **Step 2: Rust gate** — `cargo test && cargo clippy --all-targets -- -D warnings` in `rust/zurvan-mcp` → clean.
- [ ] **Step 3: Guards** — `python scripts/public_repo_guard.py` and `git diff --check` → clean. `python scripts/status_index.py --check` → 0.
- [ ] **Step 4: Frozen golds unchanged** — `python scripts/eval_provenance.py --gold eval/provenance_real_gold.jsonl` → 86% recall / 100% completeness / 0% raw leak.
- [ ] **Step 5: Change-log entries** — one `### 2026-11-DD (Australia/Sydney) — Package A: F1–F6 …` entry in both `AGENTS.md` and `CHANGELOG.md` (scope, per-finding RED→GREEN test names, parity counts from Task 7 step 5, files, verification numbers, follow-ups: "Package B — Z1b closure — next"). Rotate the oldest `AGENTS.md` entry verbatim into `docs/agents-history.md`; verify entry counts.
- [ ] **Step 6: Record the decision in Zurvan** — `zurvan decision add --title "Package A restores trust before any promotion" --reason "<two sentences: five reproduced defects fixed test-first; no ranking change; promotion authority still waits on Z1b (B)>" --status accepted --tags zurvan audit-2026-10-22 --project zurvan`, then `grep '^project:' wiki/decisions/<slug>.md` → `project: "zurvan"`.
- [ ] **Step 7: Commit** `docs: package A closeout — change logs, decision`. No push.
