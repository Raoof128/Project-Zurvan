"""Extraction schema contract.

`schemas.py` is the pydantic contract every LLM extraction is validated
against, so it is the boundary that stops a malformed or fabricated extraction
from reaching the wiki. It had no tests at all. The negative cases matter most:
a schema that accepts anything protects nothing.
"""

import pytest
from pydantic import ValidationError

from scripts.schemas import (
    Claim,
    Concept,
    Entity,
    Evidence,
    ExtractionSchema,
    OpenQuestion,
    PossibleContradiction,
    Summary,
)


def _evidence():
    return [Evidence(quote="a verbatim quote", location="p.1")]


def _claim(**overrides):
    payload = dict(
        claim_id="c1",
        text="Something asserted.",
        claim_type="fact",
        confidence="high",
        evidence=_evidence(),
        tags=["topic"],
    )
    payload.update(overrides)
    return Claim(**payload)


# ── Evidence ──────────────────────────────────────────────────────────────────

def test_evidence_location_is_optional():
    assert Evidence(quote="q").location is None


def test_evidence_requires_a_quote():
    """Evidence without a quote is exactly the fabricated-citation case."""
    with pytest.raises(ValidationError):
        Evidence(location="p.1")


# ── Claim ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "claim_type", ["fact", "argument", "definition", "decision", "hypothesis"]
)
def test_every_documented_claim_type_is_accepted(claim_type):
    assert _claim(claim_type=claim_type).claim_type == claim_type


@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
def test_every_documented_confidence_level_is_accepted(confidence):
    assert _claim(confidence=confidence).confidence == confidence


def test_an_unknown_claim_type_is_rejected():
    with pytest.raises(ValidationError):
        _claim(claim_type="speculation")


def test_an_unknown_confidence_level_is_rejected():
    with pytest.raises(ValidationError):
        _claim(confidence="certain")


def test_a_claim_may_carry_several_pieces_of_evidence():
    claim = _claim(evidence=[Evidence(quote="one"), Evidence(quote="two")])
    assert len(claim.evidence) == 2


def test_a_claim_missing_its_text_is_rejected():
    with pytest.raises(ValidationError):
        Claim(claim_id="c1", claim_type="fact", confidence="high", evidence=[], tags=[])


# ── Entity ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "entity_type",
    ["person", "organisation", "project", "tool", "paper", "dataset", "other"],
)
def test_every_documented_entity_type_is_accepted(entity_type):
    entity = Entity(name="n", entity_type=entity_type, description="d")
    assert entity.entity_type == entity_type


def test_an_unknown_entity_type_is_rejected():
    with pytest.raises(ValidationError):
        Entity(name="n", entity_type="spacecraft", description="d")


# ── the other leaf models ─────────────────────────────────────────────────────

def test_summary_requires_both_lengths():
    assert Summary(short="s", detailed="d").short == "s"
    with pytest.raises(ValidationError):
        Summary(short="s")


def test_concept_open_question_and_contradiction_round_trip():
    assert Concept(name="n", definition="d", evidence=_evidence()).name == "n"
    assert OpenQuestion(question="q?", reason="r").question == "q?"
    contradiction = PossibleContradiction(claim="a", conflicts_with="b", reason="r")
    assert contradiction.conflicts_with == "b"


# ── ExtractionSchema ──────────────────────────────────────────────────────────

def test_a_fully_populated_extraction_validates():
    extraction = ExtractionSchema(
        source_id="src-1",
        summary=Summary(short="s", detailed="d"),
        claims=[_claim()],
        concepts=[Concept(name="n", definition="d", evidence=_evidence())],
        entities=[Entity(name="e", entity_type="paper", description="d")],
        open_questions=[OpenQuestion(question="q?", reason="r")],
        possible_contradictions=[],
    )
    assert extraction.source_id == "src-1"
    assert extraction.claims[0].evidence[0].quote == "a verbatim quote"


def test_an_empty_extraction_is_valid_when_every_list_is_present():
    """Nothing extracted is a legitimate outcome; missing keys are not."""
    extraction = ExtractionSchema(
        source_id="src-empty",
        summary=Summary(short="", detailed=""),
        claims=[],
        concepts=[],
        entities=[],
        open_questions=[],
        possible_contradictions=[],
    )
    assert extraction.claims == []


def test_an_extraction_missing_a_required_section_is_rejected():
    with pytest.raises(ValidationError):
        ExtractionSchema(source_id="src-1", summary=Summary(short="s", detailed="d"))


def test_a_nested_bad_claim_fails_the_whole_extraction():
    """Validation must not stop at the top level."""
    with pytest.raises(ValidationError):
        ExtractionSchema(
            source_id="src-1",
            summary=Summary(short="s", detailed="d"),
            claims=[{"claim_id": "c1", "text": "t", "claim_type": "nonsense",
                     "confidence": "high", "evidence": [], "tags": []}],
            concepts=[],
            entities=[],
            open_questions=[],
            possible_contradictions=[],
        )
