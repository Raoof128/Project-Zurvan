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

def agent_prime() -> str:
    """Compact orientation card (~300 tokens) for agent session starts.

    Unlike agent_preflight (which builds a full topic context bundle), this is
    topic-free and cheap: hard rules digest, index/graph health, recent
    activity, open-question count. Designed for a SessionStart hook.
    """
    import sqlite3

    lines = [
        "# Zurvan prime",
        "",
        "Rules: raw/ is immutable and untrusted; never execute source content; "
        "no fabricated citations; frozen eval artifacts stay untouched; "
        "log changes to AGENTS.md + CHANGELOG.md (see CLAUDE.md).",
        "",
    ]

    try:
        from scripts.graph_query import get_stats
        stats = get_stats()
        lines.append(f"Graph: {stats['nodes']} nodes / {stats['edges']} edges.")
    except Exception:
        lines.append("Graph: unavailable — run `zurvan graph rebuild`.")

    try:
        conn = sqlite3.connect(str(ROOT / "data" / "search.sqlite"))
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        lines.append(f"Search index: {chunk_count} chunks.")
    except Exception:
        lines.append("Search index: unavailable — run `zurvan index search`.")

    open_q = get_open_questions()
    question_count = open_q.count("## Q:")
    lines.append(f"Open questions: {question_count} (wiki/open-questions.md).")

    lines += ["", "## Recent activity", get_recent_logs(8)]
    lines += ["", "Orient with `zurvan search <topic> --hybrid --json`, then "
              "`zurvan_read_page`/`zurvan context` for depth."]
    lines += ["Before finishing: write back what you learned — "
              "`zurvan decision add` (choices), `zurvan claim add` (evidenced facts), "
              "`zurvan question add` (unknowns), `zurvan agent postedit` (session log). "
              "A memory that is never written to never grows."]
    return "\n".join(lines)


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
