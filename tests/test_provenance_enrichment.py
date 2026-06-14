"""Step 1B: provenance event enrichment.

Covers the three additive changes:
  A. retrieval.fusion event (hybrid only, observe-only)
  B. scoring the existing graph_context event (gold-driven, no new event)
  C. populating context.assembled.dropped with genuine drop reasons
"""

import json

import pytest

import scripts.context_export as context_export
from scripts.trace_schema import TraceEvent, utc_now


# --- A. retrieval.fusion schema + payload --------------------------------

def test_schema_allows_retrieval_fusion_event_type():
    # Must not raise: retrieval.fusion is a recognised event type.
    event = TraceEvent(
        event_id="evt-003",
        event_type="retrieval.fusion",
        timestamp=utc_now(),
        actor="zurvan",
        payload={"mode": "hybrid"},
    )
    assert event.event_type == "retrieval.fusion"


def test_fusion_payload_records_observed_weights_and_ranks():
    results = [
        {"chunk_id": "a", "keyword_score": 0.9, "semantic_score": 0.5, "hybrid_score": 0.74},
        {"chunk_id": "b", "keyword_score": 0.2, "semantic_score": 0.8, "hybrid_score": 0.44},
    ]
    payload = context_export._fusion_payload(results)

    assert payload["mode"] == "hybrid"
    assert payload["fusion"] == "weighted_sum"
    assert payload["weights"] == {"fts": 0.6, "embedding": 0.4}
    assert payload["ranked"][0] == {
        "chunk_id": "a",
        "fts_score": 0.9,
        "embedding_score": 0.5,
        "fused_score": 0.74,
        "rank": 1,
    }
    assert payload["ranked"][1]["rank"] == 2


# --- C. context.assembled.dropped ----------------------------------------

def test_assembled_payload_empty_dropped_is_explicit():
    payload = context_export._assembled_context_payload([], dropped=[])
    assert payload["dropped"] == []
    assert payload["dropped_reason"] == "no_dropped_context"


def test_assembled_payload_passes_through_real_drops():
    dropped = [{"chunk_id": "x", "reason": "budget"}]
    payload = context_export._assembled_context_payload([], dropped=dropped)
    assert payload["dropped"] == dropped
    assert "dropped_reason" not in payload


def test_apply_budget_genuinely_drops_over_limit_chunks():
    # Three matches, limit of 1 -> two genuinely dropped with a real reason.
    matches = [
        {"chunk_id": "keep", "source_path": "wiki/a.md", "text": "a"},
        {"chunk_id": "cut1", "source_path": "wiki/b.md", "text": "b"},
        {"chunk_id": "cut2", "source_path": "wiki/c.md", "text": "c"},
    ]
    included, dropped = context_export._apply_budget(matches, limit=1)

    assert [m["chunk_id"] for m in included] == ["keep"]
    assert dropped == [
        {"chunk_id": "cut1", "reason": "budget"},
        {"chunk_id": "cut2", "reason": "budget"},
    ]


# --- A+C integration in the emitted trace --------------------------------

def _hybrid_results():
    return [
        {
            "chunk_id": "chunk-001",
            "source_path": "wiki/sources/mcp.md",
            "heading": "MCP",
            "text": "MCP provenance and retrieval tracing.",
            "keyword_score": 0.75,
            "semantic_score": 0.5,
            "hybrid_score": 0.65,
        }
    ]


def test_hybrid_context_trace_emits_retrieval_fusion(tmp_path, monkeypatch):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        context_export, "_search_internal", lambda *a, **k: _hybrid_results()
    )

    context_export.export_context(
        "MCP",
        hybrid=True,
        trace=True,
        trace_id="trace-20260614T010101Z-fusion01",
    )
    data = json.loads(
        (tmp_path / "data" / "traces" / "trace-20260614T010101Z-fusion01.json").read_text()
    )
    types = [e["event_type"] for e in data["events"]]

    assert types == [
        "retrieval.query",
        "retrieval.result",
        "retrieval.fusion",
        "context.assembled",
    ]
    fusion = data["events"][2]["payload"]
    assert fusion["weights"] == {"fts": 0.6, "embedding": 0.4}
    assert fusion["ranked"][0]["chunk_id"] == "chunk-001"


def test_keyword_context_trace_omits_retrieval_fusion(tmp_path, monkeypatch):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        context_export, "_search_internal", lambda *a, **k: _hybrid_results()
    )

    context_export.export_context(
        "MCP",
        hybrid=False,
        trace=True,
        trace_id="trace-20260614T010101Z-keyword1",
    )
    data = json.loads(
        (tmp_path / "data" / "traces" / "trace-20260614T010101Z-keyword1.json").read_text()
    )
    types = [e["event_type"] for e in data["events"]]

    assert "retrieval.fusion" not in types
    assert types == ["retrieval.query", "retrieval.result", "context.assembled"]
