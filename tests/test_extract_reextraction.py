"""F1 follow-up (c), 2026-11-07 review: re-extraction behaviour.

Roadmap F1 gate: "retry after interruption and intentional updates must either
preserve prior evidence or return an explicit conflict. A successful retry
should not create duplicates."  Four gaps remained after Package A:

1. same-hash re-extraction rewrote an existing claim page without comparing it;
2. open questions were appended again on every run;
3. an `ExtractionConflict` made the CLI exit 0;
4. the claim page heading used the sanitised id where the plan says raw.

Every test builds its own store under `tmp_path` (`ZURVAN_ROOT`); none touches
the live wiki.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from scripts.config import PROJECT_ROOT

QUOTE = "Zurvan turns raw sources into a persistent Markdown wiki..."


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    prompts = tmp_path / "scripts" / "prompts"
    prompts.mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "scripts" / "prompts" / "extract_source.md",
                prompts / "extract_source.md")
    (tmp_path / "source.md").write_text(f"{QUOTE}\nBody.\n")
    monkeypatch.delenv("ZURVAN_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(extract, rel="source.md"):
    """One extraction is one operation (as `scripts/extract.py __main__` opens it)."""
    from scripts import knowledge_revision as kr
    with kr.operation(writer="extract", eager=True):
        extract.extract_source(rel)


def _llm(claim_text="A claim.", open_questions=()):
    return json.dumps({
        "source_id": "x",
        "summary": {"short": "s", "detailed": "d"},
        "claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                    "text": claim_text, "tags": [],
                    "evidence": [{"quote": QUOTE, "location": "line 1"}]}],
        "concepts": [], "entities": [],
        "open_questions": [{"question": q, "reason": r} for q, r in open_questions],
        "possible_contradictions": [],
    })


def _claim_pages(ws):
    return sorted((ws / "wiki" / "claims").glob("*.md"))


def _claims(ws):
    from scripts import claim_ledger
    return claim_ledger.current(ws)


# 1. same-key re-extraction compares before it writes (Z2: through the ledger) --

def test_re_extracting_identical_content_skips_the_claim_write(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm())
    _run(extract)
    (page,) = _claim_pages(workspace)
    os.utime(page, ns=(1_000_000_000, 1_000_000_000))
    _run(extract)
    assert page.stat().st_mtime_ns == 1_000_000_000, "identical claim was rewritten"
    (state,) = _claims(workspace).values()
    assert state.rev == 1


def test_plan_omits_a_claim_already_in_the_ledger(workspace, monkeypatch):
    import hashlib
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm())
    _run(extract)
    sha = hashlib.sha256((workspace / "source.md").read_bytes()).hexdigest()
    assert extract.plan_claim_records(json.loads(_llm()), sha) == []


def test_same_key_with_different_text_is_a_conflict_and_preserves_the_claim(workspace, monkeypatch):
    import hashlib
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm("accepted evidence"))
    _run(extract)
    sha = hashlib.sha256((workspace / "source.md").read_bytes()).hexdigest()
    with pytest.raises(extract.ExtractionConflict, match="claim-1"):
        extract.plan_claim_records(json.loads(_llm("a different reading")), sha)
    (state,) = _claims(workspace).values()
    assert state.text == "accepted evidence"


def test_a_changed_re_extraction_writes_nothing_at_all(workspace, monkeypatch):
    """Plan-before-write: the conflicting run adds no JSON, summary or question
    either, and leaves the accepted claim and its page byte-identical."""
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm("first reading"))
    _run(extract)
    (page,) = _claim_pages(workspace)
    before = page.read_bytes()
    extraction = next((workspace / "data" / "extractions").glob("*.json"))
    extraction_before = extraction.read_bytes()
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        "second reading", open_questions=[("New?", "because")]))
    with pytest.raises(extract.ExtractionConflict):
        _run(extract)
    assert page.read_bytes() == before
    assert extraction.read_bytes() == extraction_before
    assert not (workspace / "wiki" / "open-questions.md").exists()
    (state,) = _claims(workspace).values()
    assert state.text == "first reading" and state.rev == 1


# 2. open questions are idempotent per (source_id, question) -----------------

def test_re_extracting_the_same_source_does_not_duplicate_open_questions(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        open_questions=[("Who owns it?", "unclear"), ("When?", "undated")]))
    _run(extract)
    _run(extract)
    text = (workspace / "wiki" / "open-questions.md").read_text()
    assert text.count("**Who owns it?**") == 1
    assert text.count("**When?**") == 1


def test_the_same_question_from_another_source_is_still_recorded(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        open_questions=[("Who owns it?", "unclear")]))
    _run(extract)
    (workspace / "other.md").write_text(f"{QUOTE}\nOther body.\n")
    _run(extract, "other.md")
    text = (workspace / "wiki" / "open-questions.md").read_text()
    assert text.count("**Who owns it?**") == 2


def test_a_question_repeated_within_one_response_is_written_once(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        open_questions=[("Who owns it?", "a"), ("Who owns it?", "b")]))
    _run(extract)
    assert (workspace / "wiki" / "open-questions.md").read_text().count("**Who owns it?**") == 1


# 3. a conflict is a non-zero exit -------------------------------------------

def _run_cli(ws, source="source.md"):
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "ZURVAN_ROOT": str(ws),
           "TOKENIZERS_PARALLELISM": "false"}
    env.pop("ZURVAN_LLM_PROVIDER", None)
    return subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "extract.py"),
                           "--source", source],
                          cwd=str(ws), env=env, capture_output=True, text=True)


def test_the_cli_exits_non_zero_on_a_conflict_and_writes_nothing(workspace):
    """The ledger already holds this source's `claim-dummy-001` with other text."""
    import hashlib
    from scripts import claim_ledger
    from scripts import knowledge_revision as kr
    sha = hashlib.sha256((workspace / "source.md").read_bytes()).hexdigest()
    with kr.operation(writer="test"):
        claim_ledger.add("other text", "source.md", QUOTE, confidence="high", actor="t",
                         method="model_extraction", reader="extract_text@1",
                         extraction_key=f"{sha}:claim-dummy-001")
    result = _run_cli(workspace)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "claim-dummy-001" in (result.stdout + result.stderr)
    (state,) = _claims(workspace).values()
    assert state.text == "other text"
    assert not (workspace / "data" / "extractions").exists()
    assert not (workspace / "wiki" / "summaries").exists()


def test_the_cli_still_exits_zero_on_a_clean_extraction(workspace):
    result = _run_cli(workspace)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_claim_pages(workspace)) == 1


# 4. the model's claim id never names anything durable (Z2) ------------------

def test_model_claim_id_is_only_part_of_the_extraction_key(workspace, monkeypatch):
    """Superseded the old 'heading is the raw claim id' rule: since Z2 the page is
    generated from the ledger and named by ledger id; the model id survives only
    inside `extraction_key`."""
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm())
    _run(extract)
    (page,) = _claim_pages(workspace)
    assert page.name.startswith("clm_") and "claim-1" not in page.name
    assert "claim-1" not in page.read_text()
