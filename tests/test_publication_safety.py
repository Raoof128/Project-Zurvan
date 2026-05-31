import pytest
from scripts.publication_safety import check_publication_safety

def test_safety_blocks():
    # Test path leak
    res = check_publication_safety("File at /Users/raouf/something.txt")
    assert any("absolute path" in r for r in res)
    
    # Test email leak
    res = check_publication_safety("Contact me@domain.com")
    assert any("Email address" in r for r in res)
    
    res_allow = check_publication_safety("Contact me@domain.com", allow_emails=True)
    assert not res_allow
    
    # Test token leak
    res = check_publication_safety("my api_key='a1b2c3d4e5f6g7h8i9j0'")
    assert any("Token-like" in r for r in res)
    
    # Test raw reference
    res = check_publication_safety("Refer to raw/data.txt")
    assert any("raw/" in r for r in res)
