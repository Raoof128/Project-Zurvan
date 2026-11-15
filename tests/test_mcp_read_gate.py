"""The Python MCP server's read allow-list (IOU-5), the same surface as the Rust
server's `security.rs`.

On 2026-10-19 the live Python server returned `.git/config`,
`.claude/settings.json`, `.remember/now.md` and an ingested legal document
under `sources/` through `zurvan_read_page`, because its gate only refused
absolute paths, traversal and `raw/`. Every case below runs in a throwaway root.
"""
import os
import sys

import pytest

from scripts import mcp_resources, mcp_security
from scripts.mcp_security import is_readable_path

UNSAFE = "Error: Path {} failed safety checks (no absolute paths, no '../', raw/ blocked)."
CASE_INSENSITIVE = sys.platform == "darwin"


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "store"
    for d in ("wiki/traces", "wiki/decisions", "docs/evaluation/retrieval", "docs/mcp", "eval",
              ".git", ".claude", ".remember", "sources", "data", "scripts", "raw"):
        (r / d).mkdir(parents=True, exist_ok=True)
    files = {
        "wiki/index.md": "# Index\n", "wiki/decisions/d.md": "decision\n", "wiki/traces/t.md": "TRACE SECRET\n",
        "docs/mcp/x.md": "doc\n", "docs/evaluation/retrieval/report.md": "REPORT SECRET\n",
        "eval/README.md": "eval\n", "README.md": "readme\n", "AGENTS.md": "a\n", "CLAUDE.md": "c\n",
        "CHANGELOG.md": "ch\n", ".git/HEAD": "GIT SECRET\n", ".claude/settings.json": "{}\n",
        ".remember/now.md": "REMEMBER SECRET\n", "sources/legal.md": "LEGAL SECRET\n",
        "data/registry.sqlite": "x", "scripts/cli.py": "x", "raw/r.md": "RAW SECRET\n", "notes.md": "x",
    }
    for p, body in files.items():
        (r / p).write_text(body)
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE SECRET\n")
    os.symlink(outside, r / "wiki" / "escape.md")
    os.symlink(r / ".git" / "HEAD", r / "wiki" / "to-git.md")
    os.symlink(r / "wiki" / "traces", r / "wiki" / "tracelink")
    os.symlink(r / "nowhere.md", r / "wiki" / "dangling.md")
    monkeypatch.setattr(mcp_resources, "PROJECT_ROOT", r)
    monkeypatch.setattr(mcp_security, "PROJECT_ROOT", r)
    monkeypatch.delenv("ZURVAN_MCP_ALLOW_RAW_READ", raising=False)
    return r


ALLOWED = ["wiki/index.md", "wiki/decisions/d.md", "docs/mcp/x.md", "eval/README.md",
           "README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md", "wiki/new-page.md"]
REFUSED = [".git/HEAD", "wiki/../.git/HEAD", ".claude/settings.json", ".remember/now.md",
           "sources/legal.md", "data/registry.sqlite", "scripts/cli.py", "notes.md",
           "wiki/traces/t.md", "wiki/escape.md", "wiki/to-git.md", "wiki/tracelink/t.md",
           "wiki/dangling.md", "docs/evaluation/retrieval/report.md", "raw/r.md",
           "/etc/passwd", "../outside.md", "wiki/missing/../../.git/HEAD", "", "."]


@pytest.mark.parametrize("path", ALLOWED)
def test_mcp_read_gate_allows_the_indexed_surface(root, path):
    assert is_readable_path(path) is True


@pytest.mark.parametrize("path", REFUSED)
def test_mcp_read_gate_refuses_everything_else(root, path):
    assert is_readable_path(path) is False


@pytest.mark.skipif(not CASE_INSENSITIVE, reason="needs a case-insensitive filesystem")
@pytest.mark.parametrize("path", ["WIKI/TRACES/t.md", "wiki/TRACES/t.md", "wiki/traceſ/t.md",
                                  ".GIT/HEAD", "Raw/r.md", "DOCS/Evaluation/Retrieval/report.md"])
def test_mcp_read_gate_judges_stored_names(root, path):
    # APFS folds case (with full Unicode rules: a long s opens `traces`), so a
    # check on the requested spelling would pass what the filesystem opens.
    assert is_readable_path(path) is False


@pytest.mark.skipif(not CASE_INSENSITIVE, reason="needs a case-insensitive filesystem")
def test_mcp_read_gate_case_variant_of_an_allowed_name_reads_the_stored_file(root):
    # `WIKI/index.md` opens the real `wiki/index.md`: judged on stored names it is allowed.
    assert is_readable_path("WIKI/index.md") is True


def test_mcp_read_gate_raw_needs_the_flag(root):
    assert is_readable_path("raw/r.md") is False
    assert is_readable_path("raw/r.md", allow_raw=True) is True


@pytest.mark.parametrize("path", [".git/HEAD", "sources/legal.md", "wiki/traces/t.md",
                                  "wiki/escape.md", "docs/evaluation/retrieval/report.md"])
def test_mcp_read_gate_readers_refuse_with_the_unsafe_text(root, path):
    for reader in (lambda p: mcp_resources.resource_file(p),
                   lambda p: mcp_resources.read_file_slice(p, 0, 100)):
        out = reader(path)
        assert out == UNSAFE.format(path)
        assert "SECRET" not in out


def test_mcp_read_gate_readers_still_serve_allowed_files(root):
    assert mcp_resources.resource_file("wiki/index.md") == "# Index\n"
    assert mcp_resources.read_file_slice("wiki/index.md", 0, 3) == "[wiki/index.md — characters 0-3] More: call again with offset=3.\n# I"
    assert mcp_resources.resource_file("wiki/missing.md") == "Error: File wiki/missing.md not found. Locate it with zurvan_search first."


def test_mcp_read_gate_static_resources_refuse_planted_symlinks(root):
    # Same finding as the Rust server's I4: the fixed wiki/eval resources read
    # the joined path directly, so a symlink planted at wiki/log.md was served.
    os.symlink(root / ".git" / "HEAD", root / "wiki" / "log.md")
    (root / "eval" / "README.md").unlink()
    os.symlink(root / "sources" / "legal.md", root / "eval" / "README.md")
    assert mcp_resources.resource_wiki_index() == "# Index\n"
    for out in (mcp_resources.resource_wiki_log(), mcp_resources.resource_eval_baseline()):
        assert out.startswith("Error") and "SECRET" not in out
    assert mcp_resources.resource_wiki_overview() == f"Error: Resource {root / 'wiki' / 'overview.md'} not found."


def test_mcp_read_gate_claim_source_is_allow_listed(root, monkeypatch):
    from scripts import mcp_tools
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    called = []
    monkeypatch.setattr(mcp_tools, "add_claim_detailed",
                        lambda *a, **k: called.append(a) or (True, None))
    import contextlib
    # No ledger operation may open against any real store from this test.
    monkeypatch.setattr("scripts.knowledge_revision.operation", lambda **k: contextlib.nullcontext())
    fn = mcp_tools.tool_zurvan_claim_add
    for src in (".git/HEAD", "sources/legal.md", "wiki/escape.md"):
        assert fn("t", src, "SECRET", "high", []) == mcp_tools.CLAIM_SOURCE_ERROR
    assert called == []
    assert fn("t", "wiki/index.md", "Index", "high", []) == "Claim added successfully: t"
