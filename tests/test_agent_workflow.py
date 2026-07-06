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


from pathlib import Path
from scripts.agent_workflow import project_digest


def _make_corpus(tmp_path: Path):
    d = tmp_path / "wiki" / "decisions"
    c = tmp_path / "wiki" / "claims"
    d.mkdir(parents=True)
    c.mkdir(parents=True)
    (d / "nexus-auth.md").write_text(
        '---\ntitle: "Zero-trust auth for Nexus Archive"\ntype: decision\n'
        'status: "accepted"\ntags:\n  - "nexus"\n  - "auth"\n---\n\n# Zero-trust auth\n',
        encoding="utf-8")
    (d / "unrelated.md").write_text(
        '---\ntitle: "Delay vector search"\ntype: decision\nstatus: "accepted"\n'
        'tags:\n  - "roadmap"\n---\n\n# Delay vector search\n', encoding="utf-8")
    (c / "claim-abc123.md").write_text(
        '---\ntype: claim\nconfidence: "high"\nsource: "docs/x.md"\n'
        'tags:\n  - "nexus"\n---\n\n# Claim\nNexus Archive uses Supabase RLS.\n\n'
        '## Evidence\n> quote\n', encoding="utf-8")
    (tmp_path / "wiki" / "open-questions.md").write_text(
        "# Open Questions\n\n## Q: Should nexus-archive rotate JWTs weekly?\n"
        "- **ID**: aaa\n- **Tags**: nexus, auth\n\n"
        "## Q: Unrelated question?\n- **ID**: bbb\n- **Tags**: mcp\n",
        encoding="utf-8")


def test_project_digest_matches_tags_titles_and_questions(tmp_path):
    _make_corpus(tmp_path)
    out = project_digest("Nexus_Archive", root=tmp_path)
    assert "Zero-trust auth for Nexus Archive" in out
    assert "wiki/decisions/nexus-auth.md" in out
    assert "Supabase RLS" in out
    assert "rotate JWTs" in out
    assert "Delay vector search" not in out
    assert "Unrelated question" not in out
    assert "1 decision" in out and "1 claim" in out and "1 open question" in out
    # pointer line for deeper recall
    assert "zurvan_search" in out


def test_project_digest_empty(tmp_path):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    out = project_digest("simurghforge", root=tmp_path)
    assert "No Zurvan knowledge for this project yet." in out
    assert "zurvan_search" in out


def test_project_digest_caps_output(tmp_path):
    d = tmp_path / "wiki" / "decisions"
    d.mkdir(parents=True)
    for i in range(12):
        (d / f"dec-{i}.md").write_text(
            f'---\ntitle: "Simurgh decision {i} ' + "x" * 200 + '"\n'
            'tags:\n  - "simurgh"\n---\n', encoding="utf-8")
    out = project_digest("simurgh", root=tmp_path)
    lines = [l for l in out.splitlines() if l.startswith("- ")]
    assert len(lines) <= 5                       # decision cap
    assert all(len(l) <= 160 for l in lines)     # line cap (120 + path suffix)
    assert "12 decisions" in out                 # counts reflect the true total
