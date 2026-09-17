"""The canonical search index must survive interruption, and must never be mock.

Two independent failures landed here on 2026-09-16/17 and between them left the
project with no usable index:

1. **Poisoning.** `get_embedding` falls back to the deterministic-hash provider
   when `ZURVAN_EMBED_PROVIDER` is unset *or* when SentenceTransformers fails to
   load, and it does so with a printed warning and a successful return. A
   canonical rebuild started from an unpinned shell (`check.sh` gate 5 pins
   nothing) therefore replaced 184,231 real vectors with hashes. The index still
   passed every count-based check: semantic recall was 0% and nothing said so.

2. **Interruption.** `_PriorIndex` moved the healthy index to `<db>.prev` and
   built the replacement AT the canonical path, so the canonical path held a
   partial index from the first written row onward. A SIGKILL 43 seconds in left
   2,000 of 184,231 chunks — structurally valid, 59 of 4,418 sources present.

The invariants these tests pin: a canonical rebuild is atomic (the old index is
readable until the new one is complete and validated), a canonical rebuild
cannot use mock vectors, and "healthy" means provider, model, dimension and
row-count agreement — not a row count alone.
"""

import json
import os
import sqlite3
import sys
import hashlib
from pathlib import Path

import pytest

from scripts.config import PROJECT_ROOT
from scripts.index_health import (
    CANONICAL_DIMENSION,
    CANONICAL_MODEL,
    CANONICAL_PROVIDER,
    check_index_health,
    is_canonical_index,
)
from scripts.rebuild_search_index import rebuild_search_index
import scripts.rebuild_search_index as rsi


CANONICAL_DB = PROJECT_ROOT / "data" / "search.sqlite"


# ── fixtures ──────────────────────────────────────────────────────────────────

def _corpus(root: Path) -> Path:
    """A three-page wiki. Runtime must depend on this, never on the live store."""
    wiki = root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "citation.md").write_text(
        "# Citation Rules\n\nEvery citation needs a source and a verbatim quote.\n",
        encoding="utf-8",
    )
    (wiki / "provenance.md").write_text(
        "# Provenance\n\nA claim without provenance is an assertion.\n",
        encoding="utf-8",
    )
    (wiki / "boundary.md").write_text(
        "# Boundary\n\nThe egress gate scans staged blobs before they leave.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def mock_env(monkeypatch):
    """Mock embeddings, which every non-canonical index in the suite uses."""
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(db: Path) -> tuple[int, int]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0],
        )
    finally:
        conn.close()


# ── 1. SessionStart cannot initiate a rebuild ─────────────────────────────────

