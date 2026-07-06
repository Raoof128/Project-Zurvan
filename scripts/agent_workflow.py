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

def _index_staleness(root: Path | None = None, db_path: Path | None = None) -> str:
    """One-line freshness verdict: compares the newest wiki/docs file mtime
    against the index's newest indexed_at, so every session start says
    whether search results can be trusted."""
    import sqlite3
    root = root or ROOT
    db_path = db_path or (root / "data" / "search.sqlite")
    if not Path(db_path).exists():
        return "missing — run `zurvan index search`"
    try:
        conn = sqlite3.connect(str(db_path))
        newest_indexed = conn.execute("SELECT MAX(indexed_at) FROM chunks").fetchone()[0]
        conn.close()
        indexed_at = datetime.datetime.fromisoformat(newest_indexed)
    except Exception:
        return "unreadable — run `zurvan index search`"

    from scripts.chunk import scan_markdown_files
    stale = 0
    for rel in scan_markdown_files(root):
        try:
            mtime = datetime.datetime.fromtimestamp((Path(root) / rel).stat().st_mtime)
        except OSError:
            continue
        if mtime > indexed_at:
            stale += 1
    if stale:
        return f"STALE — {stale} file(s) newer than the index; run `zurvan index search`"
    return "fresh"


def _norm(s: str) -> str:
    return s.strip().lower().replace("_", "-")


def _frontmatter_title_tags(path: Path) -> tuple[str, list[str]]:
    """Naive line-based frontmatter reader (same approach as graph_build):
    returns (title, [tags]) — enough for digest matching, no YAML dependency."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "", []
    if not lines or lines[0].strip() != "---":
        return "", []
    title, tags, in_tags = "", [], False
    for line in lines[1:80]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            title = line.split(":", 1)[1].strip().strip('"')
            in_tags = False
        elif line.startswith("tags:"):
            in_tags = True
        elif in_tags and line.strip().startswith("- "):
            tags.append(_norm(line.strip()[2:].strip('"')))
        elif line and not line.startswith(" ") and ":" in line:
            in_tags = False
    return title, tags


def _claim_text(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    parts = body.split("# Claim", 1)
    if len(parts) == 2:
        for line in parts[1].splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(">"):
                return line
    return ""


def _matches(name: str, tokens: list[str], text: str, tags: list[str]) -> bool:
    if name in _norm(text):
        return True
    return any(t in tags for t in tokens)


def project_digest(project: str, root: Path | None = None) -> str:
    """~150-token, titles-only recall digest for a project name. Pure keyword
    scan over decisions/claims/open questions — never loads the embedding
    model, so it is safe in a SessionStart hook for any repo."""
    root = root or ROOT
    name = _norm(project)
    tokens = [t for t in name.split("-") if len(t) >= 4] or [name]

    decisions, claims, questions = [], [], []
    n_dec = n_claim = n_q = 0

    for path in sorted((root / "wiki" / "decisions").glob("*.md")):
        title, tags = _frontmatter_title_tags(path)
        if _matches(name, tokens, title, tags):
            n_dec += 1
            if len(decisions) < 5:
                rel = path.relative_to(root)
                decisions.append(f"- {title[:120]} ({rel})")

    for path in sorted((root / "wiki" / "claims").glob("*.md")):
        _, tags = _frontmatter_title_tags(path)
        text = _claim_text(path)
        if _matches(name, tokens, text, tags):
            n_claim += 1
            if len(claims) < 3:
                rel = path.relative_to(root)
                claims.append(f"- {text[:120]} ({rel})")

    oq = root / "wiki" / "open-questions.md"
    if oq.exists():
        block_q, block_tags = "", ""
        blocks = []
        for line in oq.read_text(encoding="utf-8").splitlines() + ["## Q:"]:
            if line.startswith("## Q:"):
                if block_q:
                    blocks.append((block_q, block_tags))
                block_q, block_tags = line[5:].strip(), ""
            elif "**Tags**:" in line:
                block_tags = line.split("**Tags**:", 1)[1]
        for q, qtags in blocks:
            tag_list = [_norm(t) for t in qtags.replace(",", " ").split()]
            if _matches(name, tokens, q, tag_list):
                n_q += 1
                if len(questions) < 3:
                    questions.append(f"- {q[:120]}")

    lines = [f"# Zurvan recall — {project}"]
    if not (n_dec or n_claim or n_q):
        lines.append("No Zurvan knowledge for this project yet.")
    else:
        if decisions:
            lines += ["Decisions:"] + decisions
        if claims:
            lines += ["Claims:"] + claims
        if questions:
            lines += ["Open questions:"] + questions

        def _plural(n, w):
            return f"{n} {w}{'' if n == 1 else 's'}"

        lines.append(f"{_plural(n_dec, 'decision')}, {_plural(n_claim, 'claim')}, "
                     f"{_plural(n_q, 'open question')} match.")
    lines.append("Deeper: `zurvan_search` MCP tool (hybrid) or "
                 "`zurvan search \"<topic>\" --hybrid --json`; "
                 "write back with `zurvan decision/claim/question add` (tag the project).")
    return "\n".join(lines)


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
        lines.append(f"Search index: {chunk_count} chunks — {_index_staleness()}.")
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
