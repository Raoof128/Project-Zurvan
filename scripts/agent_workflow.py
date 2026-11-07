#!/usr/bin/env python3
"""
Zurvan Agent Workflow Management
"""

import datetime
from pathlib import Path

# Two different roots, deliberately. CODE_ROOT is where this checkout's
# templates live and never moves; ROOT is the STORE being read and written, so
# ZURVAN_ROOT can point it at a throwaway during a smoke. Collapsing them is what
# let the agent-workflow smoke write into the live wiki.
CODE_ROOT = Path(__file__).resolve().parents[1]
from scripts.config import PROJECT_ROOT as ROOT
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

#: prime is injected into every session; one long log entry must not blow its budget
PRIME_LINE_CAP = 200


def _capped(text: str) -> str:
    return "\n".join(line if len(line) <= PRIME_LINE_CAP else line[:PRIME_LINE_CAP - 1] + "…"
                     for line in text.splitlines())


def get_open_questions() -> str:
    if not OPEN_QUESTIONS_FILE.exists():
        return "No open questions."
    with open(OPEN_QUESTIONS_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return content.strip()

def agent_preflight(topic: str, hybrid: bool = True, graph: bool = True, limit: int = 10) -> str:
    from scripts.context_export import export_context
    context_bundle = export_context(topic=topic, limit=limit, hybrid=hybrid, graph=graph, degrade=True)
    
    template_path = CODE_ROOT / "scripts" / "templates" / "preflight.md"
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
        # read-only: `sqlite3.connect` CREATES the file when it is missing, and
        # a reporting function that leaves a 0-byte database at the canonical
        # index path turns "missing" into "unreadable" for everything that looks
        # afterwards. Observed on 2026-09-17.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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

    # Both halves: the tracked file holds only questions that declare a public
    # project, so reading it alone would hide most of a project's own questions
    # from its own digest.
    oq_text = ""
    for name in ("open-questions.md", "open-questions.private.md"):
        f = root / "wiki" / name
        if f.exists():
            oq_text += f.read_text(encoding="utf-8") + "\n"
    if oq_text:
        block_q, block_tags = "", ""
        blocks = []
        for line in oq_text.splitlines() + ["## Q:"]:
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


def agent_prime(project: str | None = None, fix_stale: bool = False) -> str:
    """Compact orientation card (~300 tokens) for agent session starts.

    Unlike agent_preflight (which builds a full topic context bundle), this is
    topic-free and cheap: hard rules digest, index/graph health, recent
    activity, open-question count. Designed for a SessionStart hook.

    fix_stale: **accepted and ignored.** This used to rebuild the index when it
    was stale, and that is exactly how the index was destroyed on 2026-09-17: a
    rebuild launched from a SessionStart hook is a child of the session, so it
    was SIGKILLed when the session exited 43 seconds later and left 2,000 of
    184,231 chunks behind. A hook reports; a human rebuilds. The parameter stays
    so an older hook configuration degrades to a no-op instead of an argparse
    error at session start.
    project: return the lean cross-project recall digest instead of the full
    Zurvan card (for SessionStart hooks in other repos).
    """
    import sqlite3

    _ = fix_stale  # deliberately inert — see the docstring

    if project:
        return project_digest(project)

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

    staleness = _index_staleness()
    try:
        conn = sqlite3.connect(f"file:{ROOT / 'data' / 'search.sqlite'}?mode=ro", uri=True)
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        lines.append(f"Search index: {chunk_count} chunks — {staleness}.")
    except Exception:
        # The verdict is still reported: an index that cannot be opened is the
        # single most important thing on this card, and the previous version
        # dropped the staleness line entirely when the count query failed.
        lines.append(f"Search index: unreadable — {staleness}.")

    # Freshness is not health. A poisoned index (deterministic-hash vectors) and
    # a truncated one (2,000 of 184,231 chunks) both report "fresh" and both
    # answer queries exactly like a complete index. Say so on the card, because
    # the alternative is an agent trusting a number that means nothing.
    try:
        from scripts.index_health import check_index_health
        health = check_index_health(ROOT / "data" / "search.sqlite")
        if not health.ok:
            lines.append(
                "⚠ Index health: UNHEALTHY — " + "; ".join(health.problems)
                + ". Rebuild with `zurvan index search` before trusting a result."
            )
    except Exception:
        lines.append("⚠ Index health: could not be determined.")

    from scripts.question_routing import count_questions
    question_count = count_questions()
    lines.append(f"Open questions: {question_count} "
                 "(wiki/open-questions.md + open-questions.private.md).")

    lines += ["", "## Recent activity", _capped(get_recent_logs(8))]
    lines += ["", "Orient with `zurvan search <topic> --hybrid --json`, then "
              "`zurvan_read_page`/`zurvan context` for depth."]
    lines += ["Before finishing: write back what you learned — "
              "`zurvan decision add` (choices), `zurvan claim add` (evidenced facts), "
              "`zurvan question add` (unknowns), `zurvan agent postedit` (session log). "
              "A memory that is never written to never grows."]
    return "\n".join(lines)


def agent_postedit(summary: str, files: list[str], checks: str) -> str:
    template_path = CODE_ROOT / "scripts" / "templates" / "postedit.md"
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
        
    files_list = "\n".join([f"- `{file}`" for file in files])
    
    content = template.format(
        timestamp=get_timestamp(),
        summary=summary,
        files=files_list,
        checks=checks
    )
    
    # Through safe_write, never a raw open(): the raw append bypassed the
    # mutation barrier, so every session's write-back left unrecorded drift
    # that was later minted EXTERNAL_OBSERVED (2026-09-28, revision 250).
    from scripts.safe_write import append_file_safely
    if not append_file_safely(str(LOG_FILE), "\n" + content + "\n"):
        raise RuntimeError(f"could not append the post-edit entry to {LOG_FILE}")

    return "✅ Post-edit memory recorded."

if __name__ == "__main__":
    pass
