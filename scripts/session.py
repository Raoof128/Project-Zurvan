#!/usr/bin/env python3
"""
Zurvan Agent Session Management
"""

import datetime
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS_DIR = ROOT / "wiki" / "sessions"
LOG_FILE = ROOT / "wiki" / "log.md"

def get_safe_filename(topic: str) -> str:
    safe = re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-')
    today = datetime.date.today().isoformat()
    return f"{today}-{safe}.md"

def get_timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def session_start(topic: str) -> str:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(topic)
    filepath = SESSIONS_DIR / filename
    
    template_path = ROOT / "scripts" / "templates" / "session_start.md"
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
        
    content = template.format(
        topic=topic,
        start_time=get_timestamp(),
        status="Open"
    )
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
        
    log_entry = f"\n### {get_timestamp()}\n**Session Started**: {topic}\n- **File**: `wiki/sessions/{filename}`\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
        
    return str(filepath)

def session_close(topic: str, summary: str, checks: str) -> str:
    filename = get_safe_filename(topic)
    filepath = SESSIONS_DIR / filename
    
    if not filepath.exists():
        raise FileNotFoundError(f"Session file not found for topic '{topic}': {filepath}")
        
    template_path = ROOT / "scripts" / "templates" / "session_close.md"
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
        
    content = template.format(
        end_time=get_timestamp(),
        summary=summary,
        checks=checks
    )
    
    # Read existing content, replace "Status: Open" with "Status: Closed"
    with open(filepath, "r", encoding="utf-8") as f:
        existing = f.read()
    
    existing = existing.replace("**Status**: Open", "**Status**: Closed")
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(existing + content)
        
    log_entry = f"\n### {get_timestamp()}\n**Session Closed**: {topic}\n- **Summary**: {summary}\n- **Checks Run**: `{checks}`\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
        
    return str(filepath)