def test_session_start_hook_does_not_pass_fix_stale():
    """The hook that actually runs on every session start, read from disk."""
    settings = json.loads((PROJECT_ROOT / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for entry in settings.get("hooks", {}).get("SessionStart", [])
        for hook in entry.get("hooks", [])
    ]
    assert commands, "SessionStart hook missing — this test is checking nothing"
    for command in commands:
        assert "--fix-stale" not in command, (
            "SessionStart must report staleness, never repair it: a rebuild "
            "launched from a hook is killed when the session exits, and on "
            "2026-09-17 that left the canonical index at 2,000 of 184,231 chunks"
        )


def test_agent_prime_never_rebuilds_even_when_the_index_is_stale(monkeypatch):
    """`agent_prime` is the hook's entry point; no argument may reach a rebuild."""
    from scripts import agent_workflow

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("agent_prime attempted an index rebuild")

    monkeypatch.setattr(rsi, "rebuild_search_index", _forbidden)
    monkeypatch.setattr(agent_workflow, "_index_staleness", lambda *a, **k: "STALE — 99 file(s)")

    card = agent_workflow.agent_prime()

    # The claim is that no rebuild was attempted (the monkeypatch above would
    # have raised). The card must still say something about the index, whatever
    # state the developer's own store happens to be in when the suite runs.
    assert "index" in card.lower(), "the orientation card must report on the index"


def test_cli_fix_stale_flag_is_an_accepted_no_op(monkeypatch):
    """An older config may still pass the flag; it must be inert, not fatal."""
    from scripts import agent_workflow

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("--fix-stale reached a rebuild")

    monkeypatch.setattr(rsi, "rebuild_search_index", _forbidden)
    monkeypatch.setattr(agent_workflow, "_index_staleness", lambda *a, **k: "STALE — 99 file(s)")

    assert agent_workflow.agent_prime(fix_stale=True)


# ── 2. A production rebuild cannot silently use the mock provider ─────────────

@pytest.fixture
def disposable_root(tmp_path, monkeypatch):
    """A throwaway tree that IS canonical for the duration of one test.

    The canonical-path guards have to be exercised against a canonical path,
    and until 2026-09-17 that meant the tests pointed at the production index
    and relied on the guards holding. They did not: mutation-testing removed
    them and the "canonical rebuild is refused" test rebuilt and then deleted
    the real index. Production state is not a test fixture — so `PROJECT_ROOT`
    moves here instead, and `data/search.sqlite` under this tmp_path becomes the
    canonical index for everything that resolves it.
    """
    from scripts import config

    (tmp_path / "data").mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    _corpus(tmp_path)
    return tmp_path


def test_canonical_rebuild_refuses_the_mock_provider(disposable_root, monkeypatch):
    """The 2026-09-16 poisoning, reproduced against a disposable canonical root."""
    monkeypatch.delenv("ZURVAN_EMBED_PROVIDER", raising=False)
    monkeypatch.delenv("ZURVAN_EMBED_MODEL", raising=False)
    fake_canonical = disposable_root / "data" / "search.sqlite"
    assert is_canonical_index(fake_canonical), "the fixture must produce a canonical path"

    with pytest.raises(RuntimeError, match="mock"):
        rebuild_search_index(root=disposable_root)

    assert not fake_canonical.exists(), "the guard must refuse BEFORE touching the index"
    assert list((disposable_root / "data").glob("*")) == [], "nothing may be written at all"


def test_no_test_may_rebuild_the_canonical_index_whatever_the_provider(
    disposable_root, monkeypatch
):
    """A second lock, independent of the provider clause.

    Mutation-testing the provider guard removed it, and the test above went on
    to rebuild the production index and delete it on the way out. The guard and
    its test must not share a single point of failure — so a rebuild of ANY
    canonical path is refused while `PYTEST_CURRENT_TEST` is set, even when the
    provider is the correct production one.
    """
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", CANONICAL_PROVIDER)
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", CANONICAL_MODEL)
    monkeypatch.setattr(
        rsi, "get_embedding",
        lambda *_a, **_k: {
            "provider": CANONICAL_PROVIDER, "model": CANONICAL_MODEL,
            "dimension": CANONICAL_DIMENSION, "vector": [0.0] * CANONICAL_DIMENSION,
        },
    )

    with pytest.raises(RuntimeError, match="test run"):
        rebuild_search_index(root=disposable_root)

    assert not (disposable_root / "data" / "search.sqlite").exists()


def test_canonical_rebuild_refuses_a_silent_provider_fallback(tmp_path, monkeypatch):
    """SentenceTransformers failing to load returns mock with a warning and a
    successful return value. Requested != actual must be fatal, not printed."""
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "sentence_transformers")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", CANONICAL_MODEL)
    monkeypatch.setattr(
        rsi, "get_embedding",
        lambda *_a, **_k: {
            "provider": "mock", "model": "deterministic_hash",
            "dimension": 384, "vector": [0.0] * 384,
        },
    )

    with pytest.raises(RuntimeError, match="fell back|mock"):
        rebuild_search_index(str(tmp_path / "search.sqlite"), root=_corpus(tmp_path))


