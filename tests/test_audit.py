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
