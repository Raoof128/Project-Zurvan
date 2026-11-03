"""F2 (audit 2026-10-22): empty quotations passed evidence admission on every
path. One admission function now gates them all; these tests pin each path."""
import json

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


# --- the three admission paths -------------------------------------------

def _op(tmp_path, monkeypatch):
    """An operation over a throwaway store (tests/test_memory.py pattern)."""
    from scripts import knowledge_revision as kr
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(exist_ok=True)
    return kr.operation(writer="test", root=tmp_path)


def _isolated_source(tmp_path, monkeypatch):
    import scripts.memory as memory
    import scripts.safe_write as safe_write
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "src.md").write_text("line one\n")
    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)
    return memory


def test_add_claim_rejects_empty_evidence_and_writes_nothing(tmp_path, monkeypatch):
    memory = _isolated_source(tmp_path, monkeypatch)
    with _op(tmp_path, monkeypatch):
        assert memory.add_claim("c", "wiki/src.md", "", "high", []) is False
        assert memory.add_claim("c", "wiki/src.md", "   ", "high", []) is False
    claims = tmp_path / "wiki" / "claims"
    assert not claims.exists() or list(claims.glob("*.md")) == []


def test_mcp_claim_add_rejects_empty_evidence(tmp_path, monkeypatch):
    _isolated_source(tmp_path, monkeypatch)
    # Writes enabled: in read-only mode every write is refused before admission
    # runs, and this test would pass for the wrong reason.
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    from scripts.mcp_tools import tool_zurvan_claim_add
    with _op(tmp_path, monkeypatch):
        out = tool_zurvan_claim_add("c", "wiki/src.md", "", "high", [])
        assert out.startswith("Error"), out
        assert "read-only" not in out, out
        claims = tmp_path / "wiki" / "claims"
        assert not claims.exists() or list(claims.glob("*.md")) == []
        # Control: the same path admits a verbatim quote.
        ok = tool_zurvan_claim_add("c", "wiki/src.md", "line one", "high", [])
        assert not ok.startswith("Error"), ok
    assert len(list((tmp_path / "wiki" / "claims").glob("claim-*.md"))) == 1


def test_extraction_validator_rejects_empty_quote():
    from scripts.validate_extraction import validate_extraction_json
    data = {
        "source_id": "s", "summary": {}, "concepts": [], "entities": [],
        "open_questions": [], "possible_contradictions": [],
        "claims": [{"claim_id": "claim-1", "claim_type": "fact", "confidence": "high",
                    "text": "t", "evidence": [{"quote": ""}]}],
    }
    with pytest.raises(ValueError, match="empty"):
        validate_extraction_json(json.dumps(data), "any source text")