def test_a_throwaway_index_may_still_use_mock(tmp_path, mock_env):
    """The whole suite depends on this: mock is fine anywhere but canonical."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    chunks, embeddings = _counts(db)
    assert chunks > 0 and chunks == embeddings
    assert not is_canonical_index(db)


# ── 3. An interrupted rebuild leaves the previous canonical index intact ──────

def test_interrupted_rebuild_leaves_the_previous_index_usable(tmp_path, mock_env):
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    healthy_digest = _digest(db)
    healthy_counts = _counts(db)

    real_iter = rsi.iter_all_markdown_chunks

    def _dies_partway(root=None):
        for i, chunk in enumerate(real_iter(root)):
            if i >= 1:
                raise KeyboardInterrupt("SIGINT mid-rebuild")
            yield chunk

    monkeypatch_target = rsi
    original = monkeypatch_target.iter_all_markdown_chunks
    monkeypatch_target.iter_all_markdown_chunks = _dies_partway
    try:
        with pytest.raises(KeyboardInterrupt):
            rebuild_search_index(str(db), root=tmp_path)
    finally:
        monkeypatch_target.iter_all_markdown_chunks = original

    assert _digest(db) == healthy_digest, "the canonical index was mutated by a failed rebuild"
    assert _counts(db) == healthy_counts
    assert check_index_health(db, expect_provider="mock").ok
    leftovers = list(tmp_path.glob("search.sqlite.*"))
    assert leftovers == [], f"partial rebuild files left behind: {leftovers}"


def test_a_rebuild_killed_after_a_committed_batch_promotes_nothing(
    tmp_path, mock_env, monkeypatch
):
    """The 2026-09-17 shape exactly: die AFTER at least one commit.

    `COMMIT_EVERY` batching is what made the old failure so convincing — the
    partial index at the canonical path was fully committed and internally
    consistent, just 96% empty. Committed progress must still be unreachable.
    """
    corpus = _corpus(tmp_path)
    db = tmp_path / "data" / "search.sqlite"
    db.parent.mkdir(exist_ok=True)
    monkeypatch.setattr(rsi, "COMMIT_EVERY", 2)

    rebuild_search_index(str(db), root=corpus)
    healthy_digest = _digest(db)
    healthy_counts = _counts(db)
    assert healthy_counts[0] > 2, "the fixture must span more than one commit batch"

    real_iter = rsi.iter_all_markdown_chunks

    def _dies_after_a_commit(root=None):
        for i, chunk in enumerate(real_iter(root)):
            if i >= 2:  # chunks 0 and 1 are already COMMITTED at this point
                raise KeyboardInterrupt("SIGTERM mid-rebuild, after a commit")
            yield chunk

    monkeypatch.setattr(rsi, "iter_all_markdown_chunks", _dies_after_a_commit)
    # Force a full re-embed so the doomed run actually writes rows.
    monkeypatch.setattr(rsi, "_PriorIndex", lambda _p: _NoReuse())

    with pytest.raises(KeyboardInterrupt):
        rebuild_search_index(str(db), root=corpus)

    assert _digest(db) == healthy_digest, "the canonical index changed"
    assert _counts(db) == healthy_counts
    # A reader opening the canonical path sees the complete old index.
    assert check_index_health(db, expect_provider="mock").ok
    assert list((tmp_path / "data").glob("search.sqlite.*")) == []


class _NoReuse:
    """A _PriorIndex that never offers a stored vector."""
    def get(self, _chunk_id):
        return None

    def close(self):
        pass


def test_a_rebuild_refuses_to_start_beside_a_live_one(tmp_path, mock_env):
    """Observed for real on 2026-09-17: a backtick inside a shell audit command
    executed `zurvan index search`, and a second production rebuild ran for ten
    minutes alongside the first. Atomicity meant neither corrupted anything —
    but one of them was pure waste, and `check.sh`'s pgrep guard does not cover
    a rebuild started any other way."""
    db = tmp_path / "search.sqlite"
    Path(f"{db}.building-{os.getpid()}-journal").write_bytes(b"pretend this is another run")

    # The live pid here is this process, which _sweep treats as its own; use a
    # pid that is alive but not us — the parent process serves.
    ppid = os.getppid()
    Path(f"{db}.building-{ppid}").write_bytes(b"in flight")

    with pytest.raises(RuntimeError, match="already running"):
        rebuild_search_index(str(db), root=_corpus(tmp_path))

    assert Path(f"{db}.building-{ppid}").exists(), "the other rebuild's file must survive"


def test_the_sweep_can_never_delete_the_canonical_index(tmp_path, mock_env):
    """Cleanup runs on every rebuild; it must not be able to reach the index."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    digest = _digest(db)

    Path(f"{db}.building-999999").write_bytes(b"abandoned")
    rsi._sweep_abandoned_builds(str(db))

    assert db.exists() and _digest(db) == digest
    assert not Path(f"{db}.building-999999").exists()


