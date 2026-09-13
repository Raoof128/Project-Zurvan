import pytest
from scripts.audit_wiki import audit_frontmatter, audit_uncited_claims

def test_audit_frontmatter():
    good_content = "---\ntitle: test\n---\ncontent"
    bad_content = "# Title\ncontent"
    
    assert audit_frontmatter(good_content, "path.md") is None
    assert audit_frontmatter(bad_content, "path.md") is not None

def test_audit_uncited_claims():
    good_claim = "This is a claim. cited from [source](source.md)"
    bad_claim = "This is a claim with no citations."

    assert audit_uncited_claims(good_claim, "claims/test.md") is None
    assert audit_uncited_claims(bad_claim, "claims/test.md") is not None


def test_audit_accepts_claims_written_by_the_cli():
    """`zurvan claim add` cites via frontmatter + a wikilink, not a md link.

    Regression: the old heuristic only recognised "cited from"/"missing?" or a
    Markdown inline link, so every CLI-created claim was reported as uncited.
    """
    cli_claim = (
        '---\n'
        'type: claim\n'
        'confidence: "high"\n'
        'source: "wiki/sources/some-source.md"\n'
        '---\n\n'
        '# Claim\nSomething that was verified.\n\n'
        '## Evidence\n> a verbatim quote\n\n'
        '**Source**: [[wiki/sources/some-source.md]]\n'
    )
    assert audit_uncited_claims(cli_claim, "wiki/claims/claim-abc123.md") is None


def test_audit_still_flags_a_claim_whose_source_is_empty():
    """An empty `source:` must not count as a citation."""
    empty_source = (
        '---\ntype: claim\nsource:\n---\n\n# Claim\nNo evidence anywhere.\n'
    )
    assert audit_uncited_claims(empty_source, "wiki/claims/claim-def456.md") is not None
