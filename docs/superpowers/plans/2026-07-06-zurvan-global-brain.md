# Zurvan Global Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Zurvan the persistent cross-project memory for every Claude Code session: a ~150-token session-start digest per project, auto-reindex when stale, and on-demand read-only MCP recall everywhere.

**Architecture:** Extend `agent prime` with `--project` (lean keyword digest, no embedding load) and `--fix-stale` (incremental reindex); wire a global SessionStart hook through the self-locating `scripts/zurvan` wrapper; harden the existing user-scope MCP registration to read-only; add a short recall/write-back section to the global `~/.claude/CLAUDE.md`.

**Tech Stack:** Python 3 stdlib only (pathlib/sqlite3), pytest, Claude Code hooks/MCP JSON config.

**Spec:** `docs/superpowers/specs/2026-07-06-zurvan-global-brain-design.md`

## Global Constraints

- **Never `git push`** — local commits only (pre-push hook enforces; per Raouf 2026-07-04).
- Frozen artifacts untouched: `eval/provenance_real_*.jsonl`, committed `data/traces/`, 2C/1B metrics (86%/79%).
- **No retrieval-ranking or indexing-format change** — `--fix-stale` only invokes the existing `rebuild_search_index()`; therefore no `eval_search` re-run is required, state this in CHANGELOG.
- Digest path must **not load the embedding model** (must run <1 s) and stays ≤ ~150 tokens (caps: 5 decisions, 3 claims, 3 questions, 120-char lines).
- Hook must never block a session: `|| true`, `2>/dev/null`, hook `timeout` field set.
- MCP stays read-only from all projects: `ZURVAN_MCP_READONLY=1` (fails closed; only `"0"` enables writes).
- Change protocol: postflight `Raouf:` entries in AGENTS.md + CHANGELOG.md; quality gate = `PYTHONPATH=. python -m pytest -q` (0 failed) + `python scripts/public_repo_guard.py` + `git diff --check`.
- Run every repo command from `<ZURVAN_REPO>` with `PYTHONPATH=.`.
- Do not stage `.obsidian/graph.json`; `wiki/log.md` is gitignored (test noise there is fine).

---

### Task 1: `project_digest()` — lean cross-project digest

**Files:**
- Modify: `scripts/agent_workflow.py` (append after `_index_staleness`, before `agent_prime`)
- Test: `tests/test_agent_workflow.py` (append)