def test_a_failed_validation_does_not_promote_the_new_index(tmp_path, mock_env, monkeypatch):
    """Validation is the last gate before the swap; failing it keeps the old index."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    healthy_digest = _digest(db)

    from scripts import index_health

    monkeypatch.setattr(
        rsi, "check_index_health",
        lambda *_a, **_k: index_health.HealthReport(
            ok=False, problems=["synthetic validation failure"], stats={},
        ),
    )

    with pytest.raises(RuntimeError, match="validation"):
        rebuild_search_index(str(db), root=_corpus(tmp_path))

    assert _digest(db) == healthy_digest
    assert list(tmp_path.glob("search.sqlite.*")) == []


def test_the_sweep_removes_dead_builds_and_spares_a_live_one(tmp_path):
    """A SIGKILLed rebuild cannot clean up after itself; a running one must not
    have its files removed by the next rebuild — including its `-journal`,
    whose name does not parse as a bare pid."""
    db = str(tmp_path / "search.sqlite")
    live, dead = os.getpid(), 999_999

    abandoned = Path(f"{db}.building-{dead}")
    abandoned.write_bytes(b"partial")
    live_build = Path(f"{db}.building-{live}-journal")
    live_build.write_bytes(b"in flight")

    rsi._sweep_abandoned_builds(db)

    assert not abandoned.exists(), "a dead rebuild's file must be swept"
    assert live_build.exists(), "a live rebuild's journal must be left alone"


def test_rebuild_does_not_depend_on_a_prev_file(tmp_path, mock_env):
    """`.prev` was correctness-load-bearing; it must not exist at all now."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    assert not (tmp_path / "search.sqlite.prev").exists()
    assert check_index_health(db, expect_provider="mock").ok


def test_embeddings_are_still_reused_across_rebuilds(tmp_path, mock_env, monkeypatch):
    """Reuse must survive the move to a temp build path — without it a real
    canonical rebuild re-embeds 184k chunks instead of reusing them."""
    db = tmp_path / "search.sqlite"
    corpus = _corpus(tmp_path)
    rebuild_search_index(str(db), root=corpus)

    from scripts.embed import get_embedding as real_get_embedding
    calls = {"n": 0}

    def _counting(text, *a, **k):
        calls["n"] += 1
        return real_get_embedding(text, *a, **k)

    monkeypatch.setattr(rsi, "get_embedding", _counting)
    rebuild_search_index(str(db), root=corpus)

    assert calls["n"] == 1, "only the provider probe should embed on an unchanged corpus"


# ── 4. Health is provider + model + dimension + agreement, not a row count ────

def test_a_partial_index_is_not_healthy(tmp_path, mock_env):
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM embeddings WHERE rowid IN (SELECT rowid FROM embeddings LIMIT 1)")
    conn.commit()
    conn.close()

    report = check_index_health(db, expect_provider="mock")
    assert not report.ok
    assert any("embedding" in p for p in report.problems), report.problems


def test_chunks_must_equal_embeddings(tmp_path, mock_env):
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    chunks, embeddings = _counts(db)
    assert chunks == embeddings

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at)"
        " VALUES ('orphan', 'wiki/x.md', 'h', 't', 'c', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    assert not check_index_health(db, expect_provider="mock").ok


def test_the_wrong_provider_fails_health_even_with_perfect_counts(tmp_path, mock_env):
    """The 2026-09-16 index: 184,231 chunks, 184,231 embeddings, all mock."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    chunks, embeddings = _counts(db)
    assert chunks == embeddings, "the poisoned index agreed on counts too"

    report = check_index_health(
        db, expect_provider=CANONICAL_PROVIDER, expect_model=CANONICAL_MODEL,
        expect_dimension=CANONICAL_DIMENSION,
    )
    assert not report.ok
    assert any("provider" in p for p in report.problems), report.problems


def test_the_wrong_model_fails_health(tmp_path, mock_env):
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    conn = sqlite3.connect(db)
    conn.execute("UPDATE embeddings SET provider = ?, model = 'all-MiniLM-L12-v2'",
                 (CANONICAL_PROVIDER,))
    conn.commit()
    conn.close()

    report = check_index_health(
        db, expect_provider=CANONICAL_PROVIDER, expect_model=CANONICAL_MODEL,
    )
    assert not report.ok
    assert any("model" in p for p in report.problems), report.problems


def test_a_mixed_provider_index_fails_health(tmp_path, mock_env):
    """Cosine scores across two embedding spaces are arithmetic, not meaning."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE embeddings SET provider = ?, model = ? "
        "WHERE chunk_id IN (SELECT chunk_id FROM embeddings LIMIT 1)",
        (CANONICAL_PROVIDER, CANONICAL_MODEL),
    )
    conn.commit()
    conn.close()

    report = check_index_health(db, expect_provider="mock")
    assert not report.ok
    assert any("mixed" in p or "more than one" in p for p in report.problems), report.problems


