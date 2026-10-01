#!/usr/bin/env python3
"""
Zurvan Agent Session Management
"""

import datetime
import re
from pathlib import Path

# The STORE root, not this file's location. Deriving it from `__file__` meant
# ZURVAN_ROOT could not redirect session writes, so `check.sh`'s agent-workflow
# smoke wrote `wiki/sessions/<date>-phase-7-smoke-test.md` into the live corpus
# on every run — six had accumulated, all indexed, all inside every retrieval
# baseline the project had measured.
from scripts.config import PROJECT_ROOT as ROOT
from scripts.safe_write import append_file_safely, write_file_safely

# Where this checkout's templates live. Distinct from ROOT, which is the STORE
# and may be redirected to a throwaway by ZURVAN_ROOT.
CODE_ROOT = Path(__file__).resolve().parents[1]
SESSIONS_DIR = ROOT / "wiki" / "sessions"
LOG_FILE = ROOT / "wiki" / "log.md"

def get_safe_filename(topic: str) -> str:
    safe = re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-')
    today = datetime.date.today().isoformat()
    return f"{today}-{safe}.md"

def get_timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _write(path: Path, content: str) -> None:
    if not write_file_safely(str(path), content):
        raise PermissionError(f"refused unsafe session write: {path}")


def _append(path: Path, content: str) -> None:
    if not append_file_safely(str(path), content):
        raise PermissionError(f"refused unsafe log append: {path}")


def session_start(topic: str) -> str:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    filename = get_safe_filename(topic)
    filepath = SESSIONS_DIR / filename
    
    template_path = CODE_ROOT / "scripts" / "templates" / "session_start.md"
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
        
    content = template.format(
        topic=topic,
        start_time=get_timestamp(),
        status="Open"
    )
    
    # Through the barrier (filed 2026-09-28): a raw open() here left drift on
    # every session start that the next managed write minted as external.
    _write(filepath, content)
    log_entry = f"\n### {get_timestamp()}\n**Session Started**: {topic}\n- **File**: `wiki/sessions/{filename}`\n"
    _append(LOG_FILE, log_entry)
        
    return str(filepath)

def session_close(topic: str, summary: str, checks: str) -> str:
    filename = get_safe_filename(topic)
    filepath = SESSIONS_DIR / filename
    
    if not filepath.exists():
        raise FileNotFoundError(f"Session file not found for topic '{topic}': {filepath}")
        
    template_path = CODE_ROOT / "scripts" / "templates" / "session_close.md"
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
    
    _write(filepath, existing + content)
    log_entry = f"\n### {get_timestamp()}\n**Session Closed**: {topic}\n- **Summary**: {summary}\n- **Checks Run**: `{checks}`\n"
    _append(LOG_FILE, log_entry)
        
    return str(filepath)
