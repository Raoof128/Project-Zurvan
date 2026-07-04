import os
import sqlite3
import scripts.graph_build as graph_build
from scripts.graph_build import extract_node_data, parse_frontmatter, extract_edges, build_graph

def test_extract_node_data():
    content = "---\ntitle: Test Node\ntype: decision\ntags: test\n---\nBody content"
    data = extract_node_data("wiki/test.md", content)
    assert data['title'] == "Test Node"
    assert data['node_type'] == "decision"
    assert data['tags'] == "test"
    assert data['path'] == "wiki/test.md"

def test_extract_node_type_fallback():
    content = "No frontmatter"
    data = extract_node_data("wiki/claims/claim-test.md", content)
    assert data['node_type'] == "claim"

def test_extract_edges():
    content = "---\nsource_path: wiki/sources/test.md\n---\nBody [[target]]\nSource: [link](other.md)"
    nodes_dict = {
        "hash_target": {"node_id": "hash_target", "slug": "target", "title": "Target", "path": "wiki/target.md"},
        "hash_other": {"node_id": "hash_other", "slug": "other", "title": "Other", "path": "wiki/other.md"}
    }
    # Normally generate_node_id produces a real hash. For the test, let's mock it or rely on real hashes.
    import hashlib
    def fake_hash(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
    
    nodes_dict = {
        fake_hash("wiki/target.md"): {"node_id": fake_hash("wiki/target.md"), "slug": "target", "title": "Target", "path": "wiki/target.md"},
        fake_hash("wiki/other.md"): {"node_id": fake_hash("wiki/other.md"), "slug": "other", "title": "Other", "path": "wiki/other.md"},
        fake_hash("wiki/sources/test.md"): {"node_id": fake_hash("wiki/sources/test.md"), "slug": "test", "title": "Test", "path": "wiki/sources/test.md"}
    }
    
    edges = extract_edges("wiki/test.md", content, nodes_dict)
    edge_types = [e['edge_type'] for e in edges]
    
    # We should have one derived_from (from yaml source_path)
    # one mentions (from wikilink target)
    # one derived_from (from markdown link other.md, actually path will be wiki/other.md)
    assert 'mentions' in edge_types
    assert 'derived_from' in edge_types
    assert len(edges) >= 3


def test_obsidian_aliased_and_heading_wikilinks_resolve():
    # Obsidian's [[target|alias]] and [[target#heading]] must resolve to the
    # `target` node — otherwise edges an Obsidian user authors are dropped.
    import hashlib
    def fake_hash(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
    nodes_dict = {
        fake_hash("wiki/target.md"): {
            "node_id": fake_hash("wiki/target.md"),
            "slug": "target", "title": "Target", "path": "wiki/target.md",
        }
    }

    for body in ("See [[target|Display Text]].", "See [[target#Some Heading]].",
                 "See [[target#Sec|Alias]]."):
        content = "---\ntitle: X\n---\n" + body
        edges = extract_edges("wiki/x.md", content, nodes_dict)
        assert any(e['edge_type'] == 'mentions' for e in edges), f"no edge for: {body}"


def test_build_graph_excludes_wiki_traces(tmp_path, monkeypatch):
    # Trace replay mirrors under wiki/traces/ are derived, self-referential
    # audit artifacts (the search chunker excludes them too) and must not
    # become graph nodes.
    monkeypatch.setattr(graph_build, "PROJECT_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "traces").mkdir()
    (tmp_path / "wiki" / "real.md").write_text("---\ntitle: Real\n---\nknowledge")
    (tmp_path / "wiki" / "traces" / "trace-x.md").write_text("---\ntitle: Trace\n---\nreplay")

    db_path = str(tmp_path / "graph.sqlite")
    build_graph(db_path)

    conn = sqlite3.connect(db_path)
    paths = [r[0] for r in conn.execute("SELECT path FROM nodes").fetchall()]
    conn.close()
    assert any(p.endswith("real.md") for p in paths)
    assert not any("traces" in p.split(os.sep) for p in paths)
