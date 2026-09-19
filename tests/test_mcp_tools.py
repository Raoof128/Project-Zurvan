import os
from scripts.mcp_tools import (
    tool_zurvan_search,
    tool_zurvan_context,
    tool_zurvan_remember,
    tool_zurvan_graph_stats_struct,
)

def test_tool_zurvan_search(monkeypatch, tmp_path):
    # This wrote `wiki/test_mcp_search.md` into the LIVE store and `os.remove`d
    # it, the pattern `test_tool_zurvan_remember` below was already fixed for.
    # A count-based guard sees nothing while the run completes — but the name is
    # FIXED, so a run killed between the write and the remove leaves the page
    # behind, and the NEXT run then overwrites it and deletes it, netting -1
    # against a store that never gained one. Observed 2026-09-19: the session
    # guard failed a gate with `wiki pages: 120 -> 119` and no write attribution.
    # Redirect the store; cleanup is not isolation.
    import scripts.context_export as context_export
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_mcp_search.md").write_text(
        "unique_mcp_search_term", encoding="utf-8")

    result = tool_zurvan_search("unique_mcp_search_term", hybrid=False)

    assert "test_mcp_search.md" in result

def test_tool_zurvan_graph_stats_struct():
    stats = tool_zurvan_graph_stats_struct()
    assert set(stats.keys()) == {"nodes", "edges"}
    assert isinstance(stats["nodes"], int)
    assert isinstance(stats["edges"], int)

def test_tool_zurvan_remember(monkeypatch, tmp_path):
    # This test used to write the note into the LIVE wiki and `os.remove` it
    # afterwards. The file count came back even, so a count-based guard saw
    # nothing — but `wiki/log.md` kept the "Note Added - MCP test" append
    # forever, altering a chunk of an indexed 4,000-chunk file on every run, and
    # a failure between the write and the remove would have left the note in the
    # corpus. Redirect the store instead; cleanup is not isolation.
    import scripts.memory as memory
    import scripts.safe_write as safe_write
    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)
    (tmp_path / "wiki").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    result = tool_zurvan_remember("note", "MCP test", "body", ["test"])
    assert "read-only mode" in result

    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    result = tool_zurvan_remember("note", "MCP test", "body", ["test"])
    assert "successfully" in result

    assert (tmp_path / "wiki" / "note-mcp-test.md").is_file(), "must write to the throwaway store"

# ── zurvan_read_page ──────────────────────────────────────────────────────────

def test_read_page_returns_file_content():
    from scripts.mcp_tools import tool_zurvan_read_page
    out = tool_zurvan_read_page("wiki/index.md")
    assert "Wiki Index" in out


def test_read_page_blocks_unsafe_paths():
    from scripts.mcp_tools import tool_zurvan_read_page
    for bad in ("../secrets.txt", "/etc/passwd", "raw/private.pdf"):
        out = tool_zurvan_read_page(bad)
        assert out.startswith("Error:"), bad


def test_read_page_registered_on_server():
    import scripts.mcp_server as server
    import anyio
    tools = anyio.run(server.mcp.list_tools)
    names = {t.name for t in tools}
    assert "zurvan_read_page" in names


# ── result bounding: pagination and budget ────────────────────────────────────

def test_paginate_reports_range_total_and_next_cursor():
    from scripts.mcp_tools import _paginate

    page, header = _paginate(list(range(100)), limit=25, cursor=0)
    assert page == list(range(25))
    assert "Showing 1-25 of 100" in header
    assert "cursor=25" in header

    page, header = _paginate(list(range(100)), limit=25, cursor=75)
    assert page == list(range(75, 100))
    assert "Showing 76-100 of 100" in header
    assert "cursor" not in header, "the last page must not advertise more"


def test_page_bounds_clamp_hostile_values():
    from scripts.mcp_tools import _page_bounds, MAX_PAGE, DEFAULT_PAGE

    assert _page_bounds(10_000, 0)[0] == MAX_PAGE      # capped
    assert _page_bounds(0, 0)[0] == 1                  # floored
    assert _page_bounds(-5, -5) == (1, 0)              # negatives
    assert _page_bounds("nonsense", "nonsense") == (DEFAULT_PAGE, 0)


def test_budget_truncates_and_says_so():
    from scripts.mcp_tools import _budget

    out = _budget("\n".join(f"line {i}" for i in range(10000)), budget=500)
    assert len(out) < 800
    assert "truncated" in out
    assert "more characters" in out
    assert _budget("short", budget=500) == "short", "under budget is passed through"


