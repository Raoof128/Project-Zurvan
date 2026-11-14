#!/usr/bin/env python3
"""Z2 claim pages: deterministic renders of the ledger (spec §7.5).

The ledger is the master; `wiki/claims/*.md` are generated views of it. A page
carries `generated_content_hash` = sha256 of the whole page minus that one line,
so ANY hand edit (body or frontmatter) is detectable. A detected edit is a
conflict: `adopt` turns it into a revision, `regenerate --force` discards it,
and no command overwrites it silently.

This module is the only writer of `wiki/claims/`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_scope as ks
from scripts.safe_write import write_file_safely

_HASH_KEY = "generated_content_hash"
_HASH_LINE = re.compile(rf"^{_HASH_KEY}:.*\n", re.M)


def _s(value) -> str:
    """A frontmatter scalar: JSON double-quoted (valid YAML), with '---' escaped
    because `graph_build.parse_frontmatter` splits on the first '---' anywhere."""
    return json.dumps(str(value), ensure_ascii=False).replace("---", "\\u002d--")


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def _quote_lines(text) -> str:
    return "\n".join(f"> {line}" for line in str(text).split("\n"))


def _digest(page_without_hash: str) -> str:
    return hashlib.sha256(page_without_hash.encode("utf-8")).hexdigest()


def render(state, history: list, superseded_texts: list, generated_from_mutation=None) -> str:
    fm = ["---", f"type: {_s('claim')}", f"claim_id: {_s(state.claim_id)}"]
    if state.legacy_id:
        fm.append(f"legacy_id: {_s(state.legacy_id)}")
    fm += [f"authority: {_s('structured')}", f"status: {_s(state.status)}"]
    if state.confidence:
        fm.append(f"confidence: {_s(state.confidence)}")
    fm.append(f"confidence_basis: {json.dumps(list(state.basis))}")
    if state.valid_from:
        fm.append(f"valid_from: {_s(state.valid_from)}")
    if state.valid_until:
        fm.append(f"valid_until: {_s(state.valid_until)}")
    if state.evidence and state.evidence[0].get("source"):
        fm.append(f"source: {_s(state.evidence[0]['source'])}")
        if state.evidence[0].get("reader", "").startswith("extract_text"):
            # graph_build links an extracted claim to its ingested source page by
            # `source_file` (the basename ingest named the page after)
            import os.path
            fm.append(f"source_file: {_s(os.path.basename(state.evidence[0]['source']))}")
    if state.scope_project:
        fm.append(f"project: {_s(state.scope_project)}")
    if generated_from_mutation:
        fm.append(f"generated_from_mutation: {_s(generated_from_mutation)}")
    if state.tags:
        fm.append("tags:")
        fm += [f"  - {_s(t)}" for t in state.tags]
    fm.append("---")

    body = ["", "# Claim", state.text if state.text is not None else "(erased)", "",
            "## Evidence"]
    for ev in state.evidence:
        exact = (ev.get("selector") or {}).get("TextQuoteSelector", {}).get("exact")
        body.append(_quote_lines(exact) if exact is not None else "> (erased)")
        body.append("")
    if state.evidence and state.evidence[0].get("source"):
        body.append(f"**Source**: [[{state.evidence[0]['source']}]]")
        body.append("")
    if state.status not in cl.CURRENT_STATUSES:
        reason = next((h["reason"] for h in reversed(history) if h.get("reason")), "")
        body.append(f"> **STATUS: {state.status.upper()}** — {_one_line(reason)}")
        body.append("")
    for old in superseded_texts:
        body.append(f"Supersedes: {_one_line(old)}")
    if superseded_texts:
        body.append("")
    body.append("## History")
    for h in history:
        body.append(f"- rev {h['rev']} · {h['event']} · {(h.get('recorded_at') or '')[:10]}"
                    f" · {h['actor']}")
    page = "\n".join(fm + body) + "\n"
    digest = _digest(page)
    return page.replace("\n---\n", f"\n{_HASH_KEY}: {_s(digest)}\n---\n", 1)


def page_path(state, root=None) -> Path:
    name = (state.legacy_id or state.claim_id) + ".md"
    return ks.store_root(root) / "wiki" / "claims" / name


def _page_text_ok(text: str) -> bool:
    m = re.search(rf'^{_HASH_KEY}:\s*"([0-9a-f]{{64}})"\s*$', text, re.M)
    if not m:
        return False
    return _digest(_HASH_LINE.sub("", text, count=1)) == m.group(1)


def has_unadopted_edit(claim_id, root=None) -> bool:
    state = cl.current(root).get(claim_id)
    if state is None:
        return False
    path = page_path(state, root)
    if not path.exists():
        return False
    try:
        return not _page_text_ok(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return True


def _superseded_texts(claim_id, root, now) -> list:
    try:
        conn = cs.connect(root, readonly=True)
    except cs.sqlite3.OperationalError:
        return []
    try:
        olds = [r[0] for r in conn.execute(
            "SELECT to_claim FROM relations WHERE from_claim = ? AND kind = 'supersedes'"
            " ORDER BY relation_id", (claim_id,))]
    finally:
        conn.close()
    return [now[o].text for o in olds if o in now and now[o].text is not None]


def write_page(claim_id, root=None, *, force: bool = False) -> Path:
    now = cl.current(root)
    state = now[claim_id]
    path = page_path(state, root)
    if not force and path.exists() and has_unadopted_edit(claim_id, root):
        raise cs.ClaimRefused("CLAIM_CONFLICT", f"{path.name} has unadopted edits")
    text = render(state, cl.history(claim_id, root), _superseded_texts(claim_id, root, now),
                  generated_from_mutation=state.mutation_id)
    if not write_file_safely(str(path), text):
        raise cs.ClaimRefused("PAGE_WRITE_REFUSED", str(path))
    return path


def conflicts(root=None) -> list:
    return sorted(cid for cid in cl.current(root) if has_unadopted_edit(cid, root))


_CLAIM_TEXT = re.compile(r"^# Claim\n(.*?)(?:\n\n## Evidence|\Z)", re.S | re.M)


def adopt(claim_id, *, actor, reason, root=None) -> int:
    state = cl.current(root).get(claim_id)
    if state is None:
        raise cs.ClaimRefused("UNKNOWN_CLAIM", claim_id)
    try:
        text = page_path(state, root).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise cs.ClaimRefused("ADOPT_UNPARSEABLE", str(exc)) from None
    m = _CLAIM_TEXT.search(text)
    if not m or not m.group(1).strip():
        raise cs.ClaimRefused("ADOPT_UNPARSEABLE", "no '# Claim' text found in the edited page")
    new_text = m.group(1).strip()
    if new_text == state.text:
        raise cs.ClaimRefused("ADOPT_NOTHING",
                              "claim text unchanged; use `claim regenerate --force` to discard")
    return cl.revise(claim_id, reason=reason, actor=actor, method="human", text=new_text,
                     root=root, _adopting=True)


def regenerate(claim_id=None, *, force: bool = False, root=None) -> list:
    """Rewrite pages from the ledger. An edited page is skipped unless `force`."""
    targets = [claim_id] if claim_id else sorted(cl.current(root))
    written = []
    for cid in targets:
        if not force and has_unadopted_edit(cid, root):
            continue
        written.append(write_page(cid, root, force=True))
    return written
