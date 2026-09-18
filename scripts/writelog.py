#!/usr/bin/env python3
"""Who wrote to the live knowledge base, recorded at the moment of writing.

The session guard in `tests/conftest.py` asks "did the live store move?" and
fails if it did. That question cannot distinguish the two cases it sees:

* a TEST writing into production — the defect it exists to catch, which once
  left 48 `note-test-note-*.md` pages plus 28 notes and 4 decisions inside every
  retrieval baseline the project had ever measured;
* a colleague, or another repository's session, recording a decision through
  MCP while the suite happens to be running.

Only the first is a defect. The second is the product working: Zurvan is shared
cross-project memory and the server runs with `ZURVAN_MCP_READONLY=0`. Twice —
2026-09-17 14:57 and 2026-09-18 15:21 — the second case aborted a twenty-minute
gate with nothing wrong in any code under test.

Measurement snapshots fix the RETRIEVAL half of that: `eval_search` scores a
frozen index and no longer cares what the live store does. They do not fix
pytest, which is not scoring retrieval — it is asserting the suite did not
contaminate the corpus. That assertion is worth keeping. It just needs to ask a
better question.

## Attribution, by inheritance and ancestry rather than by text

Every write to `wiki/` records the pid, the parent pid, and a session token
inherited through the environment. A test process sets the token before the
suite runs, so its own writes and those of any subprocess it spawns carry it. A
session in another repository has a different token, or none.

Deliberately NOT by message text: "does this look like a test note?" is a
judgement call that gets it wrong in both directions, and an attacker — or an
ordinary decision whose title happens to contain the word "test" — walks
straight through it.

## Two properties that keep this honest

**It fails closed.** `all_external` is false for an empty window. A change with
no attribution is treated as ours, because an un-stamped writer is exactly the
case we cannot reason about. Adding a new write path without stamping it makes
the guard stricter, never weaker.

**It is evidence, not an excuse.** It never declares a mutated measurement
valid. Snapshots already removed that dependency; this only decides whether a
corpus change is the suite's fault.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SESSION_ENV = "ZURVAN_WRITE_SESSION"
LOG_NAME = "writes.jsonl"
#: Only writes under this tree are recorded: it is what the guard watches, and
#: what every retrieval baseline is a property of.
WATCHED_DIR = "wiki"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_path(root=None) -> Path:
    """`data/writes.jsonl` — under data/, never under `wiki/`.

    Recording a write inside the corpus would itself be a write to the corpus,
    so the guard would fire on its own bookkeeping.
    """
    if root is None:
        from scripts.config import PROJECT_ROOT
        root = PROJECT_ROOT
    return Path(root) / "data" / LOG_NAME


def session_token() -> str:
    return os.environ.get(SESSION_ENV, "")


def _append(root, entry: dict) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def _is_watched(target: Path, root: Path) -> bool:
    try:
        rel = target.resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return len(rel.parts) > 0 and rel.parts[0] == WATCHED_DIR


def record(file_path, action: str = "write", root=None) -> None:
    """Stamp one write. Never raises — bookkeeping must not fail a real write."""
    try:
        if root is None:
            from scripts.config import PROJECT_ROOT
            root = PROJECT_ROOT
        target = Path(file_path)
        if not _is_watched(target, Path(root)):
            return
        _append(root, {
            "at": now(),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "session": session_token(),
            "path": str(target),
            "action": action,
        })
    except Exception:
        # A guard that can lose someone's decision page in order to record that
        # a decision page was written is worse than no guard at all.
        pass


def entries(root=None, since: str | None = None) -> list:
    """Parsed write records, optionally limited to a window.

    A half-written line from a process that was killed mid-append is skipped
    rather than raising: one corrupt line must not take the suite down with it.
    """
    path = log_path(root)
    if not path.is_file():
        return []
    out = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or "at" not in entry:
            continue
        if since is not None and entry["at"] < since:
            continue
        out.append(entry)
    return out


def classify(records, session: str | None = None, pids=None) -> tuple:
    """Split records into (ours, external).

    Ours means: the same session token we are running under, OR a process that
    is us or was spawned by us. The token is what covers a subprocess — the
    original contamination was a test shelling out to the real CLI — and the pid
    check covers a child that inherited no environment.
    """
    session = session_token() if session is None else session
    pids = {os.getpid()} if pids is None else set(pids)
    ours, external = [], []
    for entry in records:
        mine = (
            (session and entry.get("session") == session)
            or entry.get("pid") in pids
            or entry.get("ppid") in pids
        )
        (ours if mine else external).append(entry)
    return ours, external


def all_external(records, session: str | None = None, pids=None) -> bool:
    """True only when there is POSITIVE evidence every write was someone else's.

    False for an empty window, on purpose. No evidence is not evidence of
    innocence, and this is the line that keeps the guard fail-closed.
    """
    if not records:
        return False
    ours, _external = classify(records, session=session, pids=pids)
    return not ours


def describe(records) -> str:
    """One line per write, for the guard's diagnostic."""
    lines = []
    for entry in records:
        lines.append(f"    {entry.get('at', '?')}  pid={entry.get('pid')}  "
                     f"session={entry.get('session') or '(none)'}  "
                     f"{entry.get('action', 'write')} {entry.get('path')}")
    return "\n".join(lines)
