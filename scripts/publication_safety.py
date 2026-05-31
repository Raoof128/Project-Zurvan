import os
import re
from pathlib import Path

def get_publications_dir() -> Path:
    config_dir = Path(os.environ.get("ZURVAN_CONFIG_DIR", Path.home() / ".zurvan"))
    pubs_dir = config_dir / "publications"
    pubs_dir.mkdir(parents=True, exist_ok=True)
    return pubs_dir

def check_publication_safety(content: str, allow_emails: bool = False) -> list[str]:
    failures = []
    
    # Block absolute paths
    if re.search(r'(?i)(?:[a-z]:\\|/Users/|/home/|/etc/|/var/)', content):
        failures.append("Blocked: Leaked absolute path detected in content.")
        
    # Block emails unless allowed
    if not allow_emails:
        if re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content):
            failures.append("Blocked: Email address detected in content.")
            
    # Block token-like secrets
    if re.search(r'(?i)(?:api_key|token|secret|password)["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{16,}', content):
        failures.append("Blocked: Token-like secret detected in content.")
        
    # Block raw/ references
    if re.search(r'\braw/', content):
        failures.append("Blocked: 'raw/' reference detected in content.")
        
    return failures
