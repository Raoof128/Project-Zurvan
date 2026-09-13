"""check.sh gate [6/6] — graph queries behind `graph stats/neighbours/trace`.

tests/test_graph_query.py covers the happy path in one broad test. These add
the branches it does not reach: unknown nodes, lookup by each identifier,
isolated nodes, depth limiting, and cycles — a graph built from wikilinks
routinely contains cycles, and an unguarded traversal would not terminate.

Each test builds its own two-to-four node graph in tmp_path.
"""

import sqlite3

import pytest

from scripts.graph_query import get_neighbours, get_node, get_stats, trace_node
from scripts.graph_schema import setup_graph_schema


@pytest.fixture
def graph(tmp_path):
    """A tiny graph builder: returns (db_path, add_node, add_edge)."""
    db_path = str(tmp_path / "graph.sqlite")
    setup_graph_schema(db_path)

    def _exec(sql, params):
        conn = sqlite3.connect(db_path)
        conn.execute(sql, params)
        conn.commit()
        conn.close()

    def add_node(node_id, title=None, path=None, slug=None):
        _exec(
            "INSERT INTO nodes (node_id, title, path, slug) VALUES (?, ?, ?, ?)",
            (node_id, title or node_id, path or f"wiki/{node_id}.md", slug or node_id),
        )

    def add_edge(edge_id, src, dst, edge_type="mentions"):
        _exec(
            "INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type)"
            " VALUES (?, ?, ?, ?)",
            (edge_id, src, dst, edge_type),
        )

    return db_path, add_node, add_edge


# ── get_stats ─────────────────────────────────────────────────────────────────

def test_stats_on_an_empty_graph_are_zero(graph):
    db_path, _, _ = graph
    assert get_stats(db_path) == {"nodes": 0, "edges": 0}


def test_stats_count_nodes_and_edges_separately(graph):
    db_path, add_node, add_edge = graph
    add_node("a")
    add_node("b")
    add_node("c")
    add_edge("e1", "a", "b")

    assert get_stats(db_path) == {"nodes": 3, "edges": 1}


# ── get_node ──────────────────────────────────────────────────────────────────

def test_a_node_is_findable_by_id_path_or_slug(graph):
    """All three spellings are used by callers, so all three must resolve."""
    db_path, add_node, _ = graph
    add_node("n1", title="Node One", path="wiki/decisions/one.md", slug="one")

    for identifier in ("n1", "wiki/decisions/one.md", "one"):
        assert get_node(identifier, db_path)["title"] == "Node One"


def test_an_unknown_node_resolves_to_none(graph):
    db_path, _, _ = graph
    assert get_node("wiki/ghost.md", db_path) is None


# ── get_neighbours ────────────────────────────────────────────────────────────

def test_neighbours_include_both_incoming_and_outgoing_edges(graph):
    db_path, add_node, add_edge = graph
    add_node("a")
    add_node("b")
    add_node("c")
    add_edge("e1", "a", "b")      # outgoing from b's perspective: incoming
    add_edge("e2", "b", "c")      # outgoing

    neighbours = get_neighbours("wiki/b.md", 1, db_path)

    assert len(neighbours) == 2
    assert {n["edge_type"] for n in neighbours} == {"mentions"}


def test_neighbours_of_an_unknown_node_are_empty_rather_than_an_error(graph):
    db_path, _, _ = graph
    assert get_neighbours("wiki/ghost.md", 1, db_path) == []


def test_an_isolated_node_has_no_neighbours(graph):
    db_path, add_node, _ = graph
    add_node("lonely")

    assert get_neighbours("wiki/lonely.md", 1, db_path) == []


def test_neighbour_rows_carry_both_endpoint_titles(graph):
    db_path, add_node, add_edge = graph
    add_node("a", title="Alpha")
    add_node("b", title="Beta")
    add_edge("e1", "a", "b", edge_type="cites")

    row = get_neighbours("wiki/a.md", 1, db_path)[0]

    assert row["from_title"] == "Alpha"
    assert row["to_title"] == "Beta"
    assert row["edge_type"] == "cites"


# ── trace_node ────────────────────────────────────────────────────────────────

def test_tracing_an_unknown_node_visits_nothing(graph):
    db_path, _, _ = graph
    result = trace_node("wiki/ghost.md", 2, db_path)

    assert result["nodes_visited"] == 0
    assert result["edges"] == []
    assert result["start_node"] == "wiki/ghost.md"


def test_trace_follows_a_chain_up_to_the_depth_limit(graph):
    db_path, add_node, add_edge = graph
    for n in ("a", "b", "c", "d"):
        add_node(n)
    add_edge("e1", "a", "b")
    add_edge("e2", "b", "c")
    add_edge("e3", "c", "d")

    shallow = trace_node("wiki/a.md", 1, db_path)
    deeper = trace_node("wiki/a.md", 3, db_path)

    assert shallow["nodes_visited"] < deeper["nodes_visited"]


def test_trace_terminates_on_a_cycle(graph):
    """Wikilink graphs contain cycles; the traversal must not loop forever."""
    db_path, add_node, add_edge = graph
    add_node("a")
    add_node("b")
    add_edge("e1", "a", "b")
    add_edge("e2", "b", "a")

    result = trace_node("wiki/a.md", 5, db_path)

    assert result["nodes_visited"] == 2


def test_trace_does_not_report_the_same_edge_twice(graph):
    """An edge reachable from both endpoints must still appear once."""
    db_path, add_node, add_edge = graph
    add_node("a")
    add_node("b")
    add_edge("e1", "a", "b")

    result = trace_node("wiki/a.md", 3, db_path)

    signatures = [(e["from_node_id"], e["to_node_id"], e["edge_type"])
                  for e in result["edges"]]
    assert len(signatures) == len(set(signatures))


def test_trace_of_an_isolated_node_visits_only_itself(graph):
    db_path, add_node, _ = graph
    add_node("lonely")

    result = trace_node("wiki/lonely.md", 2, db_path)

    assert result["nodes_visited"] == 1
    assert result["edges"] == []
