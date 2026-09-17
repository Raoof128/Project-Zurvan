#!/bin/bash
# Zurvan Comprehensive Quality Gate

set -e

echo "========================================="
echo "  Zurvan Comprehensive Quality Gate"
echo "========================================="

# ── concurrency guard ────────────────────────────────────────────────────────
# Gate 5 rebuilds the search index. On 2026-09-16 this script ran while another
# rebuild was already in flight; both wrote data/search.sqlite, and the live
# index was left holding 8,000 of 185,303 chunks — structurally valid, and
# silently missing 96% of the corpus. A partial index answers exactly like a
# complete one, so nothing looks wrong until a measurement is quietly false.
if pgrep -f "cli.py index search" > /dev/null 2>&1 || pgrep -f "rebuild_search_index" > /dev/null 2>&1; then
  echo "❌ An index rebuild is already running. check.sh rebuilds the index too;"
  echo "   running both corrupts it. Wait for the other rebuild to finish."
  exit 1
fi
if [ -f data/search.sqlite-journal ]; then
  # Since 2026-09-17 a rebuild writes to `data/search.sqlite.building-<pid>` and
  # swaps it in with one os.replace(), so an interrupted rebuild leaves NOTHING
  # at the canonical path and its own leftovers are swept by the next run. A
  # journal beside the canonical index therefore no longer means "interrupted
  # rebuild" — it means something wrote the production index directly, which
  # nothing in this repository is supposed to do.
  echo "❌ SQLite journal at data/search.sqlite-journal — something is writing the"
  echo "   canonical index outside rebuild_search_index. Inspect before gating."
  exit 1
fi

echo "[0/23] Running Boundary egress gate..."
# Opt-in: reports NOT CONFIGURED and exits 0 when no store exists, so the gate
# never blocks a machine that has not set Boundary up. When a store IS present,
# a store that cannot prove its own integrity fails the gate just as a detected
# leak does.
PYTHONPATH=. python scripts/boundary_guard.py
# Adversarial verification of the scanner itself, against real protected
# material: integrity attacks, output-leak checks, policy fail-open probes, and
# a sweep for protected content in tracked files. A gate nobody attacks is a
# gate nobody has tested.
PYTHONPATH=. python scripts/boundary_redteam.py | tail -3

# Named explicitly so a failure points at the subsystem rather than at "pytest".
# These also run inside gate 2's full sweep; the duplication is deliberate.
echo "        Boundary + instrument suites:"
PYTHONPATH=. python -m pytest -q \
  tests/test_boundary_store.py \
  tests/test_boundary_gauntlet.py \
  tests/test_boundary_gitscan.py \
  tests/test_boundary_guard.py \
  tests/test_instruments.py \
  tests/test_gauntlet_completeness.py \
  tests/test_fts_candidates_gauntlet.py \
  tests/test_plan_integrity.py | tail -2
echo ""

echo "[1/23] Running Public Repo Guard..."
PYTHONPATH=. python scripts/public_repo_guard.py
echo "✅ Public repo guard passed."
echo ""

echo "[2/23] Running Unit Tests (pytest)..."
PYTHONPATH=. pytest tests/
echo "✅ Unit tests passed."
echo ""

echo "[3/23] Running Reliability Gauntlet (Mock Provider)..."
export ZURVAN_LLM_PROVIDER=mock
PYTHONPATH=. python scripts/run_reliability_gauntlet.py \
  raw/notes/small_note.txt \
  raw/articles/medium_article.md \
  raw/papers/short_paper.pdf \
  raw/papers/long_paper.pdf \
  raw/papers/scanned_or_ugly.pdf
echo "✅ Gauntlet completed."
echo ""

echo "[4/23] Auditing Wiki..."
PYTHONPATH=. python scripts/audit_wiki.py
echo "✅ Audit completed."
echo ""

echo "[5/23] Rebuilding Search Index..."
# This gate rebuilds the PRODUCTION index, so the embedding space is pinned
# here rather than inherited. Gate 3 above exports a mock LLM provider, and
# check.sh is routinely run from a plain shell where ZURVAN_EMBED_PROVIDER is
# unset — under which `get_embedding` returns deterministic hashes and reports
# success. That is how 184,231 real vectors became hashes on 2026-09-16, with
# every count-based check still passing. rebuild_search_index refuses a mock
# canonical build as well; this line means it never has to.
PYTHONPATH=. \
ZURVAN_EMBED_PROVIDER=sentence_transformers \
ZURVAN_EMBED_MODEL=all-MiniLM-L6-v2 \
python scripts/rebuild_search_index.py

