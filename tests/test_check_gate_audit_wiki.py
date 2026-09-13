"""check.sh gate [3/6] — Wiki audit.

`audit_wiki.py` ran only as a shell step, so `main()`, the duplicate-title scan
and the orphan-page scan were uncovered. Every test builds a throwaway wiki in
tmp_path; none of them read the real one.

Note on the uncited-claim cases: those live in tests/test_audit.py, which owns
the citation-shape regressions.
"""

import scripts.audit_wiki as audit_wiki
from scripts.audit_wiki import (
    audit_duplicate_titles,
    audit_frontmatter,
    audit_orphan_pages,
    claim_is_cited,
)


def _page(wiki, relpath, content):
    path = wiki / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── frontmatter ───────────────────────────────────────────────────────────────

def test_frontmatter_missing_is_reported_with_the_path():
    err = audit_frontmatter("# No frontmatter here", "wiki/notes/a.md")
    assert err is not None and "wiki/notes/a.md" in err


def test_frontmatter_present_is_accepted():
    assert audit_frontmatter("---\ntitle: ok\n---\nbody", "a.md") is None


# ── citation shapes ───────────────────────────────────────────────────────────

def test_claim_is_cited_accepts_each_supported_shape():
    assert claim_is_cited('---\nsource: "wiki/sources/x.md"\n---\n')
    assert claim_is_cited("See [[wiki/sources/x.md]]")
    assert claim_is_cited("See [x](wiki/sources/x.md)")
    assert claim_is_cited("cited from somewhere")


def test_claim_is_cited_rejects_a_page_with_no_citation_at_all():
    assert not claim_is_cited("---\ntype: claim\n---\n# Claim\nJust an assertion.\n")


# ── duplicate titles ──────────────────────────────────────────────────────────

def test_duplicate_titles_are_reported_once_naming_both_pages(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "a.md", "---\ntitle: Shared Title\n---\n")
    _page(wiki, "b.md", "---\ntitle: Shared Title\n---\n")

    errors = audit_duplicate_titles(str(wiki))

    assert len(errors) == 1
    assert "Shared Title" in errors[0]
    assert "a.md" in errors[0] and "b.md" in errors[0]


def test_distinct_titles_produce_no_duplicate_errors(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "a.md", "---\ntitle: One\n---\n")
    _page(wiki, "b.md", "---\ntitle: Two\n---\n")

    assert audit_duplicate_titles(str(wiki)) == []


def test_pages_without_a_title_are_not_treated_as_duplicates(tmp_path):
    """Two untitled pages must not collide on an empty title."""
    wiki = tmp_path / "wiki"
    _page(wiki, "a.md", "---\ntype: note\n---\n")
    _page(wiki, "b.md", "---\ntype: note\n---\n")

    assert audit_duplicate_titles(str(wiki)) == []


def test_duplicate_title_scan_reaches_into_subdirectories(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "a.md", "---\ntitle: Same\n---\n")
    _page(wiki, "decisions/b.md", "---\ntitle: Same\n---\n")

    assert len(audit_duplicate_titles(str(wiki))) == 1


def test_non_markdown_files_are_ignored_by_the_title_scan(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "a.md", "---\ntitle: Same\n---\n")
    _page(wiki, "b.txt", "---\ntitle: Same\n---\n")

    assert audit_duplicate_titles(str(wiki)) == []


# ── orphan pages ──────────────────────────────────────────────────────────────

def test_a_page_nothing_links_to_is_reported_as_an_orphan(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "lonely.md", "---\ntitle: Lonely\n---\n")

    errors = audit_orphan_pages(str(wiki))

    assert any("lonely.md" in e for e in errors)


def test_a_linked_page_is_not_an_orphan(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "hub.md", "---\ntitle: Hub\n---\n[target](target.md)\n")
    _page(wiki, "target.md", "---\ntitle: Target\n---\n")

    errors = audit_orphan_pages(str(wiki))

    assert not any("target.md" in e for e in errors)


def test_navigation_pages_are_exempt_from_the_orphan_check(tmp_path):
    """index/log/overview/open-questions are entry points, never orphans."""
    wiki = tmp_path / "wiki"
    for name in ("index.md", "log.md", "overview.md", "open-questions.md"):
        _page(wiki, name, f"---\ntitle: {name}\n---\n")

    assert audit_orphan_pages(str(wiki)) == []


def test_template_pages_are_exempt_from_the_orphan_check(tmp_path):
    wiki = tmp_path / "wiki"
    _page(wiki, "templates/claim.md", "---\ntitle: Claim template\n---\n")

    assert audit_orphan_pages(str(wiki)) == []


def test_external_links_do_not_mark_a_page_as_linked(tmp_path):
    """An http link is not a wiki edge, so the target page stays an orphan."""
    wiki = tmp_path / "wiki"
    _page(wiki, "hub.md", "---\ntitle: Hub\n---\n[out](https://example.com/target.md)\n")
    _page(wiki, "target.md", "---\ntitle: Target\n---\n")

    assert any("target.md" in e for e in audit_orphan_pages(str(wiki)))


def test_orphan_links_resolve_relative_to_the_linking_page(tmp_path):
    """A link from decisions/ must resolve against decisions/, not the wiki root."""
    wiki = tmp_path / "wiki"
    _page(wiki, "decisions/hub.md", "---\ntitle: Hub\n---\n[t](../sources/target.md)\n")
    _page(wiki, "sources/target.md", "---\ntitle: Target\n---\n")

    errors = audit_orphan_pages(str(wiki))

    assert not any("target.md" in e for e in errors)


# ── main() ────────────────────────────────────────────────────────────────────

def test_main_reports_a_clean_wiki(tmp_path, monkeypatch, capsys):
    wiki = tmp_path / "wiki"
    _page(wiki, "index.md", "---\ntitle: Index\n---\n")
    monkeypatch.chdir(tmp_path)

    audit_wiki.main()

    assert "Audit passed cleanly!" in capsys.readouterr().out


def test_main_lists_each_issue_and_prints_the_count(tmp_path, monkeypatch, capsys):
    wiki = tmp_path / "wiki"
    _page(wiki, "orphan.md", "no frontmatter at all")
    monkeypatch.chdir(tmp_path)

    audit_wiki.main()

    out = capsys.readouterr().out
    assert "Found 2 issues:" in out          # orphan + missing frontmatter
    assert "Orphan page found: orphan.md" in out
    assert "Missing YAML frontmatter" in out
