"""Z2 Task 5 / spec §7.5a: non-current claims leave every derived view.

A generated page whose status is superseded, retracted, orphaned or erased is
not chunked for search (so not in `context`), not a graph node, not in the
recall digest and not in federation discovery. Legacy pages are never hidden.
"""

import pytest

from scripts import agent_workflow, claim_federation, chunk, graph_build
from scripts import claim_visibility as cv


def _page(status, authority="structured", text="zetaword fact"):
    auth = f"authority: {authority}\n" if authority else ""
    return (f"---\ntype: claim\n{auth}status: {status}\ntags:\n  - \"zetaproj\"\n---\n\n"
            f"# Claim\n{text}\n\n## Evidence\n> quote\n")


@pytest.fixture
def wiki(tmp_path):
    claims = tmp_path / "wiki" / "claims"
    claims.mkdir(parents=True)
    (claims / "clm_current.md").write_text(_page("asserted"), encoding="utf-8")
    (claims / "clm_retracted.md").write_text(_page("retracted"), encoding="utf-8")
    (claims / "clm_superseded.md").write_text(_page("superseded"), encoding="utf-8")
    (claims / "claim-legacy.md").write_text(_page("retracted", authority=None),
                                            encoding="utf-8")
    return tmp_path


def test_rule():
    assert cv.NONCURRENT == ("superseded", "retracted", "orphaned", "erased")
    fm = {"authority": "structured", "status": "orphaned"}
    assert cv.is_hidden_claim_page("wiki/claims/x.md", fm) is True
    assert cv.is_hidden_claim_page("wiki/decisions/x.md", fm) is False
    assert cv.is_hidden_claim_page("wiki/claims/x.md", {"status": "orphaned"}) is False
    assert cv.is_hidden_claim_page("wiki/claims/x.md",
                                   {"authority": "structured", "status": "contested"}) is False


def test_search_chunker_skips_noncurrent(wiki):
    files = {f.replace("\\", "/") for f in chunk.scan_markdown_files(wiki)}
    assert "wiki/claims/clm_current.md" in files and "wiki/claims/claim-legacy.md" in files
    assert "wiki/claims/clm_retracted.md" not in files
    assert "wiki/claims/clm_superseded.md" not in files


def test_graph_skips_noncurrent(wiki):
    nodes, _ = graph_build._collect_graph(source_root=wiki)
    paths = {n["path"] for n in nodes.values()}
    joined = " ".join(map(str, paths))
    assert "clm_current" in joined and "claim-legacy" in joined
    assert "clm_retracted" not in joined and "clm_superseded" not in joined


def test_digest_skips_noncurrent(wiki):
    out = agent_workflow.project_digest("zetaproj", root=wiki)
    assert "clm_current" in out
    assert "clm_retracted" not in out and "clm_superseded" not in out


def test_federation_skips_noncurrent(wiki):
    items = claim_federation.discover_claims_and_policies_in_project("t", wiki)
    rels = {i["relative_path"] for i in items}
    assert "wiki/claims/clm_current.md" in rels
    assert "wiki/claims/clm_retracted.md" not in rels
    assert "wiki/claims/clm_superseded.md" not in rels
