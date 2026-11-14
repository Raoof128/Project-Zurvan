"""F1 (audit 2026-10-22): extraction wrote wiki/claims/<model-chosen id>.md with
"w", so a second source returning the same claim_id replaced the first claim's
text, source and citation. Pages are now content-identified and a cross-owner
collision is an explicit conflict that writes nothing."""
import json
import shutil

import pytest

from scripts.config import PROJECT_ROOT

MOCK_QUOTE = "Zurvan turns raw sources into a persistent Markdown wiki..."


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    prompts = tmp_path / "scripts" / "prompts"
    prompts.mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "scripts" / "prompts" / "extract_source.md", prompts / "extract_source.md")
    monkeypatch.delenv("ZURVAN_LLM_PROVIDER", raising=False)
    # Z2: the claim ledger lives under ZURVAN_ROOT; without it, the REAL repo.
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(ws, rel, tail):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{MOCK_QUOTE}\n{tail}\n")
    return rel


def extract_source(rel):
    from scripts import knowledge_revision as kr
    from scripts.extract import extract_source as _extract
    with kr.operation(writer="extract", eager=True):
        _extract(rel)


def _claims(ws):
    from scripts import claim_ledger
    return claim_ledger.current(ws)


def test_two_sources_returning_the_same_claim_id_keep_both_claims(workspace):
    extract_source(_write(workspace, "a.md", "A."))
    extract_source(_write(workspace, "b.md", "B."))
    claims = _claims(workspace)
    assert len(claims) == 2, claims
    # Z2: identity comes from the ledger (clm_...), never from the model's id.
    assert all(c.startswith("clm_") for c in claims)
    assert {s.evidence[0]["source"] for s in claims.values()} == {"a.md", "b.md"}


def test_same_basename_different_dirs_do_not_collide(workspace):
    extract_source(_write(workspace, "x/source.md", "X."))
    extract_source(_write(workspace, "y/source.md", "Y."))
    assert len(list((workspace / "data" / "extractions").glob("source-*.json"))) == 2
    assert len(list((workspace / "wiki" / "summaries").glob("source-*_summary.md"))) == 2
    assert {s.evidence[0]["source"] for s in _claims(workspace).values()} == {
        "x/source.md", "y/source.md"}


def test_retry_on_identical_bytes_creates_no_duplicates(workspace):
    rel = _write(workspace, "source.md", "Same.")
    extract_source(rel)
    extract_source(rel)
    assert len(_claims(workspace)) == 1
    assert len(list((workspace / "wiki" / "claims").glob("*.md"))) == 1
    assert len(list((workspace / "data" / "extractions").glob("*.json"))) == 1


def test_source_identity_is_stem_plus_content_hash():
    import hashlib
    from scripts.extract import source_identity
    sid, h = source_identity("/any/dir/My Paper.pdf", "body text")
    assert h == hashlib.sha256(b"body text").hexdigest()
    assert sid == f"My_Paper-{h[:12]}"


def _data(text="new"):
    return {"claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                        "text": text, "evidence": [{"quote": MOCK_QUOTE}], "tags": []}]}


def test_same_key_with_different_text_is_an_explicit_conflict_and_writes_nothing(workspace):
    """Z2 replaces the cross-owner page collision: the same (file bytes, model id)
    key already in the ledger with different text is a conflict before any write."""
    from scripts import claim_ledger
    from scripts import knowledge_revision as kr
    from scripts.extract import ExtractionConflict, plan_claim_records
    rel = _write(workspace, "src.md", "S.")
    with kr.operation(writer="test"):
        claim_ledger.add("old", rel, MOCK_QUOTE, confidence="high", actor="t",
                         method="model_extraction", reader="extract_text@1",
                         extraction_key="cccc:claim-1")
    with pytest.raises(ExtractionConflict, match="claim-1"):
        plan_claim_records(_data("new"), "cccc")
    (state,) = _claims(workspace).values()
    assert state.text == "old"


def test_same_key_with_identical_content_is_a_no_op(workspace):
    from scripts import claim_ledger
    from scripts import knowledge_revision as kr
    from scripts.extract import plan_claim_records
    rel = _write(workspace, "src.md", "S.")
    [rec] = plan_claim_records(_data("new"), "cccc")
    assert rec["key"] == "cccc:claim-1"
    with kr.operation(writer="test"):
        claim_ledger.add("new", rel, MOCK_QUOTE, confidence="high", actor="t",
                         method="model_extraction", reader="extract_text@1",
                         extraction_key=rec["key"])
    assert plan_claim_records(_data("new"), "cccc") == []


def test_sanitisation_collisions_are_rejected_by_the_validator():
    from scripts.validate_extraction import validate_extraction_json
    base = {"source_id": "s", "summary": {}, "concepts": [], "entities": [],
            "open_questions": [], "possible_contradictions": []}
    claim = lambda cid: {"claim_id": cid, "claim_type": "fact", "confidence": "high",
                         "text": "t", "evidence": [{"quote": "any"}]}
    # `claim_1` and `claim 1` both sanitise to `claim_1` (space -> underscore).
    from scripts.filename_utils import sanitize_filename
    assert sanitize_filename("claim 1") == sanitize_filename("claim_1")
    with pytest.raises(ValueError, match="unique"):
        validate_extraction_json(json.dumps({**base, "claims": [claim("claim_1"), claim("claim 1")]}), "any")
