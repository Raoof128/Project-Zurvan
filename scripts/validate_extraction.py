import json
import os

from scripts.evidence_admission import EvidenceRejected, admit_quote

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
            # Exact, non-blank inclusion: a quote the source does not contain
            # byte-for-byte is a hallucinated citation, and an empty quote is
            # no citation at all (audit 2026-10-22, F2).
            try:
                admit_quote(evidence.get("quote"), source_text)
            except EvidenceRejected as e:
                raise ValueError(f"Evidence admission failed: Claim '{cid}': {e}")
                
    return data

def is_safe_filename(filename: str) -> bool:
    if "../" in filename or filename.startswith("/"):
        return False
    return True