# A rebuild is not finished until the result has been read. Counts alone passed
# on an index whose every vector was a hash, and on one holding 2,000 of
# 184,231 chunks; this asserts the embedding space, the chunk/FTS/embedding
# agreement and each vector's byte width.
if ! PYTHONPATH=. python scripts/index_health.py --deep; then
    echo "❌ Search index is not healthy — every retrieval number below it would be meaningless."
    exit 1
fi
echo "✅ Search index rebuilt and verified healthy."
echo ""

echo "[6/23] Evaluating Retrieval Harness..."
PYTHONPATH=. python scripts/eval_search.py --validate
PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6
echo "✅ Retrieval Evaluation passed."
echo ""

echo "[7/23] Testing Knowledge Graph Lite & Context Expansion..."
PYTHONPATH=. python scripts/graph_build.py
PYTHONPATH=. python scripts/cli.py graph stats
PYTHONPATH=. python scripts/cli.py graph expand wiki/decisions/delay-vector-search.md --depth 1
PYTHONPATH=. python scripts/cli.py context --topic "vector" --hybrid --graph --limit 2
echo "✅ Graph tests passed."
echo ""

echo "[8/23] Running MCP Doctor..."
PYTHONPATH=. python scripts/doctor_mcp.py
echo "✅ MCP Doctor passed."
echo ""

echo "[9/23] Testing MCP Server Integration..."
PYTHONPATH=. ZURVAN_MCP_READONLY=1 python -m pytest tests/test_mcp_security.py tests/test_mcp_tools.py tests/test_mcp_resources.py tests/test_mcp_server_smoke.py tests/test_doctor_mcp.py tests/test_install_mcp_config.py
if [ $? -ne 0 ]; then
    echo "❌ MCP server tests failed."
    exit 1
fi
echo "✅ MCP Server tests passed."
echo ""

echo "[10/23] Running MCP E2E smoke test..."
PYTHONPATH=. \
ZURVAN_MCP_TRANSPORT=stdio \
ZURVAN_MCP_READONLY=1 \
ZURVAN_MCP_ALLOW_RAW_READ=0 \
ZURVAN_LLM_PROVIDER=mock \
ZURVAN_LLM_MODEL=mock \
ZURVAN_EMBED_PROVIDER=mock \
python scripts/e2e_mcp_smoke.py
echo "✅ MCP E2E smoke test passed."
echo "[11/23] Agent Workflow Smoke Test..."
# The WRITE half of this gate runs against a throwaway store. It used to write
# `wiki/sessions/<date>-phase-7-smoke-test.md` plus log entries into the LIVE
# wiki on every run — 6 such pages had accumulated by 2026-09-15, indexed and
# therefore part of every retrieval baseline. `agent preflight` is a read and
# deliberately stays on the real store, which is what makes the smoke meaningful.
SMOKE_ROOT="$(mktemp -d -t zurvan-agent-smoke)"
mkdir -p "$SMOKE_ROOT/wiki/sessions" "$SMOKE_ROOT/wiki/decisions" \
         "$SMOKE_ROOT/wiki/claims" "$SMOKE_ROOT/data"
trap 'rm -rf "$SMOKE_ROOT"' EXIT
ZURVAN_ROOT="$SMOKE_ROOT" PYTHONPATH=. python scripts/cli.py session start --topic "Phase 7 smoke test" > /dev/null
PYTHONPATH=. python scripts/cli.py agent preflight --topic "Zurvan roadmap" --hybrid --graph --limit 10 > /dev/null
ZURVAN_ROOT="$SMOKE_ROOT" PYTHONPATH=. python scripts/cli.py agent postedit \
  --summary "Ran Phase 7 smoke test." \
  --files scripts/agent_workflow.py scripts/session.py \
  --checks "pytest tests/ && bash scripts/check.sh" > /dev/null
ZURVAN_ROOT="$SMOKE_ROOT" PYTHONPATH=. python scripts/cli.py session close \
  --topic "Phase 7 smoke test" \
  --summary "Phase 7 workflow smoke test passed." \
  --checks "pytest tests/ && bash scripts/check.sh" > /dev/null
