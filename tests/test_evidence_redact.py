from scripts.evidence_redact import redact_text, redact_item, redact_evidence_pack_items

def test_redact_absolute_paths():
    text = 'Look at this file: /Users/name/docs/file.txt and also /etc/passwd'
    res = redact_text(text)
    assert '[REDACTED_PATH]' in res
    assert '/Users/name' not in res
    
def test_redact_home_paths():
    text = 'Check ~/Desktop/foo.txt'
    res = redact_text(text)
    assert '[REDACTED_PATH]' in res
    assert '~/Desktop' not in res
    
def test_redact_emails():
    text = 'Contact me at john.doe@example.com for info.'
    res = redact_text(text)
    assert '[REDACTED_EMAIL]' in res
    assert 'john.doe' not in res

def test_redact_item():
    item = {
        "title": "A title /etc/shadow",
        "excerpt": "My email is test@test.com",
        "reason": "secret: AKIA0123456789ABCDEF",
        "unrelated": 123
    }
    redacted = redact_item(item)
    assert '[REDACTED_PATH]' in redacted["title"]
    assert '[REDACTED_EMAIL]' in redacted["excerpt"]
    assert '[REDACTED_AWS_KEY]' in redacted["reason"]
    assert redacted["unrelated"] == 123