def test_graph_neighbours_is_paged_not_unbounded(monkeypatch):
    # Regression: on the 12,050-node graph the hub node returned 300,055
    # characters (~75k tokens) in ONE result — more than most context windows.
    import scripts.mcp_tools as mt

    fake = [{"from_title": f"A{i}", "edge_type": "links_to", "to_title": f"B{i}"}
            for i in range(5000)]
    monkeypatch.setattr(mt, "get_neighbours", lambda *a, **k: fake)

    out = mt.tool_zurvan_graph_neighbours("wiki/index.md")
    assert len(out) < mt.RESULT_CHAR_BUDGET
    assert "Showing 1-25 of 5000" in out
    assert "cursor=25" in out
    assert out.count("--[links_to]-->") == 25

    page2 = mt.tool_zurvan_graph_neighbours("wiki/index.md", 1, 25, 25)
    assert "Showing 26-50 of 5000" in page2


def test_graph_expand_is_paged_and_nearest_first(monkeypatch):
    import scripts.mcp_tools as mt

    fake = [{"depth": (i % 3) + 1, "title": f"N{i}", "node_type": "note", "relation": "r"}
            for i in range(5000)]
    monkeypatch.setattr(mt, "expand_graph_context", lambda *a, **k: fake)

    out = mt.tool_zurvan_graph_expand("wiki/index.md", 2)
    assert len(out) < mt.RESULT_CHAR_BUDGET
    assert "Showing 1-25 of 5000" in out
    body = [ln for ln in out.splitlines() if ln.startswith("[")]
    assert all(ln.startswith("[1]") for ln in body), "nearest hops must come first"


def test_graph_errors_name_the_node_and_a_next_step(monkeypatch):
    # "Error: <repr>" gives an agent nothing to do. Errors should say what
    # failed and what to try.
    import scripts.mcp_tools as mt

    monkeypatch.setattr(mt, "get_neighbours", lambda *a, **k: [])
    out = mt.tool_zurvan_graph_neighbours("wiki/nope.md")
    assert "wiki/nope.md" in out
    assert "zurvan_search" in out or "graph rebuild" in out


# ── large pages are sliced, never refused ─────────────────────────────────────

def test_large_page_is_sliced_rather_than_refused(tmp_path, monkeypatch):
    # Regression: wiki/index.md grew past 256 KB and zurvan_read_page returned
    # "Error: File too large" and nothing else — the index became the one page
    # an agent could not open.
    import scripts.mcp_resources as mr
    import scripts.mcp_security as ms

    monkeypatch.setattr(mr, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ms, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    body = "".join(f"line {i}\n" for i in range(60000))   # well over 256 KB
    (tmp_path / "wiki" / "huge.md").write_text(body, encoding="utf-8")

    from scripts.mcp_tools import tool_zurvan_read_page

    first = tool_zurvan_read_page("wiki/huge.md")
    assert "too large" not in first.lower()
    assert "line 0" in first
    assert "offset=" in first.splitlines()[0]

    offset = int(first.splitlines()[0].split("offset=")[1].rstrip("."))
    second = tool_zurvan_read_page("wiki/huge.md", offset=offset)
    assert second.splitlines()[0].startswith("[wiki/huge.md — characters")
    assert "line 0\n" not in second, "the second slice must not repeat the first"


def test_status_reports_index_freshness_and_write_mode(monkeypatch):
    from scripts.mcp_tools import tool_zurvan_status

    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    out = tool_zurvan_status()
    assert "Search index:" in out
    assert "Graph:" in out
    assert "BLOCKED" in out

    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    assert "enabled" in tool_zurvan_status()


# ── stdout must never reach the wire ──────────────────────────────────────────

def test_tool_stdout_is_kept_off_the_protocol_stream(capsys, monkeypatch):
    # Under stdio, stdout IS the JSON-RPC channel. memory.py prints "Created
    # <path>" on every successful write, which landed directly in the stream:
    # the client logged "Failed to parse JSONRPC message from server", skipped
    # the line, and the e2e smoke still reported success. Silent corruption.
    import scripts.mcp_tools as mt

    @mt._protocol_safe
    def noisy():
        print("Created /some/path.md")
        return "ok"

    assert noisy() == "ok"
    captured = capsys.readouterr()
    assert captured.out == "", "nothing may be written to stdout"
    assert "Created /some/path.md" in captured.err, "it should surface on stderr"


def test_every_registered_tool_is_protocol_safe():
    # A new tool added without the guard reintroduces the bug silently, so
    # assert the property across the whole surface rather than case by case.
    import scripts.mcp_tools as mt

    entrypoints = [n for n in dir(mt) if n.startswith("tool_zurvan_")]
    assert entrypoints, "no tool entrypoints found"
    unguarded = [n for n in entrypoints
                 if not getattr(getattr(mt, n), "__wrapped__", None)]
    assert unguarded == [], f"tools missing the stdout guard: {unguarded}"


def test_write_tools_still_fail_closed_under_the_guard(monkeypatch):
    # _protocol_safe wraps enforce_read_only; the read-only gate must survive.
    import scripts.mcp_tools as mt

    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    assert "read-only mode" in mt.tool_zurvan_decision_add("t", "r", "accepted", [])
