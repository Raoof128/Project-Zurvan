"""Review follow-up (b) to audit F4: typed embedding errors on the remaining
callers. `agent preflight` and the `zurvan_context` MCP tool must degrade to
keyword results WITH the visible notice `zurvan_search` / `zurvan_orient`
already use, and `search_hybrid` must not leak its sqlite connection when the
embedder raises. Every store here lives under tmp_path."""
import sqlite3

import pytest

import scripts.hybrid_search as hs
from scripts.embed import EmbeddingSpaceMismatch, EmbeddingUnavailable

NOTICE = "Semantic search unavailable:"


@pytest.fixture
def broken_embedder_store(tmp_path, monkeypatch):
    """A tiny mock-built index under tmp_path, the keyword scan pointed at the
    same corpus, and the query embedder raising the typed error."""
    from scripts.rebuild_search_index import rebuild_search_index
    corpus = tmp_path / "corpus"
    (corpus / "wiki").mkdir(parents=True)
    (corpus / "wiki" / "citation.md").write_text(
        "# Citation Rules\n\nEvery citation needs a verbatim quote.\n", encoding="utf-8")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    db = str(tmp_path / "search.sqlite")
    rebuild_search_index(db_path=db, root=corpus)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", db)
    monkeypatch.setattr("scripts.context_export.PROJECT_ROOT", corpus)

    def boom(text, provider=None, model=None):
        raise EmbeddingUnavailable("model cannot load (fixture)")
    monkeypatch.setattr(hs, "get_embedding", boom)
    return db, corpus


def test_context_tool_degrades_to_keyword_with_the_notice(broken_embedder_store):
    from scripts.mcp_tools import tool_zurvan_context
    out = tool_zurvan_context("citation", hybrid=True, graph=False, limit=3)
    assert out.startswith(NOTICE), out
    assert "keyword results only" in out
    assert "citation.md" in out
    assert not out.startswith("Error")


def test_context_tool_degrades_on_space_mismatch_too(broken_embedder_store, monkeypatch):
    from scripts.mcp_tools import tool_zurvan_context

    def mismatch(text, provider=None, model=None):
        raise EmbeddingSpaceMismatch("wrong space (fixture)")
    monkeypatch.setattr(hs, "get_embedding", mismatch)
    out = tool_zurvan_context("citation", hybrid=True, graph=False, limit=3)
    assert out.startswith(NOTICE) and "wrong space" in out and "citation.md" in out


def test_context_tool_keyword_mode_has_no_notice(broken_embedder_store):
    from scripts.mcp_tools import tool_zurvan_context
    out = tool_zurvan_context("citation", hybrid=False, graph=False, limit=3)
    assert NOTICE not in out and "citation.md" in out


def test_cli_context_still_fails_loudly(broken_embedder_store):
    """The CLI contract is unchanged: exit 1 on stderr, never a silent degrade."""
    from scripts.context_export import export_context
    with pytest.raises(EmbeddingUnavailable):
        export_context("citation", 3, True, False)


def test_agent_preflight_degrades_with_the_notice(broken_embedder_store, tmp_path, monkeypatch):
    from scripts.agent_workflow import agent_preflight
    monkeypatch.setattr("scripts.agent_workflow.LOG_FILE", tmp_path / "log.md")
    monkeypatch.setattr("scripts.agent_workflow.OPEN_QUESTIONS_FILE", tmp_path / "open.md")
    monkeypatch.setattr("scripts.agent_workflow.ROOT", tmp_path)
    monkeypatch.setattr("scripts.agent_workflow.CODE_ROOT", tmp_path)
    (tmp_path / "scripts" / "templates").mkdir(parents=True)
    (tmp_path / "scripts" / "templates" / "preflight.md").write_text(
        "Topic: {topic}\nContext: {context_bundle}", encoding="utf-8")
    out = agent_preflight("citation", True, False, 3)
    assert f"Context: {NOTICE}" in out, out
    assert "keyword results only" in out
    assert "citation.md" in out


def test_search_hybrid_closes_its_connection_when_the_embedder_raises(broken_embedder_store, monkeypatch):
    db, _ = broken_embedder_store
    closed = []

    class Tracking(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()

    real_connect = sqlite3.connect
    monkeypatch.setattr(
        hs.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Tracking, **k))
    with pytest.raises(EmbeddingUnavailable):
        hs.search_hybrid("citation", limit=3, db_path=db)
    assert closed == [True], "the read-only connection was left open on the failure path"
