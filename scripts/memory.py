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

def resolve_project(explicit: str | None = None, root=None) -> str:
    """The SUBJECT project of a page being written — never merely the workspace
    that happens to be selected in the global registry.

    Written into every decision as `project:` because `wiki/decisions/` is
    tracked and this store holds every project's memory. `public_repo_guard`
    publishes a page only if that field names an allowlisted public project, so
    this value is a publication control, not a label.

    **The bug this exists to prevent.** It previously returned the registry's
    `current` project unconditionally. On 2026-09-15 four decisions written from
    inside the Zurvan repo were stamped with an UNRELATED project, because a session in
    an unrelated repository had left that workspace selected. The subject was
    Zurvan; the stamp said otherwise; and the guard would have made a publication
    decision on it.

    Resolution order, fail-closed:

    1. `explicit` — the caller said so, and a caller who names a project means it.
    2. The registered project containing `root` (default: the working directory
       the command was invoked from). Deepest path wins, so a project nested
       inside another resolves to the inner one. This is what makes write-back
       from any repo correct: run it in another registered repo and you get that
       project, run it in
       Zurvan and you get `zurvan`.
    3. The registry's `current` — **only** when its registered path also contains
       `root`, i.e. when it agrees with (2) anyway.
    4. `"unknown"`, which is not in `PUBLIC_PROJECTS`, so the page is blocked
       from publication rather than published under a guess.

    The invariant: a decision must never inherit a project merely because that
    project is "current" somewhere else.
    """
    from scripts.project_registry import is_safe_slug

    if explicit:
        return explicit if is_safe_slug(explicit) else "unknown"

    try:
        from pathlib import Path as _P
        from scripts.project_registry import load_registry

        here = _P(root).resolve() if root is not None else _P(os.getcwd()).resolve()
        registry = load_registry()
        current = registry.get("current")

        containing = []
        for name, meta in (registry.get("projects") or {}).items():
            try:
                base = _P(meta["path"]).resolve()
            except (KeyError, TypeError, OSError):
                continue
            if here == base or base in here.parents:
                containing.append((len(base.parts), name))
        if not containing:
            return "unknown"

        deepest = max(d for d, _ in containing)
        names = {n for d, n in containing if d == deepest}
        # Rule 3: `current` breaks a tie only when it is itself a containing
        # project. It can never override a project that actually contains root.
        if current in names:
            return current
        return sorted(names)[0]
    except Exception:
        return "unknown"


def current_project_name() -> str:
    """Backwards-compatible alias. Prefer `resolve_project`."""
    return resolve_project()


def add_decision(title: str, reason: str, status: str, tags: List[str],
                 project: str | None = None):
    filepath = _unique_path("wiki/decisions", _make_note_slug(title))

    tags_yaml = "\n".join(f"  - {escape_yaml_string(t)}" for t in tags)
    content = f"""---
title: {escape_yaml_string(title)}
type: decision
project: {escape_yaml_string(resolve_project(project))}
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
    # Routed, not fixed: wiki/open-questions.md is TRACKED on a public remote,
    # so a question only lands there when its tags declare a public project.
    # Everything else goes to the gitignored private file. See
    # scripts/question_routing.py for why this fails closed.
    from scripts.question_routing import questions_path as _route
    questions_path = _route(tags)
    
    # rstrip so an untagged question writes "- **Tags**:" rather than a line
    # with a trailing space, which `git diff --check` reports as an error.
    tags_str = ", ".join(tags)
    entry = (
        f"\n## Q: {question}\n- **ID**: {q_id}\n- **Reason**: {reason}\n"
        f"- **Tags**: {tags_str}".rstrip() + "\n"
    )
    
    if append_file_safely(questions_path, entry):
        append_to_log("Question Added", question)
        print(f"Appended question to {questions_path}")
        return True
    else:
        print("Failed to securely write question.")
        return False