def test_a_wrong_width_vector_fails_deep_health(tmp_path, mock_env):
    """A dimension/blob mismatch makes vec_distance_cosine return garbage."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE embeddings SET vector = ? WHERE chunk_id IN "
        "(SELECT chunk_id FROM embeddings LIMIT 1)", (b"\x00" * 8,)
    )
    conn.commit()
    conn.close()

    report = check_index_health(db, expect_provider="mock", deep=True)
    assert not report.ok
    assert any("width" in p or "byte" in p for p in report.problems), report.problems


def test_mock_vectors_are_never_healthy_unless_explicitly_expected(tmp_path, mock_env):
    """The quarantined 2026-09-16 index: 184,231 rows, every table agreeing,
    every vector a hash. Asked without an expectation, health said "healthy"."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    assert check_index_health(db, expect_provider="mock").ok, \
        "a test that asks for mock must still get a usable verdict"

    unqualified = check_index_health(db)
    assert not unqualified.ok
    assert any("mock" in p for p in unqualified.problems), unqualified.problems


def test_an_empty_index_is_not_healthy(tmp_path):
    db = tmp_path / "search.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, source_path TEXT, "
                 "heading TEXT, text TEXT, content_hash TEXT, indexed_at TEXT)")
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, "
                 "model TEXT, dimension INTEGER, vector BLOB)")
    conn.commit()
    conn.close()

    assert not check_index_health(db).ok


def test_min_sources_catches_a_truncated_corpus(tmp_path, mock_env):
    """2,000 chunks from 59 of 4,418 sources passed every count check there was."""
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))

    assert check_index_health(db, expect_provider="mock", min_sources=3).ok
    report = check_index_health(db, expect_provider="mock", min_sources=4418)
    assert not report.ok
    assert any("source" in p for p in report.problems), report.problems


# ── 5. The live canonical index is off-limits to the suite ───────────────────

def test_the_cli_rebuild_pins_the_production_embedding_space(monkeypatch):
    """`zurvan index search` typed into a plain terminal must still be real.

    The environment is what failed here: a rebuild started where
    ZURVAN_EMBED_PROVIDER was unset produced 184,231 hash vectors. The
    production entry point therefore carries the production space itself rather
    than inheriting whatever shell it was typed into.
    """
    import scripts.cli as cli

    monkeypatch.delenv("ZURVAN_EMBED_PROVIDER", raising=False)
    monkeypatch.delenv("ZURVAN_EMBED_MODEL", raising=False)
    captured = {}

    def _spy(script, extra_args=None, env=None):
        captured["script"], captured["env"] = script, env

    monkeypatch.setattr(cli, "_run_script", _spy)
    monkeypatch.setattr(sys, "argv", ["zurvan", "index", "search"])
    cli.main()

    assert captured["script"] == "rebuild_search_index.py"
    assert captured["env"]["ZURVAN_EMBED_PROVIDER"] == CANONICAL_PROVIDER
    assert captured["env"]["ZURVAN_EMBED_MODEL"] == CANONICAL_MODEL


def test_an_explicit_provider_still_wins_over_the_cli_default(monkeypatch):
    """Pinning a default must not silently override a caller who chose."""
    import scripts.cli as cli

    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    captured = {}
    monkeypatch.setattr(cli, "_run_script",
                        lambda script, extra_args=None, env=None: captured.update(env=env))
    monkeypatch.setattr(sys, "argv", ["zurvan", "index", "search"])
    cli.main()

    # Still mock — and a mock rebuild of the canonical index is refused
    # downstream, which is where that decision belongs.
    assert captured["env"]["ZURVAN_EMBED_PROVIDER"] == "mock"