**Interfaces:**
- Produces: `project_digest(project: str, root: Path | None = None) -> str` — later tasks call it from `agent_prime(project=...)`.
- Matching rule: case-insensitive; `_` normalized to `-`; a page matches if the normalized project name is a substring of its normalized title/text, OR any hyphen-token of the project name with length ≥ 4 equals one of its tags (so repo `nexus-archive` matches tag `nexus`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_agent_workflow.py`:

```python
from pathlib import Path
from scripts.agent_workflow import project_digest


def _make_corpus(tmp_path: Path):
    d = tmp_path / "wiki" / "decisions"
    c = tmp_path / "wiki" / "claims"
    d.mkdir(parents=True)
    c.mkdir(parents=True)
    (d / "nexus-auth.md").write_text(
        '---\ntitle: "Zero-trust auth for Nexus Archive"\ntype: decision\n'
        'status: "accepted"\ntags:\n  - "nexus"\n  - "auth"\n---\n\n# Zero-trust auth\n',
        encoding="utf-8")
    (d / "unrelated.md").write_text(
        '---\ntitle: "Delay vector search"\ntype: decision\nstatus: "accepted"\n'
        'tags:\n  - "roadmap"\n---\n\n# Delay vector search\n', encoding="utf-8")
    (c / "claim-abc123.md").write_text(
        '---\ntype: claim\nconfidence: "high"\nsource: "docs/x.md"\n'
        'tags:\n  - "nexus"\n---\n\n# Claim\nNexus Archive uses Supabase RLS.\n\n'
        '## Evidence\n> quote\n', encoding="utf-8")
    (tmp_path / "wiki" / "open-questions.md").write_text(
        "# Open Questions\n\n## Q: Should nexus-archive rotate JWTs weekly?\n"
        "- **ID**: aaa\n- **Tags**: nexus, auth\n\n"
        "## Q: Unrelated question?\n- **ID**: bbb\n- **Tags**: mcp\n",
        encoding="utf-8")


def test_project_digest_matches_tags_titles_and_questions(tmp_path):
    _make_corpus(tmp_path)
    out = project_digest("Nexus_Archive", root=tmp_path)
    assert "Zero-trust auth for Nexus Archive" in out
    assert "wiki/decisions/nexus-auth.md" in out
    assert "Supabase RLS" in out
    assert "rotate JWTs" in out
    assert "Delay vector search" not in out
    assert "Unrelated question" not in out
    assert "1 decision" in out and "1 claim" in out and "1 open question" in out
    # pointer line for deeper recall
    assert "zurvan_search" in out


def test_project_digest_empty(tmp_path):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    out = project_digest("simurghforge", root=tmp_path)
    assert "No Zurvan knowledge for this project yet." in out
    assert "zurvan_search" in out


def test_project_digest_caps_output(tmp_path):
    d = tmp_path / "wiki" / "decisions"
    d.mkdir(parents=True)
    for i in range(12):
        (d / f"dec-{i}.md").write_text(
            f'---\ntitle: "Simurgh decision {i} ' + "x" * 200 + '"\n'
            'tags:\n  - "simurgh"\n---\n', encoding="utf-8")
    out = project_digest("simurgh", root=tmp_path)
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert len(lines) <= 5                       # decision cap
    assert all(len(l) <= 160 for l in lines)     # line cap (120 + path suffix)
    assert "12 decisions" in out                 # counts reflect the true total
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_agent_workflow.py -v -k project_digest`
Expected: FAIL — `ImportError: cannot import name 'project_digest'`

- [ ] **Step 3: Implement** — insert into `scripts/agent_workflow.py` after `_index_staleness` (keep the module's stdlib-only, naive-line-parser style used elsewhere in the repo):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_agent_workflow.py -v`
Expected: all PASS (new 3 + existing)

- [ ] **Step 5: Commit**

```bash
git add scripts/agent_workflow.py tests/test_agent_workflow.py
git commit -m "feat(agent): project_digest — lean cross-project recall digest (Phase 27)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `agent_prime(project=..., fix_stale=...)`

**Files:**
- Modify: `scripts/agent_workflow.py:78` (the `agent_prime` definition)
- Test: `tests/test_agent_workflow.py` (append)

**Interfaces:**
- Consumes: `project_digest` (Task 1), `_index_staleness` (existing), `scripts.rebuild_search_index.rebuild_search_index()` (existing, incremental — reuses unchanged embeddings).
- Produces: `agent_prime(project: str | None = None, fix_stale: bool = False) -> str` — Task 3 wires the CLI to exactly this signature.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_agent_workflow.py`:

```python
from unittest.mock import patch as _patch
from scripts.agent_workflow import agent_prime


def test_agent_prime_project_routes_to_digest(tmp_path, monkeypatch):
    _make_corpus(tmp_path)
    monkeypatch.setattr("scripts.agent_workflow.ROOT", tmp_path)
    out = agent_prime(project="nexus-archive")
    assert out.startswith("# Zurvan recall — nexus-archive")
    assert "# Zurvan prime" not in out  # digest replaces the full card


def test_agent_prime_fix_stale_triggers_incremental_reindex(monkeypatch):
    monkeypatch.setattr("scripts.agent_workflow._index_staleness",
                        lambda *a, **k: "STALE — 3 file(s) newer than the index; run `zurvan index search`")
    with _patch("scripts.rebuild_search_index.rebuild_search_index") as rb:
        agent_prime(fix_stale=True)
    rb.assert_called_once()


def test_agent_prime_fix_stale_survives_reindex_failure(monkeypatch):
    monkeypatch.setattr("scripts.agent_workflow._index_staleness",
                        lambda *a, **k: "STALE — 1 file(s) newer than the index; run `zurvan index search`")
    with _patch("scripts.rebuild_search_index.rebuild_search_index",
                side_effect=RuntimeError("boom")):
        out = agent_prime(fix_stale=True)   # must not raise
    assert "STALE" in out                    # degrades to the existing warning


def test_agent_prime_fix_stale_skips_when_fresh(monkeypatch):
    monkeypatch.setattr("scripts.agent_workflow._index_staleness",
                        lambda *a, **k: "fresh")
    with _patch("scripts.rebuild_search_index.rebuild_search_index") as rb:
        agent_prime(fix_stale=True)
    rb.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_agent_workflow.py -v -k agent_prime`
Expected: FAIL — `TypeError: agent_prime() got an unexpected keyword argument`

- [ ] **Step 3: Implement** — change the `agent_prime` signature/head in `scripts/agent_workflow.py`:

```python
def agent_prime(project: str | None = None, fix_stale: bool = False) -> str:
    """Compact orientation card (~300 tokens) for agent session starts.

    fix_stale: if the search index is STALE, run the incremental rebuild
    first (reuses unchanged embeddings); on failure degrade to the warning.
    project: return the lean cross-project recall digest instead of the
    full Zurvan card (for SessionStart hooks in other repos).
    """
    import sqlite3

    if fix_stale and _index_staleness().startswith("STALE"):
        try:
            from scripts.rebuild_search_index import rebuild_search_index
            rebuild_search_index()
        except Exception:
            pass  # degrade to the STALE warning below

    if project:
        return project_digest(project)

    lines = [
        "# Zurvan prime",
        ...            # rest of the existing body, unchanged
```

(Only the signature, docstring, and the two blocks above change; the existing body from `lines = [` down stays as-is.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_agent_workflow.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/agent_workflow.py tests/test_agent_workflow.py
git commit -m "feat(agent): prime --fix-stale auto-reindex + --project digest routing (Phase 27)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: CLI wiring for `agent prime --project --fix-stale`

**Files:**
- Modify: `scripts/cli.py:414` (parser) and `scripts/cli.py:879-881` (handler)
- Test: `tests/test_cli.py` (append; subprocess style like the rest of that file)

**Interfaces:**
- Consumes: `agent_prime(project=..., fix_stale=...)` from Task 2.
- Produces: `zurvan agent prime [--project NAME] [--fix-stale]` — Task 4's hooks depend on exactly these flag names.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`:

```python
def test_cli_agent_prime_project_digest():
    import sys
    res = subprocess.run(
        [sys.executable, "scripts/cli.py", "agent", "prime",
         "--project", "zzqxnonexistentzzq"],
        capture_output=True, text=True)
    assert res.returncode == 0
    assert "Zurvan recall — zzqxnonexistentzzq" in res.stdout
    assert "No Zurvan knowledge for this project yet." in res.stdout


def test_cli_agent_prime_has_fix_stale_flag():
    import sys
    res = subprocess.run(
        [sys.executable, "scripts/cli.py", "agent", "prime", "--help"],
        capture_output=True, text=True)
    assert "--fix-stale" in res.stdout
    assert "--project" in res.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_cli.py -v -k agent_prime`
Expected: FAIL — `unrecognized arguments: --project`

- [ ] **Step 3: Implement** — in `scripts/cli.py` replace the parser line (~414):

```python
    agent_prime_p = agent_sub.add_parser(
        "prime", help="Compact session-start orientation card (~300 tokens)")
    agent_prime_p.add_argument(
        "--project", default=None,
        help="Lean cross-project recall digest for this project name (~150 tokens)")
    agent_prime_p.add_argument(
        "--fix-stale", action="store_true",
        help="Incrementally rebuild the search index first when it is stale")
```

and the handler (~879):

```python
    elif args.command == "agent" and args.action == "prime":
        from scripts.agent_workflow import agent_prime
        print(agent_prime(project=args.project, fix_stale=args.fix_stale))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_cli.py tests/test_agent_workflow.py -v`
Expected: all PASS

- [ ] **Step 5: Live sanity check (repo digest for itself)**

Run: `PYTHONPATH=. python scripts/cli.py agent prime --project zurvan | head -20`
Expected: `# Zurvan recall — zurvan` with matching decisions; runs in ~1 s (no model load).

- [ ] **Step 6: Commit**

```bash
git add scripts/cli.py tests/test_cli.py
git commit -m "feat(cli): agent prime --project / --fix-stale (Phase 27)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Hooks + MCP + global CLAUDE.md rollout

**Files:**
- Modify: `.claude/settings.json` (repo — add `--fix-stale`)
- Modify: `~/.claude/settings.json` (global hook; **read + merge, never overwrite** other keys)
- Modify: `~/.claude.json` (fix existing user-scope `mcpServers.zurvan` env)
- Modify: `~/.claude/CLAUDE.md` (append recall section; create if missing)

**Interfaces:**
- Consumes: `zurvan agent prime --fix-stale --project <name>` (Task 3); self-locating wrapper `scripts/zurvan`.
- Produces: session-start digest in every non-Zurvan repo; read-only `zurvan_*` MCP tools everywhere.

- [ ] **Step 1: Back up the three user-level files**

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak-2026-07-06
cp ~/.claude.json ~/.claude.json.bak-2026-07-06
[ -f ~/.claude/CLAUDE.md ] && cp ~/.claude/CLAUDE.md ~/.claude/CLAUDE.md.bak-2026-07-06 || true
```

- [ ] **Step 2: Repo hook gains `--fix-stale`** — in `.claude/settings.json` change the SessionStart command to:

```json
"command": "PYTHONPATH=\"$CLAUDE_PROJECT_DIR\" python \"$CLAUDE_PROJECT_DIR/scripts/cli.py\" agent prime --fix-stale"
```

- [ ] **Step 3: Global SessionStart hook** — read `~/.claude/settings.json`, merge (Python `json` round-trip, preserving all existing keys/hooks) this entry into `hooks.SessionStart`:

```json
{
  "matcher": "startup|clear",
  "hooks": [
    {
      "type": "command",
      "command": "ZR=\"<ZURVAN_REPO>\"; [ \"$CLAUDE_PROJECT_DIR\" = \"$ZR\" ] || \"$ZR/scripts/zurvan\" agent prime --fix-stale --project \"$(basename \"$CLAUDE_PROJECT_DIR\")\" 2>/dev/null || true",
      "timeout": 120,
      "statusMessage": "Zurvan recall..."
    }
  ]
}
```

Notes: `startup|clear` only (resume/compact already carry context); the guard skips the Zurvan repo itself (its local hook runs the full prime); stderr suppressed + `|| true` so failure can never block; timeout 120 s bounds a slow reindex.

- [ ] **Step 4: Fix the existing user-scope MCP registration** — `~/.claude.json` already has top-level `mcpServers.zurvan`, but with `ZURVAN_MCP_READONLY: "0"` (writes enabled globally — contradicts the repo's accepted decision *MCP write mode stays disabled by default*) and `ZURVAN_EMBED_PROVIDER: "mock"`. Edit via Python json round-trip, changing **only** these two env values:

```python
import json, pathlib
p = pathlib.Path.home() / ".claude.json"
d = json.loads(p.read_text())
env = d["mcpServers"]["zurvan"]["env"]
env["ZURVAN_MCP_READONLY"] = "1"
env["ZURVAN_EMBED_PROVIDER"] = "sentence_transformers"
p.write_text(json.dumps(d, indent=2))
```

(Query embeddings follow the index's stored provider regardless of env, so the provider fix is consistency, not a ranking change.)

- [ ] **Step 5: Global CLAUDE.md section** — append to `~/.claude/CLAUDE.md` (create the file if missing):

```markdown
## Zurvan recall (global memory — all projects)

- A `# Zurvan recall` digest may appear at session start: decisions/claims/questions relevant to this repo. Open a page only when it matters (`zurvan_read_page`).
- Before re-deriving a past decision or a fact about Raouf's projects, search first: `zurvan_search` MCP tool (hybrid), or `<ZURVAN_REPO>/scripts/zurvan search "<topic>" --hybrid --json`.
- Write back is manual, from any repo (tag entries with the project name so the digest finds them):
  - `zurvan decision add --title "..." --reason "..." --status accepted --tags <project> ...`
  - `zurvan claim add --text "..." --source "..." --evidence "<verbatim quote>" --confidence high`
  - `zurvan question add --question "..." --reason "..."`
```

- [ ] **Step 6: `zurvan` on PATH (needed by the CLAUDE.md commands)**

```bash
which zurvan || ln -s <ZURVAN_REPO>/scripts/zurvan /opt/homebrew/bin/zurvan
which zurvan
```

Expected: `/opt/homebrew/bin/zurvan`

- [ ] **Step 7: Commit the repo-side change** (user-level files are outside git)

```bash
git add .claude/settings.json
git commit -m "chore(hooks): repo prime hook gains --fix-stale (Phase 27)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: End-to-end verification + MCP-diet advisory + postflight

**Files:**
- Modify: `AGENTS.md`, `CHANGELOG.md` (postflight entries; rotate the oldest AGENTS entry verbatim into `docs/agents-history.md` to keep two)

**Interfaces:**
- Consumes: everything above.
- Produces: verified rollout + the advisory list (report text only — nothing auto-removed).

- [ ] **Step 1: Simulate the global hook from a non-Zurvan repo**

```bash
CLAUDE_PROJECT_DIR="$HOME/Desktop/Raouf" ; export CLAUDE_PROJECT_DIR
ZR="<ZURVAN_REPO>"; [ "$CLAUDE_PROJECT_DIR" = "$ZR" ] || "$ZR/scripts/zurvan" agent prime --fix-stale --project "$(basename "$CLAUDE_PROJECT_DIR")" 2>/dev/null || true
```

Expected: a `# Zurvan recall — Raouf` digest (or the two-line empty digest), exit 0, ~1 s when the index is fresh. Also run once with `CLAUDE_PROJECT_DIR="$ZR"` — expected: **no output** (guard works).

- [ ] **Step 2: Verify user-scope MCP visibility from another directory** (bug anthropics/claude-code#32939)

```bash
cd ~/Desktop/Raouf && claude mcp list 2>/dev/null | grep -i zurvan
```

Expected: `zurvan` listed. If missing: the top-level `mcpServers` block in `~/.claude.json` is the fallback and is already in place from Task 4 — report actual behavior honestly.

- [ ] **Step 3: Read-only enforcement check**

```bash
python3 -c "
import json, pathlib
env = json.loads((pathlib.Path.home()/'.claude.json').read_text())['mcpServers']['zurvan']['env']
assert env['ZURVAN_MCP_READONLY'] == '1', env
print('MCP read-only: OK')"
```

- [ ] **Step 4: MCP-diet advisory (report text only — no removals).** List registered servers and flag overlap/cost:

```bash
python3 -c "
import json, pathlib
d = json.loads((pathlib.Path.home()/'.claude.json').read_text())
print('User-scope MCP servers:', ', '.join(sorted(d.get('mcpServers', {}))))"
```

In the final report, recommend (advisory): `mempalace` overlaps Zurvan's role as memory; 4 near-identical SSH servers (`ssh-mcp`, `ssh1-3`) could collapse to the ones actually used; each active server costs a subprocess + schema tokens per session (best practice ≈ 5-6 active). **Do not remove anything.**

- [ ] **Step 5: Quality gate**

```bash
PYTHONPATH=. python -m pytest -q          # expected: 0 failed
python scripts/public_repo_guard.py       # expected: passed
git diff --check                          # expected: silent
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold.jsonl  # expected: 86% recall unchanged
```

- [ ] **Step 6: Postflight entries** — append a dated `Raouf:` entry (scope / summary / files / verification / follow-ups, Australia/Sydney date) to **both** `AGENTS.md` and `CHANGELOG.md`; rotate the now-third-oldest AGENTS entry verbatim into `docs/agents-history.md`. State explicitly: no ranking/indexing change → no `eval_search` re-run required; frozen golds re-verified.

- [ ] **Step 7: Final commit (local only — never push)**

```bash
git add AGENTS.md CHANGELOG.md docs/agents-history.md
git commit -m "docs(protocol): Phase 27 postflight — global brain rollout entries

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Memory write-back (per CLAUDE.md)** — record the decision in Zurvan itself:

```bash
PYTHONPATH=. python scripts/cli.py decision add \
  --title "Zurvan is the global Claude Code brain: digest + on-demand recall" \
  --reason "Raouf chose session-start ~150-token project digest + on-demand MCP over per-prompt injection (token cost) and over automatic write-back (manual only); index auto-reindexes when stale via SessionStart hook" \
  --status accepted --tags zurvan claude-code memory hooks
```
