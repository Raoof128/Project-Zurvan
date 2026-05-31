import pytest
from scripts.publication_citations import generate_citation_appendix

def test_generate_citation_appendix():
    report = {
        "citations": [{"evidence_id": "ev-1"}, {"evidence_id": "ev-missing"}]
    }
    
    pack = {
        "items": [
            {
                "id": "ev-1",
                "project_name": "testproj",
                "path": "wiki/test.md",
                "excerpt": "This is a test."
            }
        ]
    }
    
    appendix = generate_citation_appendix(report, pack)
    assert len(appendix) == 2
    
    assert appendix[0]["evidence_id"] == "ev-1"
    assert appendix[0]["project"] == "testproj"
    
    assert appendix[1]["evidence_id"] == "ev-missing"
    assert appendix[1]["project"] == "MISSING"
