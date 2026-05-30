from typing import List, Literal, Optional
from pydantic import BaseModel, Field

class Summary(BaseModel):
    short: str
    detailed: str

class Evidence(BaseModel):
    quote: str
    location: Optional[str] = None

class Claim(BaseModel):
    claim_id: str
    text: str
    claim_type: Literal["fact", "argument", "definition", "decision", "hypothesis"]
    confidence: Literal["low", "medium", "high"]
    evidence: List[Evidence]
    tags: List[str]

class Concept(BaseModel):
    name: str
    definition: str
    evidence: List[Evidence]

class Entity(BaseModel):
    name: str
    entity_type: Literal["person", "organisation", "project", "tool", "paper", "dataset", "other"]
    description: str

class OpenQuestion(BaseModel):
    question: str
    reason: str

class PossibleContradiction(BaseModel):
    claim: str
    conflicts_with: str
    reason: str

class ExtractionSchema(BaseModel):
    source_id: str
    summary: Summary
    claims: List[Claim]
    concepts: List[Concept]
    entities: List[Entity]
    open_questions: List[OpenQuestion]
    possible_contradictions: List[PossibleContradiction]
