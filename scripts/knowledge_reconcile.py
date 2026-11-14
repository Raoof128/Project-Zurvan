#!/usr/bin/env python3
"""Inspect orphaned mutations and decide whether the store is coherent again.

Not `rollback`. A dead process proves the mutation will not finish and proves
nothing about whether the bytes it already wrote are complete, so automatic
rollback would be fiction and automatic timeout-commit a lie of the same size.

Reconciliation reaches CLEAN WITHOUT rebuilding anything. CLEAN means the
authoritative knowledge state is accepted as coherent; whether a derived index
is current is a separate question answered by CURRENT / STALE / INVALID. An
earlier draft conflated them and deadlocked: reaching CLEAN required a rebuild,
and a rebuild refuses SUSPECT input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scripts import knowledge_revision as kr


@dataclass
class OrphanReport:
    mutation: kr.Mutation
    missing_paths: list = field(default_factory=list)
    parse_failures: list = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing_paths and not self.parse_failures


def _parses(path: Path) -> bool:
    """Readable, and its frontmatter is not torn.

    Uses the repository's existing parser rather than calling YAML directly.
    `graph_build.parse_frontmatter` and `wiki_merge._parse_fm` already exist; a
    third definition of "valid frontmatter" would eventually disagree with
    ingestion about what Zurvan can actually read.

    **That parser never raises** — measured, it returns `({}, text)` for torn
    frontmatter exactly as it does for a file that simply has none::

        valid  -> ({'title': 'X'}, '\\n\\nbody\\n')
        torn   -> ({},            '---\\ntitle: [unclosed\\n')
        no fm  -> ({},            'just text\\n')

    So "did it raise" detects nothing. The structural signal is that the parser
    did not CONSUME a frontmatter block the file clearly opens: the text starts
    with `---` and the returned body still does. That reuses the shared parser
    as the definition of what Zurvan can read, which is the point, without
    depending on an exception it does not throw.

    Syntax only — deliberately not a claim that the operation finished.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        from scripts.graph_build import parse_frontmatter
        meta, body = parse_frontmatter(text)
    except Exception:
        return False
    if text.lstrip().startswith("---") and not meta and body.lstrip().startswith("---"):
        return False                 # opened a block the parser could not close
    return True


def _completed_ok(path: Path, planned) -> bool:
    """Z2: the claim store is SQLite; the text parser would raise
    UnicodeDecodeError on it (found by the builder check, 2026-11-14)."""
    if path.name == "claims.sqlite":
        from scripts.claim_store import reconcile_ok
        pages = [p for p in planned if "/wiki/claims/" in str(p).replace("\\", "/")]
        return reconcile_ok(path, pages)
    return _parses(path)


def inspect(root=None) -> list:
    kr.detect_orphans(root)
    reports = []
    for m in kr.mutations_in(kr.ORPHANED, root):
        missing = [p for p in m.planned_paths if p not in m.completed_paths]
        failures = [p for p in m.completed_paths if not _completed_ok(Path(p), m.planned_paths)]
        reports.append(OrphanReport(mutation=m, missing_paths=missing,
                                    parse_failures=failures))
    return reports


def reconcile(root=None, accept=False, actor="local:unknown", reason=None) -> dict:
    """Return the store to CLEAN when the evidence supports it.

    `accept=True` records an explicit decision for an operation whose
    completeness cannot be established mechanically. It is the only path that
    clears an incomplete orphan, and it is deliberately not the default.
    """
    reports = inspect(root)
    if not reports:
        return {"status": "CLEAN", "reports": [],
                "revision": kr.current(root).revision}

    unresolved = [r for r in reports if not r.complete]
    if unresolved and not accept:
        return {"status": "NEEDS_REVIEW", "reports": reports,
                "revision": kr.current(root).revision}

    result = kr.reconcile_revision(
        [r.mutation.mutation_id for r in reports],
        actor=actor,
        reason=reason or ("accepted without mechanical proof of completeness"
                          if unresolved else "all planned paths completed"),
        root=root)
    return {"status": "CLEAN", "reports": reports,
            "revision": result["to_revision"]}


def format_report(reports, status, revision) -> str:
    """Render the RESULTING state, never the pre-reconciliation one.

    An earlier draft derived the header from the reports, so an accepted
    reconciliation printed `SUSPECT revision ...` directly above
    `status: CLEAN`.
    """
    lines = [f"{status if status != 'NEEDS_REVIEW' else 'SUSPECT'} "
             f"revision {revision}"]
    for r in reports:
        m = r.mutation
        lines.append("Orphaned mutation:")
        # Three separate facts, never merged into one word. An operator reading
        # "zurvan / zurvan / otherproj" learns that the mutation targeted Zurvan,
        # ran in the Zurvan workspace, and that the global selector was stale —
        # which is evidence. One field holding whichever of those happened to
        # be written was fiction.
        lines.append(f"  store:     {m.store_project or '(unknown)'}")
        lines.append(f"  execution: {m.execution_project or '(unknown)'}")
        lines.append(f"  selected:  {m.selected_project or '(none)'}"
                     "   [diagnostic only]")
        lines.append(f"  writer:  {m.writer or '(unknown)'}")
        lines.append(f"  caller:  {m.caller or '(unknown)'}")
        lines.append(f"  owner:   pid {m.owner_pid} / {m.owner_identity}")
        lines.append(f"  planned:   {', '.join(m.planned_paths) or '(none)'}")
        lines.append(f"  completed: {', '.join(m.completed_paths) or '(none)'}")
        if status == "NEEDS_REVIEW" and r.missing_paths:
            lines.append(f"  → {len(r.missing_paths)} planned path(s) never "
                         "completed; semantic completeness UNPROVEN")
        if status == "NEEDS_REVIEW" and r.parse_failures:
            lines.append(f"  → {len(r.parse_failures)} written file(s) do not "
                         "parse; semantic completeness UNPROVEN")
    if status == "NEEDS_REVIEW":
        lines.append("Snapshots blocked until reconciliation. "
                     "Live writes remain available.")
    return "\n".join(lines)
