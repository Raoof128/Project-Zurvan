from scripts.policy_rules import identify_policies

def test_identify_policies_positive():
    matches = identify_policies("We never modify raw files. The raw/ is immutable.")
    assert "raw_protection" in matches
    assert matches["raw_protection"]["status"] == "positive"
    
def test_identify_policies_negative():
    matches = identify_policies("We can write to raw if needed.")
    assert "raw_protection" in matches
    assert matches["raw_protection"]["status"] == "negative"
    
def test_identify_policies_conflict():
    matches = identify_policies("We never modify raw files, but sometimes we update raw directly.")
    assert "raw_protection" in matches
    assert matches["raw_protection"]["status"] == "conflict"
    
def test_identify_policies_multiple():
    matches = identify_policies("No cloud APIs allowed. Local sqlite only.")
    assert "no_cloud" in matches
    assert "graph_no_remote_db" in matches
