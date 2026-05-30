import json
import os

def validate_extraction_json(json_str: str, source_text: str):
    """
    Validates the LLM JSON output.
    Returns parsed dictionary if valid, raises ValueError if not.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parses correctly failed: {e}")
        
    required_fields = ["source_id", "summary", "claims", "concepts", "entities", "open_questions", "possible_contradictions"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Required fields exist failed: Missing field '{field}'")
            
    claim_ids = set()
    for claim in data.get("claims", []):
        cid = claim.get("claim_id")
        if cid in claim_ids:
            raise ValueError(f"Claim IDs are unique failed: Duplicate claim_id '{cid}'")
        claim_ids.add(cid)
        
        evidence_list = claim.get("evidence", [])
        if not evidence_list:
            raise ValueError(f"Claims have evidence failed: Claim '{cid}' has no evidence.")
            
        for evidence in evidence_list:
            quote = evidence.get("quote", "")
            if quote and quote not in source_text:
                # Basic check, LLM might slightly alter quotes, but strict requirement for MVP:
                # To prevent hallucinated citations, it must exist in the source text.
                # If doing exact substring match is too brittle, one might use a fuzzy matcher.
                # MVP enforces exact string inclusion.
                raise ValueError(f"Evidence quote appears in source text failed: Quote not found in source text: '{quote}'")
                
    return data

def is_safe_filename(filename: str) -> bool:
    if "../" in filename or filename.startswith("/"):
        return False
    return True
