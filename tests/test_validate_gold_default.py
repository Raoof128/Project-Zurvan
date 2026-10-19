"""IOU-11: the gold set's default path resolves at call time.

`DEFAULT_GOLD` in scripts/eval_search.py is a `None` sentinel (the cleartext is
local-only and may be absent), but `tool_zurvan_validate_gold` and
`failure_anatomy.analyse` passed it straight through, so the MCP tool answered
`Error: expected str, bytes or os.PathLike object, not NoneType` on every call.
"""
import json

from scripts import eval_search, mcp_tools


def _gold(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("x")
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"query": "q", "expected_paths": [str(page)]}) + "\n")
    return gold


def test_validate_gold_tool_uses_the_default_instrument(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    monkeypatch.setattr(eval_search, "_gold_path", lambda: gold)
    out = mcp_tools.tool_zurvan_validate_gold()
    assert not out.startswith("Error"), out
    assert "validated successfully" in out


def test_load_gold_dataset_accepts_the_none_default(tmp_path, monkeypatch):
    gold = _gold(tmp_path)
    monkeypatch.setattr(eval_search, "_gold_path", lambda: gold)
    assert len(eval_search.load_gold_dataset(eval_search.DEFAULT_GOLD)) == 1
    assert eval_search.validate_gold_dataset(None, quiet=True) is True
