"""An authoritative write with no operation above it is refused, not recorded.

**The incident.** On 2026-09-23 an agent in another repository wanted to stamp
`project="ClientX"` on two decisions. `zurvan decision add --project` could not
carry it — `cli.py` popped `--project` as the global workspace switch before
argparse ran — so the agent imported the library instead:

    uv run python -c "from scripts.memory import add_decision, add_note; ..."

Three logical writes became SIX revisions (73-78), each page and its
`wiki/log.md` append committing separately, so a snapshot taken between them
would have named a decision the log does not mention. `resolve_project('ClientX')`
then answered `"unknown"` without a word, and the agent hand-edited the pages.
Forensics: `~/.zurvan/forensics/2026-09-28-implicit-mutations-pid22012*.json`.

The ledger did what it was designed to do — record the writes as implicit and
list the caller. That design was the defect: implicit mode was a migration
mechanism, and a migration mechanism that anybody can reach by importing a
module is the production path. Now:

- `plan_write` refuses an authoritative write outside an operation, before a
  byte lands and before a ledger row exists. It is the one point every
  `safe_write` passes, so a caller that skips `memory.py` is refused too.
- `memory`'s public writers refuse first, with a message that names the fix.
- Implicit mode survives only inside `allow_implicit_for_migration(reason)`,
  and the reason is recorded on the row.
- An explicit project that is not a valid identifier raises; it is never
  silently stamped `unknown`, and never silently lowercased — a subject is a
  publication control, and canonicalising a typo manufactures authority.
- `--project` after the subcommand belongs to the subcommand.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


@pytest.fixture
def mem(root, monkeypatch):
    """`scripts.memory` pointed at the throwaway store.

    `memory` binds `PROJECT_ROOT` at import, so the environment variable alone
    does not move it; the module attribute is redirected instead.
    """
    import scripts.memory as memory
    import scripts.question_routing as qr
    monkeypatch.setattr(memory, "PROJECT_ROOT", root)
    # `add_question` routes through its own PROJECT_ROOT. Left on the live repo,
    # its write is refused by the path guard before the ledger is asked, and
    # the backstop is never exercised — found by removing memory's own guard
    # and watching this case alone stop raising.
    monkeypatch.setattr(qr, "PROJECT_ROOT", root)
    return memory


def _rows(root) -> list:
    conn = sqlite3.connect(kr.db_path(root))
    try:
        return conn.execute("SELECT mutation_id FROM mutations").fetchall()
    finally:
        conn.close()


def _snapshot(root):
    """Everything a refused write must leave untouched."""
    files = sorted(str(p.relative_to(root)) for p in (root / "wiki").rglob("*")
                   if p.is_file())
    return files, kr.current(root).revision, len(_rows(root))


# ── the backstop: plan_write ──────────────────────────────────────────────────

def test_a_write_outside_any_operation_is_refused(root):
    from scripts.safe_write import write_file_safely
    before = _snapshot(root)
    with pytest.raises(kr.KnowledgeOperationRequired):
        write_file_safely(str(root / "wiki" / "page.md"), "x")
    assert _snapshot(root) == before
    assert not (root / "wiki" / "page.md").exists()


def test_an_append_outside_any_operation_is_refused_and_leaves_the_file(root):
    from scripts.safe_write import append_file_safely
    log = root / "wiki" / "log.md"
    log.write_text("# Log\n")
    before = _snapshot(root)
    with pytest.raises(kr.KnowledgeOperationRequired):
        append_file_safely(str(log), "\n- entry")
    assert log.read_text() == "# Log\n"
    assert _snapshot(root) == before


def test_the_refusal_names_the_way_out(root):
    from scripts.safe_write import write_file_safely
    with pytest.raises(kr.KnowledgeOperationRequired) as exc:
        write_file_safely(str(root / "wiki" / "page.md"), "x")
    message = str(exc.value)
    assert "zurvan decision add" in message
    assert "operation(" in message


def test_a_write_outside_the_wiki_is_not_the_ledgers_business(root):
    """Scope is unchanged: only authoritative paths need an operation."""
    from scripts.safe_write import write_file_safely
    assert write_file_safely(str(root / "data" / "scratch.md"), "x")
    assert _rows(root) == []


def test_a_write_inside_an_operation_still_commits_once(root):
    from scripts.safe_write import write_file_safely, append_file_safely
    rev = kr.current(root).revision
    with kr.operation(writer="test", root=root):
        write_file_safely(str(root / "wiki" / "page.md"), "x")
        append_file_safely(str(root / "wiki" / "log.md"), "\n- entry")
    assert kr.current(root).revision == rev + 1


# ── the escape hatch is explicit, reasoned and scoped ────────────────────────

def test_a_migration_may_still_write_implicitly_and_says_why(root):
    from scripts.safe_write import write_file_safely
    with kr.allow_implicit_for_migration("re-stamp legacy decisions"):
        assert write_file_safely(str(root / "wiki" / "page.md"), "x")
    [m] = kr.implicit_mutations(root)
    assert m.implicit_reason == "re-stamp legacy decisions"


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_the_escape_hatch_demands_a_reason(root, reason):
    with pytest.raises(ValueError):
        with kr.allow_implicit_for_migration(reason):
            pass


def test_the_escape_hatch_closes_behind_itself(root):
    from scripts.safe_write import write_file_safely
    with kr.allow_implicit_for_migration("one write"):
        write_file_safely(str(root / "wiki" / "a.md"), "x")
    with pytest.raises(kr.KnowledgeOperationRequired):
        write_file_safely(str(root / "wiki" / "b.md"), "x")


def test_an_operation_inside_the_escape_hatch_is_not_implicit(root):
    """The hatch permits implicit writes; it does not create them."""
    from scripts.safe_write import write_file_safely
    with kr.allow_implicit_for_migration("irrelevant"):
        with kr.operation(writer="test", root=root):
            write_file_safely(str(root / "wiki" / "a.md"), "x")
    assert kr.implicit_mutations(root) == []


def test_a_live_ledger_gains_the_reason_column_without_losing_rows(tmp_path):
    """The production ledger predates the column. Migrating it must keep every
    historical row, and the forensic implicit rows most of all."""
    (tmp_path / "data").mkdir()
    kr.ensure_schema(tmp_path)
    conn = sqlite3.connect(kr.db_path(tmp_path))
    conn.execute("ALTER TABLE mutations DROP COLUMN implicit_reason")
    conn.execute(
        "INSERT INTO mutations (mutation_id, base_revision, state, writer,"
        " implicit, caller, owner_identity, started_at)"
        " VALUES ('mold', 0, 'COMMITTED', 'implicit', 1,"
        " 'scripts.memory:add_decision', '1:x', '2026-09-23')")
    conn.commit()
    conn.close()

    kr.ensure_schema(tmp_path)

    [m] = kr.implicit_mutations(tmp_path)
    assert m.mutation_id == "mold"
    assert m.implicit_reason is None


def test_a_resolution_can_say_what_actually_happened(root):
    """The 2026-09-23 rows were not "migrated to an explicit operation" — the
    caller was an external agent that nobody migrated; the door was closed."""
    from scripts.safe_write import write_file_safely
    with kr.allow_implicit_for_migration("test"):
        write_file_safely(str(root / "wiki" / "a.md"), "x")
    caller = kr.implicit_mutations(root)[0].caller
    kr.resolve_implicit(caller, actor="local:test", root=root,
                        reason="path closed: see forensics")
    conn = sqlite3.connect(kr.db_path(root))
    try:
        [(reason,)] = conn.execute(
            "SELECT resolution_reason FROM mutations").fetchall()
    finally:
        conn.close()
    assert reason == "path closed: see forensics"
    assert kr.implicit_mutations(root) == []


# ── the library: memory's public writers ─────────────────────────────────────

WRITERS = {
    "add_decision": lambda m: m.add_decision("T", "R", "accepted", ["t"]),
    "add_note": lambda m: m.add_note("T", "B", ["t"]),
    "add_question": lambda m: m.add_question("Q?", "R", ["t"]),
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_library_write_with_no_operation_is_refused(mem, root, name):
    """The 2026-09-23 call shape, reproduced: import and call, no boundary."""
    before = _snapshot(root)
    with pytest.raises(kr.KnowledgeOperationRequired) as exc:
        WRITERS[name](mem)
    assert name in str(exc.value)
    assert _snapshot(root) == before


def test_a_claim_with_no_operation_is_refused_before_it_reads_the_source(
        mem, root):
    source = root / "src.md"
    source.write_text("the quote")
    before = _snapshot(root)
    with pytest.raises(kr.KnowledgeOperationRequired):
        mem.add_claim("T", str(source), "the quote", "high", [])
    assert _snapshot(root) == before


def test_a_decision_and_its_log_line_are_one_revision(mem, root):
    rev = kr.current(root).revision
    with kr.operation(writer="test", root=root):
        assert mem.add_decision("T", "R", "accepted", ["t"], project="clientx")
    assert kr.current(root).revision == rev + 1
    assert (root / "wiki" / "log.md").read_text().count("Decision Added") == 1


def test_a_note_and_its_log_line_are_one_revision(mem, root):
    rev = kr.current(root).revision
    with kr.operation(writer="test", root=root):
        assert mem.add_note("T", "B", ["t"])
    assert kr.current(root).revision == rev + 1


# ── the subject: fail loudly, never canonicalise ─────────────────────────────

@pytest.mark.parametrize("bad", ["ClientX", "clientx project", "../x", "a" * 65])
def test_an_invalid_explicit_project_writes_nothing(mem, root, bad):
    before = _snapshot(root)
    with kr.operation(writer="test", root=root):
        with pytest.raises(mem.InvalidProjectError):
            mem.add_decision("T", "R", "accepted", [], project=bad)
    assert _snapshot(root) == before


def test_the_invalid_project_error_suggests_without_deciding(mem):
    with pytest.raises(mem.InvalidProjectError) as exc:
        mem.resolve_project("ClientX")
    assert "'clientx'" in str(exc.value)


def test_no_explicit_project_still_resolves_to_unknown_outside_a_workspace(
        mem, tmp_path, monkeypatch):
    """`unknown` remains the honest answer when nobody named a subject — it is
    only an explicit, malformed one that must not collapse into it."""
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(config))
    elsewhere = tmp_path / "not-a-workspace"
    elsewhere.mkdir()
    assert mem.resolve_project(None, root=elsewhere) == "unknown"


# ── the CLI: `--project` after the subcommand belongs to the subcommand ─────

@pytest.mark.parametrize("argv, expected", [
    (["--project", "x", "search", "q"], ("x", ["search", "q"])),
    (["decision", "add", "--project", "clientx", "--title", "T"],
     (None, ["decision", "add", "--project", "clientx", "--title", "T"])),
    (["--project", "x", "decision", "add", "--project", "clientx"],
     ("x", ["decision", "add", "--project", "clientx"])),
    (["search", "q"], (None, ["search", "q"])),
])
def test_only_a_leading_project_flag_switches_workspace(argv, expected):
    from scripts.cli import _split_global_project
    assert _split_global_project(list(argv)) == expected


def _cli(store, config, *args):
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO),
           "ZURVAN_CONFIG_DIR": str(config)}
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), *args],
        cwd=str(store), env=env, capture_output=True, text=True)


@pytest.fixture
def store(tmp_path):
    store = tmp_path / "Zurvan"
    (store / "wiki" / "decisions").mkdir(parents=True)
    (store / "data").mkdir()
    config = tmp_path / "config"
    config.mkdir()
    return store, config


def test_decision_add_project_reaches_the_page_as_one_revision(store):
    """The command the 2026-09-23 agent needed and could not have."""
    store, config = store
    result = _cli(store, config, "decision", "add", "--title", "T",
                  "--reason", "R", "--status", "accepted", "--project", "clientx")
    assert result.returncode == 0, result.stderr
    page = next((store / "wiki" / "decisions").glob("*.md"))
    assert 'project: "clientx"' in page.read_text()
    [m] = kr.mutations_in(kr.COMMITTED, store)
    assert m.store_project == "clientx"
    assert kr.current(store).revision == 1
    assert kr.implicit_mutations(store) == []


def test_decision_add_with_a_malformed_project_fails_and_writes_nothing(store):
    store, config = store
    result = _cli(store, config, "decision", "add", "--title", "T",
                  "--reason", "R", "--status", "accepted", "--project", "ClientX")
    assert result.returncode != 0
    assert "'clientx'" in (result.stdout + result.stderr)
    assert list((store / "wiki" / "decisions").glob("*.md")) == []
    assert not (store / "wiki" / "log.md").exists()
    assert kr.mutations_in(kr.COMMITTED, store) == []


def test_the_mcp_tool_reports_a_malformed_project_and_writes_nothing(
        mem, root, monkeypatch):
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    from scripts.mcp_tools import tool_zurvan_decision_add
    before = _snapshot(root)
    out = tool_zurvan_decision_add("T", "R", "accepted", [], project="ClientX")
    assert out.startswith("Error:") and "'clientx'" in out
    assert _snapshot(root) == before


# ── the other standalone entry points carry their own boundary ───────────────

@pytest.mark.parametrize("script", ["ingest.py", "extract.py"])
def test_a_standalone_writer_opens_an_operation(script):
    """`ingest.py` and `extract.py` run as `__main__` from the e2e scripts and
    reach `wiki_merge` -> `safe_write`. Without a boundary of their own they
    would now be refused; with one, each run is one revision."""
    source = (REPO / "scripts" / script).read_text()
    main_block = source[source.index('if __name__ == "__main__":'):]
    assert "operation(" in main_block


# ── the wrong interpreter says so, instead of sending the caller elsewhere ───

def test_a_missing_dependency_names_the_launcher_not_a_traceback(tmp_path):
    """`PYTHONPATH=. python3 scripts/cli.py` under Homebrew's python3 died with
    `ModuleNotFoundError: yaml` on 2026-09-23 — the first step of the path that
    ended in a direct library import. `scripts/zurvan` already picks an
    interpreter that has the dependencies; the error should say so."""
    code = ("import sys, runpy; sys.modules['yaml'] = None; "
            f"sys.argv = ['cli.py', '--help']; "
            f"runpy.run_path({str(REPO / 'scripts' / 'cli.py')!r}, run_name='__main__')")
    result = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                            env={**os.environ, "PYTHONPATH": str(REPO)},
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "scripts/zurvan" in result.stderr
    assert "Traceback" not in result.stderr
