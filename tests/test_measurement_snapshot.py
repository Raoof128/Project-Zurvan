"""Measurements read a frozen input; the shared store stays live and writable.

Zurvan is cross-project memory — the MCP server runs with
`ZURVAN_MCP_READONLY=0` so a session in any repository can record a decision.
Zurvan also measures itself against that same store. On 2026-09-17 at 14:57 a
session working on an unrelated project wrote one note, it landed inside
`check.sh`'s pytest window, the guard fired, and a twenty-minute gate aborted
with nothing wrong in any code under test. It happened again on 2026-09-18 at
15:21. Both times the guard was right and the gate was still useless.

The invariant these tests pin:

> **Shared knowledge may remain live and writable; measurements must not depend
> on the shared store remaining stationary.**

Freezing Zurvan to measure Zurvan is the wrong lever. Freeze the input.

The mechanism is cheap because of something already true: a rebuild builds into
a temp file and promotes it with one `os.replace()`, so the canonical path gets
a NEW INODE and the old bytes are never mutated in place. A hard link taken
before the swap therefore keeps pointing at exactly the bytes that existed at
capture time, for free, while production moves on.

What is NOT acceptable as a solution, and is tested for: blocking writers. A
twenty-minute lock on a shared brain every time someone runs the suite gets
routed around within a week.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from scripts import measurement_snapshot as snap
from scripts.index_health import inspect_index
from scripts.rebuild_search_index import rebuild_search_index


# ── fixtures ─────────────────────────────────────────────────────────────────

def _page(root: Path, name: str, body: str) -> Path:
    wiki = root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    p = wiki / name
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway tree standing in for the live store, for one test.

    `PROJECT_ROOT` moves here so the corpus scan finds this wiki, and
    `ZURVAN_SEARCH_DB` points the index somewhere disposable. Both are needed:
    without the redirect the index path under a patched PROJECT_ROOT is
    CANONICAL, and rebuilding a canonical index under the mock provider — or
    from inside a test run at all — is refused, by two guards added on
    2026-09-17 after a mutation test deleted the production index. Those guards
    are correct; this fixture works with them rather than around them.
    """
    from scripts import config

    (tmp_path / "data").mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "data" / "index.sqlite"))
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")

    _page(tmp_path, "citation.md",
          "# Citation Rules\n\nEvery citation needs a source and a verbatim quote.\n")
    _page(tmp_path, "provenance.md",
          "# Provenance\n\nA claim without provenance is an assertion.\n")
    _page(tmp_path, "boundary.md",
          "# Boundary\n\nThe egress gate scans staged blobs before they leave.\n")
    rebuild_search_index(root=tmp_path)
    return tmp_path


def _canonical(root: Path) -> Path:
    """The live index for this fixture — whatever `ZURVAN_SEARCH_DB` names."""
    return Path(os.environ["ZURVAN_SEARCH_DB"])


def _external_write_and_rebuild(root: Path, name: str, body: str) -> None:
    """What another project's session actually does: write a page, reindex."""
    _page(root, name, body)
    rebuild_search_index(root=root)


def _sources_in(index: Path) -> set:
    conn = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT source_path FROM chunks")}
    finally:
        conn.close()


# ── identity ─────────────────────────────────────────────────────────────────

def test_a_snapshot_records_the_identity_of_what_it_froze(store):
    """A result that cannot name its input is not a measurement.

    Three retrieval baselines in this project carry chunk counts of 184,002 /
    184,231 / 184,241 and no way to tell whether they measured the same corpus.
    """
    with snap.capture(root=store, label="test") as s:
        ident = s.identity

    for field in ("snapshot_id", "index_inode", "chunks", "sources",
                  "chunk_id_set_sha", "embedding_space", "corpus_manifest_sha",
                  "captured_at"):
        assert field in ident, f"identity is missing {field}"
    assert ident["chunks"] > 0
    assert ident["sources"] == 3
    assert ident["index_inode"] == _canonical(store).stat().st_ino


