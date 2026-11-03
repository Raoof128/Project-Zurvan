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
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(ws, rel, tail):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{MOCK_QUOTE}\n{tail}\n")
    return rel


def test_two_sources_returning_the_same_claim_id_keep_both_claims(workspace):
    from scripts.extract import extract_source
    extract_source(_write(workspace, "a.md", "A."))
    extract_source(_write(workspace, "b.md", "B."))
    claims = sorted(p.name for p in (workspace / "wiki" / "claims").glob("*.md"))
    assert len(claims) == 2, claims
    assert all(n.endswith("--claim-dummy-001.md") for n in claims), claims
    bodies = [(workspace / "wiki" / "claims" / n).read_text() for n in claims]
    assert any("source_file: a.md" in b for b in bodies) and any("source_file: b.md" in b for b in bodies)


def test_same_basename_different_dirs_do_not_collide(workspace):
    from scripts.extract import extract_source
    extract_source(_write(workspace, "x/source.md", "X."))
    extract_source(_write(workspace, "y/source.md", "Y."))
    assert len(list((workspace / "data" / "extractions").glob("source-*.json"))) == 2
    assert len(list((workspace / "wiki" / "summaries").glob("source-*_summary.md"))) == 2
    assert len(list((workspace / "wiki" / "claims").glob("source-*--claim-dummy-001.md"))) == 2


def test_retry_on_identical_bytes_creates_no_duplicates(workspace):
    from scripts.extract import extract_source
    rel = _write(workspace, "source.md", "Same.")
    extract_source(rel)
    extract_source(rel)
    assert len(list((workspace / "wiki" / "claims").glob("*.md"))) == 1
    assert len(list((workspace / "data" / "extractions").glob("*.json"))) == 1


def test_source_identity_is_stem_plus_content_hash():
    import hashlib
    from scripts.extract import source_identity
    sid, h = source_identity("/any/dir/My Paper.pdf", "body text")
    assert h == hashlib.sha256(b"body text").hexdigest()
    assert sid == f"My_Paper-{h[:12]}"


def test_cross_owner_collision_is_an_explicit_conflict_and_writes_nothing(workspace):
    from scripts.extract import ExtractionConflict, plan_claim_writes
    claims_dir = workspace / "wiki" / "claims"
    claims_dir.mkdir(parents=True)
    page = claims_dir / "src-aaaaaaaaaaaa--claim-1.md"
    page.write_text("---\ntype: claim\nsource_hash: bbbbbbbb\n---\nold\n")
    data = {"claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                        "text": "new", "evidence": [{"quote": "q"}], "tags": []}]}
    with pytest.raises(ExtractionConflict, match="claim-1"):
        plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc", str(claims_dir), "src.md")
    assert page.read_text().endswith("old\n")


def test_same_owner_may_rewrite_its_own_page(workspace):
    from scripts.extract import plan_claim_writes
    claims_dir = workspace / "wiki" / "claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "src-aaaaaaaaaaaa--claim-1.md").write_text("---\ntype: claim\nsource_hash: cccccccc\n---\nold\n")
    data = {"claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                        "text": "new", "evidence": [{"quote": "q"}], "tags": []}]}
    plan = plan_claim_writes(data, "src-aaaaaaaaaaaa", "cccccccc", str(claims_dir), "src.md")
    assert [p for p, _ in plan] == [str(claims_dir / "src-aaaaaaaaaaaa--claim-1.md")]
    assert "source_hash: cccccccc" in plan[0][1]


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
