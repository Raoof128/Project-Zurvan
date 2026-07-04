import os
import uuid
import re
from datetime import datetime
from typing import List, Optional
from scripts.safe_write import write_file_safely, append_file_safely, escape_yaml_string
from scripts.config import PROJECT_ROOT

def append_to_log(action: str, details: str):
    log_path = str(PROJECT_ROOT / "wiki" / "log.md")
    timestamp = datetime.now().isoformat()
    entry = f"\n- **{timestamp}**: {action} - {details}"
    append_file_safely(log_path, entry)

def _make_note_slug(name: str) -> str:
    """Lowercase hyphenated slug for note/decision filenames (different from wiki-page slugs)."""
    name = name.lower().replace(" ", "-")
    return re.sub(r'[^a-z0-9\-]', '', name)[:50]


def _unique_path(directory: str, slug: str, prefix: str = "") -> str:
    """Return a non-colliding '<dir>/<prefix><slug>.md' path. Two titles that
    slugify identically (e.g. 'Use SQLite!' and 'Use SQLite?') must not silently
    overwrite each other, so a numeric suffix is added on collision."""
    base = str(PROJECT_ROOT / directory / f"{prefix}{slug}.md")
    if not os.path.exists(base):
        return base
    counter = 2
    while True:
        candidate = str(PROJECT_ROOT / directory / f"{prefix}{slug}-{counter}.md")
        if not os.path.exists(candidate):
            return candidate
        counter += 1

def add_decision(title: str, reason: str, status: str, tags: List[str]):
    filepath = _unique_path("wiki/decisions", _make_note_slug(title))

    tags_yaml = "\n".join(f"  - {escape_yaml_string(t)}" for t in tags)
    content = f"""---
title: {escape_yaml_string(title)}
type: decision
status: {escape_yaml_string(status)}
tags:
{tags_yaml}
---

# {title}

## Reason
{reason}

## Status
{status}
"""
    if write_file_safely(filepath, content):
        append_to_log("Decision Added", title)
        print(f"Created {filepath}")
        return True
    else:
        print(f"Failed to securely write to {filepath}")
        return False

def add_note(title: str, body: str, tags: List[str]):
    filepath = _unique_path("wiki", _make_note_slug(title), prefix="note-")

    tags_yaml = "\n".join(f"  - {escape_yaml_string(t)}" for t in tags)
    content = f"""---
title: {escape_yaml_string(title)}
type: note
tags:
{tags_yaml}
---

# {title}

{body}
"""
    if write_file_safely(filepath, content):
        append_to_log("Note Added", title)
        print(f"Created {filepath}")
        return True
    else:
        print(f"Failed to securely write to {filepath}")
        return False

def add_claim(text: str, source: str, evidence: str, confidence: str, tags: List[str]):
    # Validate evidence quote exists in source file. Relative sources resolve
    # against the repo root so this works from any CWD (e.g. the MCP server).
    source_read_path = source if os.path.isabs(source) else str(PROJECT_ROOT / source)
    if not os.path.exists(source_read_path):
        print(f"Error: Source file {source} does not exist.")
        return False

    with open(source_read_path, 'r', encoding='utf-8') as f:
        source_text = f.read()
        
    if evidence not in source_text:
        print("Error: Evidence quote not found verbatim in the source file.")
        return False
        
    claim_id = str(uuid.uuid4())[:8]
    filename = f"claim-{claim_id}.md"
    filepath = str(PROJECT_ROOT / "wiki" / "claims" / filename)
    
    tags_yaml = "\n".join(f"  - {escape_yaml_string(t)}" for t in tags)
    content = f"""---
type: claim
confidence: {escape_yaml_string(confidence)}
source: {escape_yaml_string(source)}
tags:
{tags_yaml}
---

# Claim
{text}

## Evidence
> {evidence}

**Source**: [[{source}]]
"""
    if write_file_safely(filepath, content):
        append_to_log("Claim Added", f"Claim {claim_id} from {source}")
        print(f"Created {filepath}")
        return True
    return False

def add_question(question: str, reason: str, tags: List[str]):
    q_id = str(uuid.uuid4())[:8]
    questions_path = str(PROJECT_ROOT / "wiki" / "open-questions.md")
    
    tags_str = ", ".join(tags)
    entry = f"\n## Q: {question}\n- **ID**: {q_id}\n- **Reason**: {reason}\n- **Tags**: {tags_str}\n"
    
    if append_file_safely(questions_path, entry):
        append_to_log("Question Added", question)
        print(f"Appended question to {questions_path}")
        return True
    else:
        print("Failed to securely write question.")
        return False