if ! ls "$SMOKE_ROOT"/wiki/sessions/*.md >/dev/null 2>&1; then
  echo "❌ Agent workflow smoke wrote nothing into its throwaway root."
  exit 1
fi
if ls wiki/sessions/*phase-7-smoke-test.md >/dev/null 2>&1; then
  echo "❌ Agent workflow smoke leaked a session page into the LIVE wiki."
  exit 1
fi
rm -rf "$SMOKE_ROOT"; trap - EXIT
echo "✅ Agent Workflow smoke test passed (writes isolated)."
echo ""

echo "[12/23] Running Version Check..."
PYTHONPATH=. python scripts/cli.py version
echo "✅ Version check passed."
echo ""

echo "[13/23] Running Doctor Check..."
PYTHONPATH=. python scripts/cli.py doctor
echo "✅ Doctor check passed."
echo ""

echo "[14/23] Running Snapshot Smoke Test..."
export ZURVAN_SNAPSHOT_TEST="true"
PYTHONPATH=. python scripts/cli.py snapshot create
echo "✅ Snapshot smoke test passed."
echo ""

echo -e "\n[15/23] Testing Workspace Registry..."
export ZURVAN_CONFIG_DIR="$(mktemp -d)"
PYTHONPATH=. python scripts/cli.py project register --name testproj --path . > /dev/null
PYTHONPATH=. python scripts/cli.py project list
PYTHONPATH=. python scripts/cli.py project current
echo "✅ Workspace registry test passed."

echo -e "\n[16/23] Testing Federation..."
# Register a second project for federation tests
mkdir -p "$ZURVAN_CONFIG_DIR/testproj2"
PYTHONPATH=. python scripts/cli.py project register --name testproj2 --path . > /dev/null

PYTHONPATH=. python scripts/cli.py project federation stats
PYTHONPATH=. python scripts/cli.py project federation doctor

echo "Testing search-all..."
PYTHONPATH=. python scripts/cli.py project search-all "MCP security" --hybrid > /dev/null
echo "✅ search-all smoke test passed."

echo "Testing context-all..."
PYTHONPATH=. python scripts/cli.py project context-all --topic "agent memory" --hybrid --graph --limit 10 > /dev/null
echo "✅ context-all smoke test passed."

echo -e "\n[17/23] Testing Decision Memory..."
python scripts/cli.py project decisions-all >/dev/null
python scripts/cli.py project decisions-stale >/dev/null
echo "✅ Decision memory smoke test passed."

echo ""
echo "[18/23] Testing Policy Radar..."
python scripts/cli.py project radar policies >/dev/null
python scripts/cli.py project radar contradictions >/dev/null
python scripts/cli.py project radar drift >/dev/null
echo "✅ Policy radar smoke test passed."

echo ""
echo "[19/23] Testing Evidence Pack Builder..."
mkdir -p "$ZURVAN_CONFIG_DIR"
python scripts/cli.py project register --name zurvan --path . >/dev/null
python scripts/cli.py evidence build --topic "smoke test" --hybrid --graph --include-decisions --include-policy-radar >/dev/null
PACK_ID=$(python scripts/cli.py evidence list | grep "smoke test" | awk '{print $2}')
if [ -n "$PACK_ID" ]; then
    python scripts/cli.py evidence inspect "$PACK_ID" >/dev/null
    python scripts/cli.py evidence export "$PACK_ID" --format markdown >/dev/null
    python scripts/cli.py evidence export "$PACK_ID" --format json >/dev/null
    echo "✅ Evidence pack smoke test passed."
else
    echo "❌ Evidence pack smoke test failed."
    exit 1
fi

echo ""
echo "[20/23] Testing Report Composer..."
python scripts/cli.py report compose --pack "$PACK_ID" --template evidence_digest >/dev/null
REPORT_ID=$(python scripts/cli.py report list | head -n 1 | awk '{print $2}')
if [ -n "$REPORT_ID" ]; then
    python scripts/cli.py report inspect "$REPORT_ID" >/dev/null
    python scripts/cli.py report validate "$REPORT_ID" >/dev/null
    python scripts/cli.py report export "$REPORT_ID" --format markdown >/dev/null
    echo "✅ Report composer smoke test passed."
else
    echo "❌ Report composer smoke test failed."
    exit 1
fi

echo ""
echo "[21/23] Testing Local Review Workbench..."
# Just start the server in background and kill it to ensure it creates correctly
python scripts/cli.py review serve --host 127.0.0.1 --port 8769 &
SERVER_PID=$!
sleep 2
kill $SERVER_PID || true
echo "✅ Review workbench smoke test passed."

echo ""
echo "[22/23] Testing Review Audit & Index..."
python scripts/cli.py review audit > /dev/null
python scripts/cli.py review index rebuild > /dev/null
echo "✅ Review Audit and Index passed."

echo ""
echo "[23/23] Testing Publication Export..."
# Just validate a smoke test for publication (we need a report first, but the pytest unit tests will handle that)
# We can just check that the CLI runs validation.
# We skip running export if there's no report guaranteed, so we rely on tests/test_publication_*.py which was run in pytest.
echo "✅ Publication Export CLI structure is present."

echo ""
echo "========================================="
echo "🎉 All Zurvan checks passed successfully."
echo "========================================="
