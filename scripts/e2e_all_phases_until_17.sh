#!/usr/bin/env bash
set -euo pipefail

echo "========================================="
echo "Zurvan Phase 1-17 Full E2E Verification"
echo "========================================="

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes."
  echo "Commit/stash first so the E2E worktree is clean."
  exit 1
fi

ROOT="$(pwd)"
BRANCH="e2e-phase17-$(date +%Y%m%d-%H%M%S)"
TMP_REPO="$(mktemp -d)/zurvan-e2e"
TMP_CONFIG="$(mktemp -d)"

cleanup() {
  cd "$ROOT" >/dev/null 2>&1 || true
  git worktree remove --force "$TMP_REPO" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[0/24] Creating isolated git worktree..."
git worktree add "$TMP_REPO" -b "$BRANCH" >/dev/null
cd "$TMP_REPO"

export PYTHONPATH=.
export ZURVAN_CONFIG_DIR="$TMP_CONFIG"
export ZURVAN_LLM_PROVIDER=mock
export ZURVAN_LLM_MODEL=mock
export ZURVAN_EMBED_PROVIDER=mock
export ZURVAN_MCP_READONLY=1
export ZURVAN_MCP_TRANSPORT=stdio
export ZURVAN_MCP_ALLOW_RAW_READ=0

echo "[1/24] Installing/checking dependencies..."
python -m pip install -r requirements.txt >/dev/null
if [ -f requirements-dev.txt ]; then
  python -m pip install -r requirements-dev.txt >/dev/null
fi

echo "[2/24] Running public repo guard..."
python scripts/public_repo_guard.py

echo "[3/24] Running unit tests..."
pytest tests/

echo "[4/24] Version + doctor..."
python scripts/cli.py version
python scripts/cli.py doctor

echo "[5/24] Creating E2E raw fixture..."
mkdir -p raw/notes
cat > raw/notes/e2e_phase17_source.txt <<'EOF'
Zurvan is a local-first AI knowledge engine.

Vector search was delayed until extraction reliability was proven across TXT, Markdown, short PDF, long PDF, and ugly scanned PDF.

The raw folder is immutable. Generated knowledge must never be written into raw.

Zurvan uses a CLI memory layer so coding agents can store decisions, claims, notes, and questions without calling an internal LLM.

MCP must remain read-only by default.

Public repositories must not commit raw files, SQLite databases, snapshots, reports, publications, or local registry files.
EOF

echo "[6/24] Ingestion..."
python scripts/ingest.py raw/notes/e2e_phase17_source.txt
SOURCE_FILE="$(find wiki/sources -iname '*e2e_phase17*' | head -n 1)"
if [ -z "${SOURCE_FILE}" ]; then
  echo "Could not find generated E2E source file."
  exit 1
fi
echo "Generated source: $SOURCE_FILE"

echo "[7/24] Extraction..."
python scripts/extract.py --source "$SOURCE_FILE"

echo "[8/24] CLI memory + evidence-backed claim..."
python scripts/cli.py remember \
  --type note \
  --title "E2E Phase 17 memory note" \
  --body "Zurvan can store local agent memory through the CLI without internal LLM calls." \
  --tags e2e phase17 cli memory

python scripts/cli.py decision add \
  --title "E2E Phase 17 verification decision" \
  --reason "Zurvan must pass ingestion, extraction, search, graph, MCP, evidence, report, review, and publication checks before Phase 18." \
  --status accepted \
  --tags e2e phase17 verification

python scripts/cli.py claim add \
  --text "Zurvan keeps MCP read-only by default." \
  --source raw/notes/e2e_phase17_source.txt \
  --evidence "MCP must remain read-only by default." \
  --confidence high \
  --tags e2e mcp security

echo "[9/24] Fake evidence rejection..."
set +e
python scripts/cli.py claim add \
  --text "This fake claim should fail." \
  --source raw/notes/e2e_phase17_source.txt \
  --evidence "This quote does not exist in the source file." \
  --confidence high \
  --tags e2e failure
FAKE_EXIT=$?
set -e
if [ "$FAKE_EXIT" -eq 0 ]; then
  echo "Fake evidence claim unexpectedly passed."
  exit 1
fi

echo "[10/24] Search index + hybrid search..."
python scripts/rebuild_search_index.py
python scripts/cli.py search "raw folder immutable"
python scripts/cli.py search "MCP read-only default" --hybrid

echo "[11/24] Retrieval evaluation..."
python scripts/cli.py eval validate-gold
python scripts/cli.py eval search --hybrid --min-top3 0.6

echo "[12/24] Knowledge graph..."
python scripts/graph_build.py
python scripts/cli.py graph stats
python scripts/cli.py graph export --format markdown
python scripts/cli.py graph export --format dot

echo "[13/24] Graph-assisted context..."
python scripts/cli.py context --topic "MCP read-only default" --hybrid --graph --limit 10

echo "[14/24] Agent workflow..."
python scripts/cli.py session start --topic "Phase 17 E2E smoke"
python scripts/cli.py agent preflight --topic "Zurvan verification" --hybrid --graph --limit 10
python scripts/cli.py agent postedit \
  --summary "Ran full Phase 1-17 E2E verification." \
  --files scripts/cli.py scripts/check.sh \
  --checks "pytest tests/ && bash scripts/check.sh"
python scripts/cli.py session close \
  --topic "Phase 17 E2E smoke" \
  --summary "Full E2E verification reached agent workflow stage." \
  --checks "pytest tests/ && bash scripts/check.sh"

echo "[15/24] Snapshot/version/doctor..."
python scripts/cli.py snapshot create
python scripts/cli.py snapshot list
python scripts/cli.py doctor

echo "[16/24] Workspace registry using temp ZURVAN_CONFIG_DIR..."
python scripts/cli.py project register --name zurvan --path .
python scripts/cli.py project list
python scripts/cli.py project current
python scripts/cli.py --project zurvan doctor
python scripts/cli.py --project zurvan search "MCP security" --hybrid

echo "[17/24] Federation..."
python scripts/cli.py project federation stats
python scripts/cli.py project federation doctor
python scripts/cli.py project search-all "MCP security" --hybrid
python scripts/cli.py project context-all --topic "agent memory" --hybrid --graph --limit 10

echo "[18/24] Decision memory..."
python scripts/cli.py project decisions-all
python scripts/cli.py project decisions-similar "MCP read only" --limit 10
python scripts/cli.py project decisions-conflicts || true
python scripts/cli.py project decisions-stale --days 1 || true
python scripts/cli.py project decision-memory rebuild

echo "[19/24] Policy radar..."
python scripts/cli.py project radar scan
python scripts/cli.py project radar policies
python scripts/cli.py project radar contradictions || true
python scripts/cli.py project radar drift || true
python scripts/cli.py project radar report --format markdown

echo "[20/24] Evidence pack build..."
python scripts/cli.py evidence build \
  --topic "MCP security" \
  --hybrid \
  --graph \
  --include-decisions \
  --include-policy-radar \
  --limit 20

PACK_ID="$(find "$ZURVAN_CONFIG_DIR/evidence-packs" -mindepth 1 -maxdepth 1 -type d | sed 's#.*/##' | sort | tail -n 1)"
if [ -z "${PACK_ID}" ]; then
  echo "Could not find generated evidence pack."
  exit 1
fi
echo "Evidence pack: $PACK_ID"

python scripts/cli.py evidence list
python scripts/cli.py evidence inspect "$PACK_ID"
python scripts/cli.py evidence export "$PACK_ID" --format markdown

echo "[21/24] Report compose..."
REPORT_TEMPLATE="${ZURVAN_REPORT_TEMPLATE:-executive_summary}"

python scripts/cli.py report compose \
  --pack "$PACK_ID" \
  --template "$REPORT_TEMPLATE" || {
    echo "Report compose failed with template '$REPORT_TEMPLATE'."
    echo "Set ZURVAN_REPORT_TEMPLATE to a valid template name and rerun."
    exit 1
  }

REPORT_ID="$(find "$ZURVAN_CONFIG_DIR/reports" -mindepth 1 -maxdepth 1 -type d | sed 's#.*/##' | sort | tail -n 1)"
if [ -z "${REPORT_ID}" ]; then
  echo "Could not find generated report."
  exit 1
fi
echo "Report: $REPORT_ID"

python scripts/cli.py report list
python scripts/cli.py report inspect "$REPORT_ID"
python scripts/cli.py report validate "$REPORT_ID"
python scripts/cli.py report export "$REPORT_ID" --format markdown

echo "[22/24] Review workbench audit/index..."
python scripts/cli.py review audit
python scripts/cli.py review audit "$REPORT_ID"
python scripts/cli.py review index rebuild
python scripts/cli.py review checklist "$REPORT_ID"

echo "[23/24] Publication export and bundle..."
python scripts/cli.py publish validate "$REPORT_ID"
python scripts/cli.py publish citations "$REPORT_ID"
python scripts/cli.py publish export "$REPORT_ID" --format markdown
python scripts/cli.py publish export "$REPORT_ID" --format json
python scripts/cli.py publish export "$REPORT_ID" --format html
python scripts/cli.py publish bundle "$REPORT_ID"

echo "[24/24] MCP smoke + full check gate..."
if [ -f scripts/e2e_mcp_smoke.py ]; then
  python scripts/e2e_mcp_smoke.py
fi

bash scripts/check.sh

echo "========================================="
echo "🎉 Zurvan Phase 1-17 Full E2E Verification PASSED"
echo "Temp repo: $TMP_REPO"
echo "Temp config: $ZURVAN_CONFIG_DIR"
echo "========================================="
