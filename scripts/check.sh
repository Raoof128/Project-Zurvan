#!/bin/bash
# Zurvan Comprehensive Quality Gate

set -e

echo "========================================="
echo "  Zurvan Comprehensive Quality Gate"
echo "========================================="

echo "[1/6] Running Unit Tests (pytest)..."
PYTHONPATH=. pytest tests/
echo "✅ Unit tests passed."
echo ""

echo "[2/6] Running Reliability Gauntlet (Mock Provider)..."
export ZURVAN_LLM_PROVIDER=mock
PYTHONPATH=. python scripts/run_reliability_gauntlet.py \
  raw/notes/small_note.txt \
  raw/articles/medium_article.md \
  raw/papers/short_paper.pdf \
  raw/papers/long_paper.pdf \
  raw/papers/scanned_or_ugly.pdf
echo "✅ Gauntlet completed."
echo ""

echo "[3/6] Auditing Wiki..."
PYTHONPATH=. python scripts/audit_wiki.py
echo "✅ Audit completed."
echo ""

echo "[4/6] Rebuilding Search Index..."
PYTHONPATH=. python scripts/rebuild_search_index.py
echo "✅ Search index rebuilt."
echo ""

echo "[5/6] Evaluating Retrieval Harness..."
PYTHONPATH=. python scripts/eval_search.py --validate
PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6
echo "✅ Retrieval Evaluation passed."
echo ""

echo "[6/6] Testing Knowledge Graph Lite & Context Expansion..."
PYTHONPATH=. python scripts/graph_build.py
PYTHONPATH=. python scripts/cli.py graph stats
PYTHONPATH=. python scripts/cli.py graph expand wiki/decisions/delay-vector-search.md --depth 1
PYTHONPATH=. python scripts/cli.py context --topic "vector" --hybrid --graph --limit 2
echo "✅ Graph tests passed."
echo ""

echo "[7/8] Testing MCP Server Integration..."
PYTHONPATH=. ZURVAN_MCP_READONLY=1 python -m pytest tests/test_mcp_security.py tests/test_mcp_tools.py tests/test_mcp_resources.py tests/test_mcp_server_smoke.py
echo "✅ MCP Server tests passed."
echo ""

echo "[8/8] Running MCP E2E smoke test..."
PYTHONPATH=. \
ZURVAN_MCP_TRANSPORT=stdio \
ZURVAN_MCP_READONLY=1 \
ZURVAN_MCP_ALLOW_RAW_READ=0 \
ZURVAN_LLM_PROVIDER=mock \
ZURVAN_LLM_MODEL=mock \
ZURVAN_EMBED_PROVIDER=mock \
python scripts/e2e_mcp_smoke.py
echo "✅ MCP E2E smoke test passed."
echo ""

echo "========================================="
echo "🎉 All Zurvan checks passed successfully."
echo "========================================="
