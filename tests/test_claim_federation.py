import pytest
from pathlib import Path
from scripts.claim_federation import discover_claims_and_policies_in_project

@pytest.fixture
def mock_project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    
    # Docs
    d_dir = p / "docs"
    d_dir.mkdir()
    d1 = d_dir / "policy.md"
    d1.write_text("Policy content")
    
    # Wiki claims
    c_dir = p / "wiki" / "claims"
    c_dir.mkdir(parents=True)
    c1 = c_dir / "c1.md"
    c1.write_text("---\ntitle: C1\ntags: [tag1]\n---\nClaim 1")
    
    # Wiki frontmatter claim
    n_dir = p / "wiki" / "notes"
    n_dir.mkdir(parents=True)
    n1 = n_dir / "n1.md"
    n1.write_text("---\ntitle: N1\ntype: claim\n---\nClaim 2")
    
    # Note (should be ignored)
    n2 = n_dir / "n2.md"
    n2.write_text("Just a note")
    
    # AGENTS.md
    a1 = p / "AGENTS.md"
    a1.write_text("Agents content")
    
    # Raw
    raw = p / "raw"
    raw.mkdir()
    r1 = raw / "r1.md"
    r1.write_text("---\ntype: claim\n---\nRaw claim")
    
    return p

def test_discover_claims_and_policies(mock_project):
    items = discover_claims_and_policies_in_project("proj", mock_project)
    
    assert len(items) == 4
    sources = [it["source_kind"] for it in items]
    assert sources.count("policy") == 1 # docs/policy.md
    assert sources.count("claim") == 2 # c1.md, n1.md
    assert sources.count("rule") == 1 # AGENTS.md
    
    titles = [it["title"] for it in items]
    assert "C1" in titles
    assert "N1" in titles