def test_two_snapshots_of_an_unchanged_store_agree_on_everything_but_their_id(store):
    with snap.capture(root=store, label="a") as a, snap.capture(root=store, label="b") as b:
        for field in ("index_inode", "chunks", "sources", "chunk_id_set_sha",
                      "corpus_manifest_sha", "embedding_space"):
            assert a.identity[field] == b.identity[field], field
        assert a.snapshot_id != b.snapshot_id


def test_capturing_without_an_index_fails_loudly(tmp_path, monkeypatch):
    """Fail closed. A snapshot of nothing must not read as a snapshot of clean."""
    from scripts import config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    with pytest.raises(FileNotFoundError):
        snap.capture(root=tmp_path, label="empty")


# ── the required regression: A stays A while the store becomes B ─────────────

def test_a_measurement_keeps_measuring_a_while_the_live_store_moves_to_b(store):
    """The eight steps, in one test, through the real rebuild path.

    1. live state A            5. results still derive entirely from A
    2. capture a snapshot      6. B is present in the live store
    3. external write -> B     7. no A/B mixture
    4. keep measuring          8. the NEXT measurement sees B
    """
    # 1 + 2
    canonical = _canonical(store)
    a_inode = canonical.stat().st_ino
    with snap.capture(root=store, label="gate") as s:
        a_ident = dict(s.identity)
        a_sources = _sources_in(s.index_path)
        assert len(a_sources) == 3

        # 3 — a real external-style write plus a real rebuild
        _external_write_and_rebuild(
            store, "intruder.md",
            "# Intruder\n\nWritten by another project's session mid-gate.\n")
        b_inode = canonical.stat().st_ino
        assert b_inode != a_inode, (
            "the rebuild reused the inode; the whole mechanism depends on "
            "os.replace() promoting a NEW one")

        # 4 + 5 — the measurement continues, against A
        assert s.index_path.stat().st_ino == a_inode
        assert _sources_in(s.index_path) == a_sources
        assert inspect_index(s.index_path)["chunks"] == a_ident["chunks"]
        assert s.identity == a_ident, "the snapshot's identity moved under it"
        assert not any("intruder" in p for p in _sources_in(s.index_path)), (
            "state B leaked into the frozen measurement input")

        # 6 — B is really there, in the live store and the canonical index
        assert (store / "wiki" / "intruder.md").is_file()
        b_sources = _sources_in(canonical)
        assert any("intruder" in p for p in b_sources)

        # 7 — no mixture, in either direction
        assert a_sources < b_sources
        assert len(a_sources) == 3 and len(b_sources) == 4

    # 8 — the next measurement sees B
    with snap.capture(root=store, label="gate2") as s2:
        assert any("intruder" in p for p in _sources_in(s2.index_path))
        assert s2.identity["chunks"] != a_ident["chunks"]
        assert s2.identity["chunk_id_set_sha"] != a_ident["chunk_id_set_sha"]


def test_a_snapshot_survives_repeated_rebuilds_of_the_canonical_index(store):
    """Not one promotion — several, the way a long gate actually runs."""
    with snap.capture(root=store, label="long") as s:
        frozen = _sources_in(s.index_path)
        for i in range(3):
            _external_write_and_rebuild(store, f"later-{i}.md",
                                        f"# Later {i}\n\nMore content arrived.\n")
            assert _sources_in(s.index_path) == frozen, (
                f"the frozen input changed after rebuild {i}")
        assert len(_sources_in(_canonical(store))) == len(frozen) + 3


# ── cost ─────────────────────────────────────────────────────────────────────

def test_a_snapshot_is_a_hard_link_and_copies_nothing(store):
    """O(1) and no extra disk: the index is 738 MB on this machine."""
    canonical = _canonical(store)
    before_nlink = canonical.stat().st_nlink
    with snap.capture(root=store, label="cheap") as s:
        assert s.index_path.stat().st_ino == canonical.stat().st_ino
        assert canonical.stat().st_nlink == before_nlink + 1, (
            "the snapshot is a copy, not a link")
    assert canonical.stat().st_nlink == before_nlink


# ── release and cleanup ──────────────────────────────────────────────────────

