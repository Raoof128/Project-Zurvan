import sys

def test_mcp_server_import():
    # Just ensure it imports without crashing
    import scripts.mcp_server
    assert scripts.mcp_server.mcp.name == "Zurvan"


# ── SDK v2 / protocol 2026-07-28 ──────────────────────────────────────────────

def test_server_runs_on_sdk_v2():
    # v1.x is maintenance-only (security fixes), and the {+path} template below
    # cannot even be registered on it.
    import importlib.metadata as md
    from mcp.server.mcpserver import MCPServer

    import scripts.mcp_server as srv

    assert int(md.version("mcp").split(".")[0]) >= 2
    assert isinstance(srv.mcp, MCPServer)


def test_nested_resource_paths_resolve():
    # Regression: the template was `zurvan://file/{path}` — RFC 6570 SIMPLE
    # expansion, which does not match "/". Every path in this repo is nested,
    # so zurvan://file/wiki/... was an unknown-resource error and the template
    # only ever worked for root-level files. `{+path}` is reserved expansion.
    import asyncio

    import scripts.mcp_server as srv

    templates = asyncio.run(srv.mcp.list_resource_templates())
    assert [t.uri_template for t in templates] == ["zurvan://file/{+path}"]

    result = asyncio.run(srv.mcp.read_resource("zurvan://file/README.md"))
    assert "Zurvan" in str(list(result)[0].content)

    nested = asyncio.run(srv.mcp.read_resource("zurvan://file/docs/mcp/security.md"))
    assert "Error" not in str(list(nested)[0].content)[:40]


def test_exempting_the_sdk_path_check_does_not_weaken_zurvans_own_gate():
    # `exempt_params={"path"}` turns off the SDK's traversal rejection so "/"
    # can pass. is_safe_path must remain the real gate.
    import asyncio

    import scripts.mcp_server as srv

    for uri in ("zurvan://file/../../etc/passwd", "zurvan://file/raw/papers/x.pdf"):
        content = str(list(asyncio.run(srv.mcp.read_resource(uri)))[0].content)
        assert content.startswith("Error: Path"), f"{uri} was not blocked: {content[:80]}"


def test_list_results_carry_private_cache_hints():
    # 2026-07-28 CacheableResult. These listings describe a personal knowledge
    # base, so a shared intermediary must never cache them.
    import scripts.mcp_server as srv

    from mcp.server.caching import CACHEABLE_METHODS

    hints = srv.mcp._lowlevel_server.cache_hints
    assert set(hints) >= {"tools/list", "prompts/list", "resources/list"}
    assert set(hints) <= set(CACHEABLE_METHODS), "only cacheable methods may carry a hint"
    for method, hint in hints.items():
        assert hint.scope == "private", f"{method} must not be publicly cacheable"
        assert hint.ttl_ms > 0


def test_initialize_carries_instructions():
    # Design §7A A1: the server explains itself on `initialize`, so an agent
    # without the Zurvan skill installed still learns the workflow. The text is
    # shared with the Rust server (rust/zurvan-mcp/instructions.md), so the
    # reference and the replacement say the same thing.
    from pathlib import Path

    import scripts.mcp_server as srv

    expected = (Path(srv.__file__).resolve().parent.parent / "rust" / "zurvan-mcp" / "instructions.md").read_text(encoding="utf-8")
    assert expected.strip(), "instructions.md is empty"
    assert srv.mcp.instructions == expected
