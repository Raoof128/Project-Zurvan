"""Keyword search scans what the index covers: wiki/ and docs/ minus trace
mirrors and minus `docs/evaluation/retrieval/` (scripts/chunk.py
EXCLUDED_PATH_PREFIXES). Reports about the retriever must not feed it, and they
can quote the retired instrument; the Rust server's read gate refuses them too.
"""
from scripts.context_export import _search_internal


def test_keyword_search_skips_retrieval_reports(tmp_path):
    for rel in ("docs/evaluation/retrieval/report.md", "docs/evaluation/other.md", "docs/guide.md",
                "wiki/traces/t.md", "wiki/page.md"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("zebracorn appears here\n")
    found = {h["source_path"] for h in _search_internal("zebracorn", hybrid=False, limit=50, root=tmp_path)}
    assert found == {"docs/evaluation/other.md", "docs/guide.md", "wiki/page.md"}