def test_no_test_starts_a_rebuild_without_isolating_its_target():
    """Production knowledge state is never a test fixture — checked, not trusted.

    Every `rebuild_search_index(...)` call in the suite must either name its
    target explicitly or run inside a test that redirects the root. The
    alternative is what happened on 2026-09-17: a call written as
    `rebuild_search_index()` resolved to the production index, and the only
    thing standing between it and the corpus was the guard the test was
    mutating. `PYTEST_CURRENT_TEST` and the conftest session check are
    defence-in-depth behind this rule, not substitutes for it.
    """
    import ast

    ISOLATION_MARKERS = (
        "ZURVAN_SEARCH_DB", "ZURVAN_ROOT", "PROJECT_ROOT",
        "disposable_root", "tmp_path", "db_path",
    )
    offenders = []

    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name != "rebuild_search_index":
                    continue
                names_a_target = bool(call.args) or any(
                    kw.arg in ("db_path", "root") for kw in call.keywords
                )
                isolated = any(marker in body for marker in ISOLATION_MARKERS)
                if not (names_a_target or isolated):
                    offenders.append(f"{path.name}::{node.name}")

    assert not offenders, (
        "these tests start an index rebuild without naming a disposable target "
        f"or redirecting the root: {offenders}"
    )



def test_reading_a_missing_index_does_not_create_one(tmp_path, monkeypatch):
    """`sqlite3.connect` creates the file it cannot find.

    On 2026-09-17 the orientation card did exactly that and left a 0-byte
    database at the canonical path, which reads as "unreadable" rather than
    "missing" — a broken index impersonating a corrupt one.
    """
    from scripts import agent_workflow

    missing = tmp_path / "data" / "search.sqlite"
    missing.parent.mkdir(parents=True)
    monkeypatch.setattr(agent_workflow, "ROOT", tmp_path)

    verdict = agent_workflow._index_staleness(root=tmp_path)
    agent_workflow.agent_prime()

    assert "missing" in verdict
    assert not missing.exists(), "a read created an empty index file"


def test_no_read_path_creates_or_touches_the_index(tmp_path, monkeypatch):
    """Every read-only consumer, checked by behaviour rather than by grep.

    A reader that opens read-write can create a 0-byte database where none
    exists, and can modify one that does. Both happened: `agent_prime` left an
    empty file at the canonical path on 2026-09-17.
    """
    from scripts import eval_search, hybrid_search, index_health, vector_cache

    absent = tmp_path / "gone" / "search.sqlite"
    absent.parent.mkdir()
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(absent))

    # Each of these resolves the index and must tolerate its absence.
    eval_search._index_uses_mock_embeddings()
    hybrid_search.search_hybrid("anything", db_path=str(absent))
    index_health.check_index_health(absent)
    vector_cache.discard(str(absent))

    assert not absent.exists(), "a read path created the index file"

    # And against an index that DOES exist: reading must not modify it.
    db = tmp_path / "search.sqlite"
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(db))
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    before = _digest(db)

    hybrid_search.search_hybrid("citation", db_path=str(db))
    index_health.check_index_health(db, expect_provider="mock", deep=True)
    eval_search._index_uses_mock_embeddings()

    assert _digest(db) == before, "a read modified the index"


def test_every_index_artefact_is_gitignored():
    """Each of these is a complete copy of the private corpus.

    `data/*.sqlite` matches only names ENDING in `.sqlite`, at the top level of
    `data/`. Rebuild artefacts are named AROUND that suffix, so renaming the
    scheme on 2026-09-17 (`.prev` -> `.building-<pid>`) left a 737 MB index one
    `git add -A` away from being staged — the same class as the private
    statutory declaration caught on 2026-09-16. The rule must match the shape.
    """
    import subprocess

    artefacts = [
        "data/search.sqlite",
        "data/search.sqlite.prev",
        "data/search.sqlite.building-12345",
        "data/search.sqlite.building-12345-journal",
        "data/search.sqlite.building-12345-wal",
        "data/search.sqlite-journal",
        "data/quarantine/2026-09-16-mock-poisoned-search.sqlite",
        "data/anything/nested/search.sqlite",
    ]
    not_ignored = [
        a for a in artefacts
        if subprocess.run(["git", "check-ignore", "-q", a],
                          cwd=str(PROJECT_ROOT)).returncode != 0
    ]

    assert not not_ignored, f"index artefacts that git would happily stage: {not_ignored}"


def test_canonical_path_detection_follows_the_search_db_override(tmp_path, monkeypatch):
    assert is_canonical_index(CANONICAL_DB)
    assert not is_canonical_index(tmp_path / "search.sqlite")

    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "search.sqlite"))
    assert not is_canonical_index(tmp_path / "search.sqlite"), (
        "a redirected index is a throwaway, never the production corpus"
    )
