import pytest
import json
from scripts.validate_extraction import validate_extraction_json, is_safe_filename

def test_validate_json_missing_fields():
    bad_json = '{"source_id": "test"}'
    with pytest.raises(ValueError, match="Required fields exist failed"):
        validate_extraction_json(bad_json, "some text")

def test_validate_json_valid():
    valid_json = '''{
        "source_id": "test",
        "summary": {"short": "test", "detailed": "test"},
        "claims": [{"claim_id": "c1", "text": "foo", "claim_type": "fact", "confidence": "high", "evidence": [{"quote": "hello"}], "tags": []}],
        "concepts": [],
        "entities": [],
        "open_questions": [],
        "possible_contradictions": []
    }'''
    # "hello" is in the source text
    data = validate_extraction_json(valid_json, "source text says hello there")
    assert data["source_id"] == "test"
    assert len(data["claims"]) == 1

def test_validate_json_hallucinated_quote():
    valid_json = '''{
        "source_id": "test",
        "summary": {"short": "test", "detailed": "test"},
        "claims": [{"claim_id": "c1", "text": "foo", "claim_type": "fact", "confidence": "high", "evidence": [{"quote": "i made this up"}], "tags": []}],
        "concepts": [],
        "entities": [],
        "open_questions": [],
        "possible_contradictions": []
    }'''
    with pytest.raises(ValueError, match="Quote not found in source text"):
        validate_extraction_json(valid_json, "source text says hello there")

def test_validate_json_missing_evidence():
    valid_json = '''{
        "source_id": "test",
        "summary": {"short": "test", "detailed": "test"},
        "claims": [{"claim_id": "c1", "text": "foo", "claim_type": "fact", "confidence": "high", "evidence": [], "tags": []}],
        "concepts": [],
        "entities": [],
        "open_questions": [],
        "possible_contradictions": []
    }'''
    with pytest.raises(ValueError, match="has no evidence"):
        validate_extraction_json(valid_json, "source text says hello there")

def test_is_safe_filename():
    assert is_safe_filename("test.md") == True
    assert is_safe_filename("../test.md") == False
    assert is_safe_filename("/etc/passwd") == False
