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


# 1. same-hash re-extraction compares before it writes -----------------------

def test_re_extracting_identical_content_skips_the_claim_write(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm())
    extract.extract_source("source.md")
    (page,) = _claim_pages(workspace)
    os.utime(page, ns=(1_000_000_000, 1_000_000_000))
    extract.extract_source("source.md")
    assert page.stat().st_mtime_ns == 1_000_000_000, "identical body was rewritten"


def test_plan_omits_a_page_whose_body_is_already_on_disk(workspace):
    from scripts.extract import plan_claim_writes
    claims_dir = workspace / "wiki" / "claims"
    claims_dir.mkdir(parents=True)
    data = {"claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                        "text": "t", "evidence": [{"quote": "q"}], "tags": []}]}
    [(path, body)] = plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc", str(claims_dir), "src.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    assert plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc", str(claims_dir), "src.md") == []


def test_same_hash_with_different_text_is_a_conflict_and_preserves_the_page(workspace):
    from scripts.extract import ExtractionConflict, plan_claim_writes
    claims_dir = workspace / "wiki" / "claims"
    claims_dir.mkdir(parents=True)
    page = claims_dir / "src-aaaaaaaaaaaa--claim-1.md"
    page.write_text("---\ntype: claim\nsource_hash: cccccccc\n---\naccepted evidence\n")
    data = {"claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                        "text": "a different reading", "evidence": [{"quote": "q"}], "tags": []}]}
    with pytest.raises(ExtractionConflict, match="claim-1"):
        plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc", str(claims_dir), "src.md")
    assert page.read_text().endswith("accepted evidence\n")


def test_a_changed_re_extraction_writes_nothing_at_all(workspace, monkeypatch):
    """Plan-before-write: the conflicting run adds no JSON, summary or question
    either, and leaves the accepted page byte-identical."""
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm("first reading"))
    extract.extract_source("source.md")
    (page,) = _claim_pages(workspace)
    before = page.read_bytes()
    extraction = next((workspace / "data" / "extractions").glob("*.json"))
    extraction_before = extraction.read_bytes()
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        "second reading", open_questions=[("New?", "because")]))
    with pytest.raises(extract.ExtractionConflict):
        extract.extract_source("source.md")
    assert page.read_bytes() == before
    assert extraction.read_bytes() == extraction_before
    assert not (workspace / "wiki" / "open-questions.md").exists()


# 2. open questions are idempotent per (source_id, question) -----------------

def test_re_extracting_the_same_source_does_not_duplicate_open_questions(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        open_questions=[("Who owns it?", "unclear"), ("When?", "undated")]))
    extract.extract_source("source.md")
    extract.extract_source("source.md")
    text = (workspace / "wiki" / "open-questions.md").read_text()
    assert text.count("**Who owns it?**") == 1
    assert text.count("**When?**") == 1


def test_the_same_question_from_another_source_is_still_recorded(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        open_questions=[("Who owns it?", "unclear")]))
    extract.extract_source("source.md")
    (workspace / "other.md").write_text(f"{QUOTE}\nOther body.\n")
    extract.extract_source("other.md")
    text = (workspace / "wiki" / "open-questions.md").read_text()
    assert text.count("**Who owns it?**") == 2


def test_a_question_repeated_within_one_response_is_written_once(workspace, monkeypatch):
    from scripts import extract
    monkeypatch.setattr(extract, "run_llm", lambda *a, **k: _llm(
        open_questions=[("Who owns it?", "a"), ("Who owns it?", "b")]))
    extract.extract_source("source.md")
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
    from scripts.extract import source_identity
    sid, _ = source_identity("source.md", (workspace / "source.md").read_text())
    claims_dir = workspace / "wiki" / "claims"
    claims_dir.mkdir(parents=True)
    page = claims_dir / f"{sid}--claim-dummy-001.md"
    page.write_text("---\ntype: claim\nsource_hash: someoneelse\n---\nother source\n")
    result = _run_cli(workspace)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "claim-dummy-001" in (result.stdout + result.stderr)
    assert page.read_text().endswith("other source\n")
    assert not (workspace / "data" / "extractions").exists()
    assert not (workspace / "wiki" / "summaries").exists()


def test_the_cli_still_exits_zero_on_a_clean_extraction(workspace):
    result = _run_cli(workspace)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_claim_pages(workspace)) == 1


# 4. the heading is the raw claim id -----------------------------------------

def test_heading_is_the_raw_claim_id_while_filename_and_frontmatter_stay_sanitised(workspace):
    from scripts.extract import plan_claim_writes
    data = {"claims": [{"claim_id": "claim 1", "claim_type": "fact", "confidence": "high",
                        "text": "t", "evidence": [{"quote": "q"}], "tags": []}]}
    [(path, body)] = plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc",
                                       str(workspace / "wiki" / "claims"), "src.md")
    assert os.path.basename(path) == "src-aaaaaaaaaaaa--claim_1.md"
    assert "\nclaim_id: claim_1\n" in body
    assert "\n# claim 1\n" in body
