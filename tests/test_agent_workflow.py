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


def test_agent_prime_is_compact_and_complete():
    from scripts.agent_workflow import agent_prime

    out = agent_prime()

    assert "Zurvan prime" in out
    assert "Rules:" in out
    assert "Graph:" in out
    assert "Search index:" in out
    assert "Open questions:" in out
    # Built for SessionStart hooks — must stay cheap to inject every session.
    assert len(out) < 4000


def _staleness_fixture(tmp_path, indexed_at):
    import sqlite3
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "page.md").write_text("# Page\ncontent")
    db = tmp_path / "search.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE chunks (chunk_id TEXT, indexed_at TEXT)")
    conn.execute("INSERT INTO chunks VALUES ('c1', ?)", (indexed_at,))
    conn.commit()
    conn.close()
    return db


def test_index_staleness_flags_newer_files(tmp_path):
    from scripts.agent_workflow import _index_staleness

    db = _staleness_fixture(tmp_path, "2020-01-01T00:00:00")  # ancient index
    verdict = _index_staleness(root=tmp_path, db_path=db)
    assert verdict.startswith("STALE")
    assert "zurvan index search" in verdict


def test_index_staleness_fresh_when_index_is_newer(tmp_path):
    import datetime
    from scripts.agent_workflow import _index_staleness

    future = (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()
    db = _staleness_fixture(tmp_path, future)
    assert _index_staleness(root=tmp_path, db_path=db) == "fresh"


def test_index_staleness_missing_db(tmp_path):
    from scripts.agent_workflow import _index_staleness
    (tmp_path / "wiki").mkdir()
    assert "missing" in _index_staleness(root=tmp_path, db_path=tmp_path / "nope.sqlite")