def test_releasing_a_snapshot_never_touches_the_canonical_index(store):
    canonical = _canonical(store)
    before = (canonical.stat().st_ino, canonical.stat().st_size)
    s = snap.capture(root=store, label="release")
    s.release()
    assert canonical.is_file()
    assert (canonical.stat().st_ino, canonical.stat().st_size) == before
    assert not s.path.exists()


def test_a_snapshot_is_released_when_the_measurement_raises(store):
    s = None
    with pytest.raises(ValueError):
        with snap.capture(root=store, label="boom") as captured:
            s = captured
            raise ValueError("measurement failed")
    assert not s.path.exists(), "an aborted gate leaked its snapshot"
    assert _canonical(store).is_file()


def test_releasing_twice_is_not_an_error(store):
    s = snap.capture(root=store, label="twice")
    s.release()
    s.release()


def test_abandoned_snapshots_from_dead_processes_are_swept(store):
    """SIGKILL cannot clean up after itself, so these accumulate."""
    ghost = snap.snapshots_dir(store) / "gate-20260101T000000Z-999999"
    ghost.mkdir(parents=True)
    os.link(_canonical(store), ghost / "search.sqlite")

    swept = snap.sweep_abandoned(root=store)

    assert ghost.name in [Path(p).name for p in swept]
    assert not ghost.exists()
    assert _canonical(store).is_file(), "the sweep followed a link to the real index"


def test_the_sweep_leaves_a_live_process_snapshot_alone(store):
    with snap.capture(root=store, label="mine") as s:
        swept = snap.sweep_abandoned(root=store)
        assert s.path.exists(), "the sweep deleted a snapshot in active use"
        assert s.path.name not in [Path(p).name for p in swept]


# ── writers are never blocked ────────────────────────────────────────────────

def test_an_open_snapshot_does_not_block_writers(store):
    """The explicit non-goal. A global lease is the wrong tool for a test run."""
    with snap.capture(root=store, label="nolock"):
        _page(store, "written-during.md", "# During\n\nA write landed anyway.\n")
        rebuild_search_index(root=store)
    assert (store / "wiki" / "written-during.md").is_file()
    assert any("written-during" in p for p in _sources_in(_canonical(store)))


# ── the measurement actually reads the snapshot ──────────────────────────────

def test_hybrid_search_can_be_pointed_at_a_snapshot(store):
    from scripts.hybrid_search import search_hybrid

    with snap.capture(root=store, label="search") as s:
        _external_write_and_rebuild(
            store, "provenance-extra.md",
            "# Provenance Extra\n\nA claim without provenance is an assertion, "
            "repeated verbatim so it would rank if it were visible.\n")

        frozen = search_hybrid("provenance assertion", limit=10,
                               db_path=str(s.index_path))
        live = search_hybrid("provenance assertion", limit=10,
                             db_path=str(_canonical(store)))

    assert not any("provenance-extra" in r["source_path"] for r in frozen), (
        "the frozen retrieval saw a page written after capture")
    assert any("provenance-extra" in r["source_path"] for r in live)


def test_eval_search_scores_against_the_index_it_is_given(store, tmp_path, capsys, monkeypatch):
    """`eval_search --index` is what lets a gate hand a snapshot to the scorer."""
    import json

    from scripts import eval_datasets, eval_search

    # `eval_datasets.REGISTRY` is bound at import from PROJECT_ROOT. Imported for the
    # first time under this fixture's patched root, it pointed at <tmp>/eval/datasets.json
    # and the test failed alone; imported earlier in a full run, it pointed at the repo's.
    # Pin it to the tracked registry so the result no longer depends on test order.
    monkeypatch.setattr(eval_datasets, "REGISTRY",
                        Path(__file__).resolve().parents[1] / "eval" / "datasets.json")

    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({
        "query": "citation source verbatim quote",
        "expected_paths": [str((store / "wiki" / "citation.md").resolve())],
    }) + "\n", encoding="utf-8")

    with snap.capture(root=store, label="eval") as s:
        metrics = eval_search.run_search_evaluation(
            gold_file=str(gold), hybrid=True, as_json=True,
            index_path=str(s.index_path))

    assert metrics["queries"] == 1
    assert metrics["index_path"] == str(s.index_path), (
        "the scorer did not report which index produced the number")


