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


# ── claims and summaries link to their source page (review of package-a, 2026-11-07) ──
#
# Audit F1 made `source_id` content-identified (`<stem>-<hash12>`), but ingest still names
# the source page `wiki/sources/<stem>.md`. The graph linked claims through
# `wiki/sources/{source_id}.md`, so every newly extracted claim and summary silently lost
# its `derived_from` edge. These tests build pages with the real writers.

def _source_node(filepath):
    from scripts.graph_build import generate_node_id
    from scripts.ingest import source_page_stem
    stem = source_page_stem(filepath)
    path = f"wiki/sources/{stem}.md"
    nid = generate_node_id(path)
    return nid, {nid: {"node_id": nid, "slug": stem, "title": stem, "path": path}}


def test_an_extracted_claim_derives_from_its_ingested_source_page(tmp_path):
    # Z2: claim pages are rendered from the ledger; an extracted claim (reader
    # extract_text@1) keeps `source_file` so the derived_from edge still resolves.
    from scripts.claim_ledger import ClaimState
    from scripts.claim_pages import render
    filepath = "raw/papers/My Paper.pdf"
    state = ClaimState(claim_id="clm_0123456789abcdef", rev=1, status="asserted", text="t",
                       confidence="high",
                       evidence=[{"source": filepath, "reader": "extract_text@1",
                                  "selector": {"TextQuoteSelector": {"exact": "the extracted text"}}}])
    body = render(state, [], [])
    node_id, nodes = _source_node(filepath)

    edges = extract_edges(f"wiki/claims/{state.claim_id}.md", body, nodes)

    assert [e["edge_type"] for e in edges if e["to_node_id"] == node_id] == ["derived_from"]


def test_an_extraction_summary_derives_from_its_ingested_source_page():
    from scripts.extract import source_identity, summary_page
    filepath = "raw/papers/My Paper.pdf"
    source_id, _ = source_identity(filepath, "the extracted text")
    body = summary_page(source_id, "My Paper.pdf", {"short": "s", "detailed": "d"})
    node_id, nodes = _source_node(filepath)

    edges = extract_edges(f"wiki/summaries/{source_id}_summary.md", body, nodes)

    assert [e["edge_type"] for e in edges if e["to_node_id"] == node_id] == ["derived_from"]


def test_a_legacy_claim_whose_source_id_is_the_stem_still_links_once():
    # Claims written before F1 carry `source_id: <stem>` and no `source_file`.
    node_id, nodes = _source_node("raw/papers/My Paper.pdf")
    body = f"---\ntype: claim\nclaim_id: c\nsource_id: {nodes[node_id]['slug']}\n---\n# c\n"

    edges = extract_edges("wiki/claims/c.md", body, nodes)

    assert [e["edge_type"] for e in edges if e["to_node_id"] == node_id] == ["derived_from"]


def test_a_list_item_with_a_colon_is_not_read_as_a_key():
    # Z2 final-review minor, fixed 2026-11-15: a generated claim page's tag
    # `  - "client: acme"` became a junk key `- "client`.
    fm, _ = parse_frontmatter('---\ntype: "claim"\ntags:\n  - "client: acme"\n'
                              '- "source: x"\nmeta:\n  nested: kept\n---\nbody\n')
    assert fm == {"type": "claim", "tags": "", "meta": "", "nested": "kept"}
