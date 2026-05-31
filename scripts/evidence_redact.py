import re
import os

REDACTION_PATTERNS = [
    # Absolute paths (rudimentary matching for unix-style)
    (re.compile(r'(?<=[\s"\'])(/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+)(?=[\s"\']|$)'), "[REDACTED_PATH]"),
    # Windows paths (e.g. C:\Users\...)
    (re.compile(r'(?<=[\s"\'])([a-zA-Z]:\\[a-zA-Z0-9_.-]+(?:\\[a-zA-Z0-9_.-]+)+)(?=[\s"\']|$)'), "[REDACTED_PATH]"),
    # Home directory paths
    (re.compile(r'(?<=[\s"\'])(~/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+)(?=[\s"\']|$)'), "[REDACTED_PATH]"),
    # Emails
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b'), "[REDACTED_EMAIL]"),
    # Phone-like (very basic)
    (re.compile(r'\b(?:\+\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b'), "[REDACTED_PHONE]"),
    # Generic API Keys / Tokens (hex or base64ish long strings, simplistic)
    (re.compile(r'\b(?:[A-Za-z0-9]{32,})\b'), "[REDACTED_TOKEN]"),
    # Bearer tokens
    (re.compile(r'(?i)Bearer\s+[A-Za-z0-9\-\._~+\/]+=*'), "Bearer [REDACTED_TOKEN]"),
    # AWS Access Keys
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), "[REDACTED_AWS_KEY]")
]

def redact_text(text: str) -> str:
    if not text:
        return text
    
    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
        
    return redacted

def redact_item(item: dict) -> dict:
    redacted_item = item.copy()
    for key in ["title", "excerpt", "quote", "claim_text", "full_text", "reason"]:
        if key in redacted_item and isinstance(redacted_item[key], str):
            redacted_item[key] = redact_text(redacted_item[key])
            
    if "matched_terms" in redacted_item:
        if isinstance(redacted_item["matched_terms"], list):
            redacted_item["matched_terms"] = [redact_text(t) if isinstance(t, str) else t for t in redacted_item["matched_terms"]]
        elif isinstance(redacted_item["matched_terms"], dict):
            new_mt = {}
            for k, v in redacted_item["matched_terms"].items():
                if isinstance(v, list):
                    new_mt[k] = [redact_text(t) if isinstance(t, str) else t for t in v]
                else:
                    new_mt[k] = redact_text(str(v))
            redacted_item["matched_terms"] = new_mt
            
    return redacted_item

def redact_evidence_pack_items(items: list[dict]) -> list[dict]:
    return [redact_item(it) for it in items]
