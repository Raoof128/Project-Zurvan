"""IOU-10: graph reads open the store read-only and never create it.

`graph_query.get_stats` used `sqlite3.connect(path)` (read-WRITE), so with
`data/` present and the graph missing it CREATED an empty `data/graph.sqlite`
and then failed with "no such table: nodes". That empty file is why the
worktree's own `test_tool_zurvan_graph_stats_struct` failed: an earlier call
had planted it. Every other reader in the codebase honours `mode=ro`.
"""
import sqlite3

import pytest

from scripts import graph_query


@pytest.mark.parametrize("call", [
    lambda p: graph_query.get_stats(p),
    lambda p: graph_query.get_node("wiki/x.md", p),
    lambda p: graph_query.get_neighbours("wiki/x.md", 1, p),
])
def test_graph_reads_never_create_the_store(tmp_path, call):
    db = tmp_path / "data" / "graph.sqlite"
    db.parent.mkdir()
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        call(str(db))
    assert not db.exists(), "a read created the graph store"


def test_trace_node_never_creates_the_store(tmp_path):
    db = tmp_path / "data" / "graph.sqlite"
    db.parent.mkdir()
    try:
        graph_query.trace_node("wiki/x.md", 2, str(db))
    except sqlite3.OperationalError:
        pass
    assert not db.exists(), "trace_node created the graph store"


def test_graph_reads_still_work_on_a_real_store(tmp_path):
    db = tmp_path / "graph.sqlite"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE nodes (id TEXT PRIMARY KEY, path TEXT, title TEXT, type TEXT);"
                    "CREATE TABLE edges (id TEXT PRIMARY KEY, source TEXT, target TEXT, type TEXT);"
                    "INSERT INTO nodes VALUES ('a', 'wiki/a.md', 'A', 'page');")
    c.commit()
    c.close()
    assert graph_query.get_stats(str(db)) == {"nodes": 1, "edges": 0}
