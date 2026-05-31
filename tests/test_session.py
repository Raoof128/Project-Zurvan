import os
from unittest.mock import patch
from scripts.session import session_start, session_close, get_safe_filename

def test_session_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.session.SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr("scripts.session.LOG_FILE", tmp_path / "wiki" / "log.md")
    monkeypatch.setattr("scripts.session.ROOT", tmp_path)
    
    # Create templates for test
    (tmp_path / "scripts" / "templates").mkdir(parents=True)
    with open(tmp_path / "scripts" / "templates" / "session_start.md", "w") as f:
        f.write("Start {topic} {start_time} **Status**: {status}")
    with open(tmp_path / "scripts" / "templates" / "session_close.md", "w") as f:
        f.write("Close {end_time} {summary} {checks}")
        
    (tmp_path / "wiki").mkdir(exist_ok=True)
    with open(tmp_path / "wiki" / "log.md", "w") as f:
        f.write("# Logs\n")

    topic = "Test Topic!@#"
    filepath = session_start(topic)
    
    assert os.path.exists(filepath)
    with open(filepath, "r") as f:
        content = f.read()
    assert "Start Test Topic!@#" in content
    assert "**Status**: Open" in content
    
    with open(tmp_path / "wiki" / "log.md", "r") as f:
        log_content = f.read()
    assert "Session Started**: Test Topic!@#" in log_content
    
    session_close(topic, "Done", "pytest")
    
    with open(filepath, "r") as f:
        content = f.read()
    assert "**Status**: Closed" in content
    assert "Close " in content
    assert "Done" in content
    assert "pytest" in content