# ── components: which bytes get frozen ───────────────────────────────────────

def _graph(root: Path) -> Path:
    """A small graph store, so a full capture has all three components."""
    path = root / "data" / "graph.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO nodes VALUES (1)")
    conn.commit()
    conn.close()
    return path


def test_the_default_capture_is_unchanged(store):
    """The contracts above must not move. A step that silently changed what
    every caller captures would be a behaviour change wearing a refactor's
    clothes — and `check.sh` gate 6 calls this on every run."""
    with snap.capture(root=store, label="default") as s:
        assert set(s.identity["components"]) == {"search"}
        # Absence is the record. A default capture froze no wiki, and nothing
        # in the manifest may let that be read as "captured and verified".
        assert "wiki" not in s.identity["components"]
        assert not (s.path / "wiki").exists()


def test_an_authoritative_capture_freezes_the_wiki(store):
    with snap.capture(root=store, label="auth", components=("wiki",)) as s:
        record = s.identity["components"]["wiki"]
        assert record["capture_backend"] in ("clonefile", "copy")
        assert record["captured_scope"] == "full_tree"
        assert record["authoritative_scope"] == "excludes_derived_subtrees"
        assert record["coherence_verified"] is False
        assert (s.path / "wiki" / "citation.md").is_file()


def test_a_full_capture_freezes_all_three(store):
    _graph(store)
    with snap.capture(root=store, label="full",
                      components=("wiki", "search", "graph")) as s:
        assert set(s.identity["components"]) == {"wiki", "search", "graph"}
        assert (s.path / "wiki").is_dir()
        assert (s.path / "graph.sqlite").is_file()


def test_an_unknown_component_fails_closed(store):
    """Silently skipping an unrecognised name would let a caller believe it had
    frozen something it had not."""
    with pytest.raises(snap.SnapshotComponentError):
        snap.capture(root=store, label="bad", components=("claims",))


def test_duplicate_component_names_are_refused(store):
    with pytest.raises(snap.SnapshotComponentError):
        snap.capture(root=store, label="dupe", components=("wiki", "wiki"))


def test_release_removes_every_component(store):
    """A cloned wiki is real directory entries. Leaking one per capture would
    fill the disk with metadata even though the data blocks are shared."""
    s = snap.capture(root=store, label="rel", components=("wiki", "search"))
    path = s.path
    s.release()

    assert not path.exists()
    assert (store / "wiki" / "citation.md").is_file()


def test_a_snapshot_is_published_by_rename_not_filled_in_place(store):
    """A crash mid-capture must not leave a directory under the FINAL name
    holding some of the components — indistinguishable from a whole snapshot.
    Captured into `.building-<pid>`, published with one rename."""
    seen = {}
    real = snap.capture_wiki

    def _observe(root, into):
        seen["directory"] = Path(into).name
        return real(root, into)

    snap._COMPONENTS["wiki"] = _observe
    try:
        s = snap.capture(root=store, label="pub", components=("wiki",))
    finally:
        snap._COMPONENTS["wiki"] = real
    try:
        assert ".building-" in seen["directory"]
        assert ".building-" not in s.path.name
    finally:
        s.release()


def test_the_manifest_is_written_into_the_snapshot(store):
    """A snapshot that cannot describe itself without the process that made it
    is not replayable."""
    import json as _json
    with snap.capture(root=store, label="man", components=("search",)) as s:
        manifest = _json.loads((s.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == s.snapshot_id
    assert set(manifest["components"]) == {"search"}


def test_a_partial_capture_leaves_nothing_behind(store):
    """Design §7.1: any failure discards the partial capture. A half-captured
    snapshot directory that survived would be indistinguishable from a whole
    one — and would be published under the final name."""
    with pytest.raises(snap.SnapshotComponentError):
        snap.capture(root=store, label="partial", components=("wiki", "graph"))

    leftovers = list((store / "data" / "snapshots").glob("*"))
    assert leftovers == [], leftovers
