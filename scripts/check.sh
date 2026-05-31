#!/bin/bash
# Zurvan Comprehensive Quality Gate

set -e

echo "========================================="
echo "  Zurvan Comprehensive Quality Gate"
echo "========================================="

echo "[1/7] Running Public Repo Guard..."
PYTHONPATH=. python scripts/public_repo_guard.py
echo "✅ Public repo guard passed."
echo ""

echo "[2/7] Running Unit Tests (pytest)..."
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

echo "[7/10] Running MCP Doctor..."
PYTHONPATH=. python scripts/doctor_mcp.py
echo "✅ MCP Doctor passed."
echo ""

echo "[8/10] Testing MCP Server Integration..."
PYTHONPATH=. ZURVAN_MCP_READONLY=1 python -m pytest tests/test_mcp_security.py tests/test_mcp_tools.py tests/test_mcp_resources.py tests/test_mcp_server_smoke.py tests/test_doctor_mcp.py tests/test_install_mcp_config.py
if [ $? -ne 0 ]; then
    echo "❌ MCP server tests failed."
    exit 1
fi
echo "✅ MCP Server tests passed."
echo ""

echo "[9/10] Running MCP E2E smoke test..."
PYTHONPATH=. \
ZURVAN_MCP_TRANSPORT=stdio \
ZURVAN_MCP_READONLY=1 \
ZURVAN_MCP_ALLOW_RAW_READ=0 \
ZURVAN_LLM_PROVIDER=mock \
ZURVAN_LLM_MODEL=mock \
ZURVAN_EMBED_PROVIDER=mock \
python scripts/e2e_mcp_smoke.py
echo "✅ MCP E2E smoke test passed."
echo "[10/10] Agent Workflow Smoke Test..."
PYTHONPATH=. python scripts/cli.py session start --topic "Phase 7 smoke test" > /dev/null
PYTHONPATH=. python scripts/cli.py agent preflight --topic "Zurvan roadmap" --hybrid --graph --limit 10 > /dev/null
PYTHONPATH=. python scripts/cli.py agent postedit \
  --summary "Ran Phase 7 smoke test." \
  --files scripts/agent_workflow.py scripts/session.py \
  --checks "pytest tests/ && bash scripts/check.sh" > /dev/null
PYTHONPATH=. python scripts/cli.py session close \
  --topic "Phase 7 smoke test" \
  --summary "Phase 7 workflow smoke test passed." \
  --checks "pytest tests/ && bash scripts/check.sh" > /dev/null
echo "✅ Agent Workflow smoke test passed."
echo ""

echo "[11/13] Running Version Check..."
PYTHONPATH=. python scripts/cli.py version
echo "✅ Version check passed."
echo ""

echo "[12/13] Running Doctor Check..."
PYTHONPATH=. python scripts/cli.py doctor
echo "✅ Doctor check passed."
echo ""

echo "[13/13] Running Snapshot Smoke Test..."
PYTHONPATH=. python scripts/cli.py snapshot create
PYTHONPATH=. python scripts/cli.py snapshot list
echo "✅ Snapshot smoke test passed."
echo ""

echo "========================================="
echo "🎉 All Zurvan checks passed successfully."
echo "========================================="
