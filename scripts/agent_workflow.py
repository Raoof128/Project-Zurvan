#!/usr/bin/env python3
"""
Zurvan Agent Workflow Management
"""

import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "wiki" / "log.md"
OPEN_QUESTIONS_FILE = ROOT / "wiki" / "open-questions.md"

def get_timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_recent_logs(lines: int = 20) -> str:
    if not LOG_FILE.exists():
        return "No recent logs."
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        content = f.readlines()
    if not content:
        return "No recent logs."
    return "".join(content[-lines:]).strip()

def get_open_questions() -> str:
    if not OPEN_QUESTIONS_FILE.exists():
        return "No open questions."
    with open(OPEN_QUESTIONS_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return content.strip()

def agent_preflight(topic: str, hybrid: bool = True, graph: bool = True, limit: int = 10) -> str:
    from scripts.context_export import export_context
    context_bundle = export_context(topic=topic, limit=limit, hybrid=hybrid, graph=graph)
    
    template_path = ROOT / "scripts" / "templates" / "preflight.md"
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
        
    return template.format(
        topic=topic,
        log_entries=get_recent_logs(),
        open_questions=get_open_questions(),
        context_bundle=context_bundle
    )

def agent_postedit(summary: str, files: list[str], checks: str) -> str:
    template_path = ROOT / "scripts" / "templates" / "postedit.md"
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
        
    files_list = "\n".join([f"- `{file}`" for file in files])
    
    content = template.format(
        timestamp=get_timestamp(),
        summary=summary,
        files=files_list,
        checks=checks
    )
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + content + "\n")
        
    return "✅ Post-edit memory recorded."

if __name__ == "__main__":
    pass
