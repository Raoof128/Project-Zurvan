import os
from unittest.mock import patch
from scripts.agent_workflow import agent_preflight, agent_postedit

def test_agent_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.agent_workflow.LOG_FILE", tmp_path / "log.md")
    monkeypatch.setattr("scripts.agent_workflow.OPEN_QUESTIONS_FILE", tmp_path / "open.md")
    monkeypatch.setattr("scripts.agent_workflow.ROOT", tmp_path)
    
    with open(tmp_path / "log.md", "w") as f:
        f.write("Log line 1\nLog line 2")
    with open(tmp_path / "open.md", "w") as f:
        f.write("Question 1")
        
    (tmp_path / "scripts" / "templates").mkdir(parents=True)
    with open(tmp_path / "scripts" / "templates" / "preflight.md", "w") as f:
        f.write("Topic: {topic}\nLogs: {log_entries}\nOpen: {open_questions}\nContext: {context_bundle}")
        
    with patch("scripts.context_export.export_context", return_value="Dummy Context"):
        result = agent_preflight("My Topic", False, False, 1)
        
    assert "Topic: My Topic" in result
    assert "Logs: Log line 1\nLog line 2" in result
    assert "Open: Question 1" in result
    assert "Context: Dummy Context" in result

def test_agent_postedit(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.agent_workflow.LOG_FILE", tmp_path / "log.md")
    monkeypatch.setattr("scripts.agent_workflow.ROOT", tmp_path)
    
    (tmp_path / "scripts" / "templates").mkdir(parents=True)
    with open(tmp_path / "scripts" / "templates" / "postedit.md", "w") as f:
        f.write("Time: {timestamp} Summary: {summary} Files: {files} Checks: {checks}")
        
    agent_postedit("Did work", ["file1.txt", "file2.txt"], "make test")
    
    with open(tmp_path / "log.md", "r") as f:
        content = f.read()
        
    assert "Summary: Did work" in content
    assert "- `file1.txt`" in content
    assert "- `file2.txt`" in content
    assert "Checks: make test" in content
