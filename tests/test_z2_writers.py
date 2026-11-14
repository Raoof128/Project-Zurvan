"""Z2 Task 8: every claim writer goes through the ledger (spec §7.9, gate G9).

memory.add_claim, extraction, and the MCP tools (Python, and the Rust bridge
that calls them) all write claims via `claim_ledger`; only `claim_pages`
writes `wiki/claims/`.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from scripts import claim_ledger as cl
from scripts import knowledge_revision as kr
from scripts.config import PROJECT_ROOT

REPO = Path(__file__).resolve().parents[1]
QUOTE = "Zurvan turns raw sources into a persistent Markdown wiki..."


@pytest.fixture
def root(tmp_path, monkeypatch):
    prompts = tmp_path / "scripts" / "prompts"
    prompts.mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "scripts" / "prompts" / "extract_source.md",
                prompts / "extract_source.md")
    for d in ("wiki/claims", "wiki/decisions", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "decisions" / "ev.md").write_text("the evidence line\nmore\n",
                                                          encoding="utf-8")
    monkeypatch.delenv("ZURVAN_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "0")
    monkeypatch.chdir(tmp_path)
    kr.ensure_schema(tmp_path)
    return tmp_path


def _src(root, rel, tail):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{QUOTE}\n{tail}\n", encoding="utf-8")
    return rel


def _extract(rel):
    from scripts.extract import extract_source
    with kr.operation(writer="extract", eager=True):
        extract_source(rel)


def test_add_claim_writes_ledger_and_page(root):
    from scripts.memory import add_claim
    with kr.operation(writer="cli", caller="cli"):
        assert add_claim("a fact", "wiki/decisions/ev.md", "the evidence line", "high",
                         ["t"]) is True
    (state,) = cl.current(root).values()
    assert state.text == "a fact" and state.tags == ["t"]
    assert (root / "wiki" / "claims" / f"{state.claim_id}.md").is_file()
    assert cl.history(state.claim_id, root)[0]["method"] == "human"


def test_add_claim_refusal_returns_false(root):
    from scripts.memory import add_claim
    with kr.operation(writer="cli"):
        assert add_claim("x", "wiki/decisions/ev.md", "not there", "high", []) is False
        assert add_claim("x", "wiki/decisions/ev.md", "the evidence line", "0.9", []) is False
    assert cl.current(root) == {}


def test_f1_two_sources_same_model_id_two_claims(root):
    _extract(_src(root, "a.md", "A."))
    _extract(_src(root, "b.md", "B."))
    claims = cl.current(root)
    assert len(claims) == 2
    assert {c.evidence[0]["source"] for c in claims.values()} == {"a.md", "b.md"}
    assert all(c.evidence[0]["reader"] == "extract_text@1" for c in claims.values())
    assert sorted(p.name for p in (root / "wiki" / "claims").glob("*.md")) == sorted(
        f"{c}.md" for c in claims)


def test_reextraction_identical_is_noop(root):
    rel = _src(root, "source.md", "Same.")
    _extract(rel)
    before = kr.current(root).revision
    _extract(rel)
    (state,) = cl.current(root).values()
    assert state.rev == 1
    assert kr.current(root).revision in (before, before + 1)   # summaries may rewrite


def test_reextraction_changed_text_conflicts(root, monkeypatch):
    from scripts import extract
    rel = _src(root, "source.md", "Body.")
    _extract(rel)
    (state,) = cl.current(root).values()
    real = extract.run_llm

    def changed(*a, **k):
        data = json.loads(real(*a, **k))
        data["claims"][0]["text"] = "a different claim text"
        return json.dumps(data)

    monkeypatch.setattr(extract, "run_llm", changed)
    with pytest.raises(extract.ExtractionConflict):
        _extract(rel)
    assert cl.current(root)[state.claim_id].text == state.text
    assert len(cl.current(root)) == 1


def test_mcp_claim_add_records_agent(root):
    from scripts import mcp_tools as tools
    out = tools.tool_zurvan_claim_add("mcp fact", "wiki/decisions/ev.md", "the evidence line",
                                      "high", ["m"])
    assert out.startswith("Claim added successfully"), out
    (state,) = cl.current(root).values()
    h = cl.history(state.claim_id, root)[0]
    assert h["method"] == "agent" and h["actor"].startswith("mcp:")


def test_mcp_supersede_retract_show_history(root):
    from scripts import mcp_tools as tools
    tools.tool_zurvan_claim_add("old fact", "wiki/decisions/ev.md", "the evidence line", "high", [])
    (old,) = cl.current(root)
    out = tools.tool_zurvan_claim_supersede(old, "new fact", "wiki/decisions/ev.md", "more",
                                            "updated", None, 1)
    assert out.startswith("Claim superseded"), out
    new = next(c for c in cl.current(root) if c != old)
    assert tools.tool_zurvan_claim_retract(new, "wrong", ["agent_inference"], None).startswith(
        "Claim retracted")
    shown = json.loads(tools.tool_zurvan_claim_show(old))
    assert shown["status"] == "superseded"
    assert [h["event"] for h in json.loads(tools.tool_zurvan_claim_history(old))] == [
        "assert", "supersede"]
    stale = tools.tool_zurvan_claim_retract(old, "x", [], 1)
    assert stale.startswith("Error: CLAIM_CONFLICT")


def test_mcp_supersede_respects_readonly(root, monkeypatch):
    from scripts import mcp_tools as tools
    tools.tool_zurvan_claim_add("old fact", "wiki/decisions/ev.md", "the evidence line", "high", [])
    (old,) = cl.current(root)
    monkeypatch.setenv("ZURVAN_MCP_READONLY", "1")
    out = tools.tool_zurvan_claim_supersede(old, "n", "wiki/decisions/ev.md", "more", "r", None, None)
    assert "read-only" in out.lower() or out.startswith("Error")
    assert len(cl.current(root)) == 1


def test_mcp_supersede_source_allow_list(root):
    from scripts import mcp_tools as tools
    tools.tool_zurvan_claim_add("old fact", "wiki/decisions/ev.md", "the evidence line", "high", [])
    (old,) = cl.current(root)
    assert tools.tool_zurvan_claim_supersede(old, "n", "../etc/passwd", "root", "r", None,
                                             None) == tools.CLAIM_SOURCE_ERROR


def test_bridge_maps_the_claim_tools():
    from scripts import mcp_bridge
    assert mcp_bridge.WRITES["claim_supersede"] == "tool_zurvan_claim_supersede"
    assert mcp_bridge.WRITES["claim_retract"] == "tool_zurvan_claim_retract"
    assert mcp_bridge.CLAIM_READS == {"claim_show": "tool_zurvan_claim_show",
                                      "claim_history": "tool_zurvan_claim_history"}


#: Every module that names the claims directory, and why it may. A new module
#: that references `wiki/claims` fails this test until it is reviewed (spec G9:
#: no module but the renderer writes claim pages).
REVIEWED = {
    "claim_pages.py": "THE writer of wiki/claims (renderer)",
    "claim_store.py": "reads pages for the MIGRATION_REQUIRED guard",
    "claim_visibility.py": "reads frontmatter to hide non-current pages",
    "claim_migrate.py": "reads legacy pages; writes only through claim_pages",
    "agent_workflow.py": "reads pages for the recall digest",
    "claim_federation.py": "reads pages for federation discovery",
    "build_snapshot.py": "snapshot component names, no page I/O",
    "measurement_snapshot.py": "snapshot component names, no page I/O",
    "knowledge_scope.py": "scope definition, no page I/O",
    "e2e_mcp_smoke.py": "creates an empty dir in a throwaway root",
    "mcp_parity.py": "creates an empty dir in a throwaway root",
    "extract.py": "extracts claims; writes them through claim_ledger only",
    "memory.py": "add_claim writes through claim_ledger only",
    "ingest.py": "comment: nothing may pre-create a claim page",
    "llm.py": "the mock extraction's JSON key 'claims'",
    "validate_extraction.py": "the extraction JSON key 'claims'",
    "prune_stub_pages.py": "deletes only frontmatter-less legacy stubs; generated pages carry type: claim",
    "public_repo_guard.py": "blocks publication of wiki/claims",
    "publication_export.py": "reads for export",
    "publication_templates.py": "template section names",
    "rebuild_index.py": "reads for indexing",
    "report_compose.py": "reads for reports",
    "report_export.py": "reads for reports",
    "review_audit.py": "reads for review",
    "review_index.py": "reads for review",
    "run_reliability_gauntlet.py": "reads for the gauntlet",
}


def test_only_renderer_writes_wiki_claims():
    pattern = re.compile(r"wiki/claims|[\"']claims[\"']")
    found = {p.name for p in (REPO / "scripts").glob("*.py")
             if pattern.search(p.read_text(encoding="utf-8"))}
    assert found <= set(REVIEWED), sorted(found - set(REVIEWED))
    for name in ("memory.py", "extract.py"):
        text = (REPO / "scripts" / name).read_text(encoding="utf-8")
        assert not re.search(r"open\([^)]*claim[^)]*[\"']w", text), name
        assert "claims_dir" not in text, name


def test_prune_never_selects_a_generated_page(tmp_path):
    from scripts.prune_stub_pages import build_manifest
    claims = tmp_path / "claims"
    concepts = tmp_path / "concepts"
    claims.mkdir()
    concepts.mkdir()
    (claims / "clm_x.md").write_text('---\ntype: "claim"\nauthority: "structured"\n---\n\n'
                                     "# Claim\nEvidence missing? No, cited from x\n",
                                     encoding="utf-8")
    assert build_manifest(claims, concepts) == []
