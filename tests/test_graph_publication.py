"""The graph is published, not edited.

`graph_build.py:186` used to open the canonical `data/graph.sqlite` and DELETE
then INSERT into it — a reader mid-build saw an empty or half-filled graph, and
a hardlink to it was illegal (design §4.4). It is now built at
`graph.sqlite.building-<pid>`, stamped inside, and published by
`sqlite_publish.publish`.
"""

import os
import signal
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.graph_build as gb
from scripts import graph_schema as gs
from scripts import sqlite_publish as sp

REPO = Path(__file__).resolve().parents[1]


def _tree(root: Path) -> Path:
    (root / "wiki" / "decisions").mkdir(parents=True)
    (root / "wiki" / "a.md").write_text("---\ntitle: A\n---\nsee [[b]]\n")
    (root / "wiki" / "b.md").write_text("---\ntitle: B\n---\nplain\n")
    (root / "wiki" / "decisions" / "d.md").write_text(
        "---\ntitle: D\ntype: decision\n---\nabout [[a]]\n")
    return root


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = _tree(tmp_path / "repo")
    monkeypatch.setattr(gb, "PROJECT_ROOT", str(root))
    (tmp_path / "data").mkdir()
    return root, str(tmp_path / "data" / "graph.sqlite")


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_the_first_build_publishes_where_nothing_existed(tree):
    _, db = tree
    gb.build_graph(db)
    assert stat.S_IMODE(os.stat(db).st_mode) == sp.PUBLISHED_MODE
    assert gs.read_graph_meta(db)["node_count"] == "3"


def test_the_graph_is_stamped_inside_the_database(tree):
    _, db = tree
    gb.build_graph(db)
    meta = gs.read_graph_meta(db)
    assert meta["schema_version"] == str(gs.GRAPH_SCHEMA_VERSION)
    assert len(meta["graph_generation_id"]) == 32
    assert meta["built_at"].endswith("+00:00")
    assert set(meta) == {"schema_version", "graph_generation_id", "built_at",
                         "node_count", "edge_count"}


def test_each_publication_is_a_new_generation(tree):
    _, db = tree
    gb.build_graph(db)
    first = gs.read_graph_meta(db)["graph_generation_id"]
    gb.build_graph(db)
    assert gs.read_graph_meta(db)["graph_generation_id"] != first


def test_the_same_corpus_gives_the_same_content(tree):
    """Rebuilding is not a ranking change: the node and edge content is a
    function of the corpus. Timestamps are excluded because
    `extract_node_data` defaults them to now()."""
    _, db = tree
    gb.build_graph(db)
    first = gs.content_digest(db)
    gb.build_graph(db)
    assert gs.content_digest(db) == first


def test_edge_ids_are_random_which_is_why_the_digest_ignores_them(tree):
    """Recorded, not endorsed: `graph_build.py:84` mints uuid4() edge ids, so a
    rebuild renames every edge. If this ever fails, edge ids became
    deterministic - then put edge_id back into content_digest."""
    _, db = tree
    def ids():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[0] for r in conn.execute("SELECT edge_id FROM edges")}
        finally:
            conn.close()
    gb.build_graph(db)
    first = ids()
    gb.build_graph(db)
    assert first and first.isdisjoint(ids())


def test_the_published_graph_is_self_contained(tree):
    _, db = tree
    gb.build_graph(db)
    assert [s for s in sp.SIDECARS if os.path.exists(db + s)] == []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


def test_a_failed_build_leaves_the_published_graph_untouched(tree, monkeypatch):
    root, db = tree
    gb.build_graph(db)
    before = _bytes(db)

    def disk_full(build_path, nodes_dict, edges_list, stamp=None):
        # A real mid-write failure: the build file exists and a write
        # transaction is open when the error lands. Replacing _write_graph
        # without creating the file made the cleanup assertion vacuous — the
        # final review showed the test passing with the cleanup removed.
        conn = sqlite3.connect(build_path)
        conn.execute("CREATE TABLE half (x)")
        conn.execute("BEGIN")
        conn.execute("INSERT INTO half VALUES (1)")
        assert os.path.exists(build_path)
        raise OSError(28, "No space left on device (injected)")
    monkeypatch.setattr(gb, "_write_graph", disk_full)
    (root / "wiki" / "c.md").write_text("---\ntitle: C\n---\nnew\n")

    with pytest.raises(OSError):
        gb.build_graph(db)
    assert _bytes(db) == before
    leftovers = [p for p in os.listdir(os.path.dirname(db)) if sp.BUILD_SUFFIX in p]
    assert leftovers == []


def test_a_second_concurrent_build_refuses(tree):
    _, db = tree
    other = f"{db}{sp.BUILD_SUFFIX}{os.getppid()}"
    open(other, "wb").close()
    try:
        with pytest.raises(RuntimeError, match=str(os.getppid())):
            gb.build_graph(db)
    finally:
        os.remove(other)


def test_a_sigkilled_build_leaves_the_published_graph_untouched(tree, tmp_path):
    """No `finally` runs under SIGKILL. The child builds, then kills itself at
    the last moment before the swap.

    Bound: the sweep decides "dead" with `os.kill(pid, 0)`, so if the OS
    reuses the child's pid before the next build, that build refuses as if a
    builder were live. Inherited from the search sweep; the ledger's
    pid+start-time identity would fix it and is not in scope.
    """
    root, db = tree
    gb.build_graph(db)
    before = _bytes(db)

    child = (
        "import os, signal, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "import scripts.graph_build as gb\n"
        "from scripts import sqlite_publish as sp\n"
        f"gb.PROJECT_ROOT = {str(root)!r}\n"
        "sp._replace = lambda *a: os.kill(os.getpid(), signal.SIGKILL)\n"
        f"gb.build_graph({db!r})\n"
    )
    result = subprocess.run([sys.executable, "-c", child], capture_output=True)
    assert result.returncode == -signal.SIGKILL

    assert _bytes(db) == before
    orphans = [p for p in os.listdir(os.path.dirname(db)) if sp.BUILD_SUFFIX in p]
    assert orphans, "the child should have left its build file behind"

    gb.build_graph(db)                          # the next build sweeps it
    assert [p for p in os.listdir(os.path.dirname(db)) if sp.BUILD_SUFFIX in p] == []


def test_an_in_place_write_to_the_published_graph_fails_loudly(tree):
    _, db = tree
    gb.build_graph(db)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM nodes")
            conn.commit()
    finally:
        conn.close()


def test_a_build_still_collecting_already_blocks_a_second(tree, tmp_path):
    """The final review found a ~3 s window on the live corpus: the build file
    was created only in `_write_graph`, after `_collect_graph` had read every
    page, so a second build started meanwhile passed the sweep. The child
    below holds itself inside `_collect_graph`; the parent must be refused."""
    import time
    root, db = tree
    marker = tmp_path / "collecting"
    child = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "import scripts.graph_build as gb\n"
        f"gb.PROJECT_ROOT = {str(root)!r}\n"
        "def slow_collect(source_root=None):\n"
        f"    open({str(marker)!r}, 'w').close()\n"
        "    time.sleep(30)\n"
        "    return {}, []\n"
        "gb._collect_graph = slow_collect\n"
        f"gb.build_graph({db!r})\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", child])
    try:
        deadline = time.monotonic() + 20
        while not marker.exists():
            assert proc.poll() is None, "child exited before collecting"
            assert time.monotonic() < deadline, "child never started collecting"
            time.sleep(0.05)
        with pytest.raises(RuntimeError, match=str(proc.pid)):
            gb.build_graph(db)
    finally:
        proc.kill()
        proc.wait()
