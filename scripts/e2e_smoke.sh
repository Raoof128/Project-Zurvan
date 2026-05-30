#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH=.
export ZURVAN_LLM_PROVIDER="${ZURVAN_LLM_PROVIDER:-mock}"
export ZURVAN_LLM_MODEL="${ZURVAN_LLM_MODEL:-mock}"
export ZURVAN_EMBED_PROVIDER="${ZURVAN_EMBED_PROVIDER:-mock}"

echo "[1/12] Installing/checking dependencies..."
pip install -r requirements.txt
pip install -r requirements-dev.txt

echo "[2/12] Running unit tests..."
pytest tests/

echo "[3/12] Creating E2E raw source fixture..."
mkdir -p raw/notes
cat > raw/notes/e2e_smoke_note.txt <<'EOF'
Zurvan is a local-first AI knowledge engine.

Vector search was delayed until extraction reliability was proven across TXT, Markdown, short PDF, long PDF, and ugly scanned PDF.

The raw folder is immutable. Generated knowledge must never be written into raw.

Zurvan uses a CLI memory layer so coding agents can store decisions, claims, notes, and questions without calling an internal LLM.
EOF

echo "[4/12] Ingesting E2E source..."
python scripts/ingest.py raw/notes/e2e_smoke_note.txt

SOURCE_FILE="$(find wiki/sources -iname '*e2e*' | head -n 1)"
if [ -z "$SOURCE_FILE" ]; then
  echo "Could not find generated E2E source file in wiki/sources/"
  exit 1
fi

echo "Using generated source: $SOURCE_FILE"

echo "[5/12] Running extraction..."
python scripts/extract.py --source "$SOURCE_FILE"

echo "[6/12] Testing CLI memory commands..."
python scripts/cli.py remember \
  --type note \
  --title "E2E smoke memory note" \
  --body "Zurvan can store agent memory through the local CLI without calling an internal LLM." \
  --tags e2e smoke cli memory

python scripts/cli.py decision add \
  --title "E2E smoke decision" \
  --reason "Zurvan must pass ingestion, extraction, search, evaluation, and graph checks before adding more features." \
  --status accepted \
  --tags e2e smoke quality

echo "[7/12] Testing evidence-backed claim success..."
python scripts/cli.py claim add \
  --text "Zurvan delays vector search until extraction reliability is proven." \
  --source raw/notes/e2e_smoke_note.txt \
  --evidence "Vector search was delayed until extraction reliability was proven across TXT, Markdown, short PDF, long PDF, and ugly scanned PDF." \
  --confidence high \
  --tags e2e smoke retrieval

echo "[8/12] Testing evidence-backed claim failure..."
set +e
python scripts/cli.py claim add \
  --text "This claim should fail because evidence is fake." \
  --source raw/notes/e2e_smoke_note.txt \
  --evidence "This fake quote does not exist in the file." \
  --confidence high \
  --tags e2e smoke failure
FAKE_CLAIM_EXIT=$?
set -e

if [ "$FAKE_CLAIM_EXIT" -eq 0 ]; then
  echo "Fake evidence claim unexpectedly passed"
  exit 1
fi

echo "[9/12] Rebuilding search index..."
python scripts/rebuild_search_index.py

echo "[10/12] Testing search and context..."
python scripts/cli.py search "raw folder immutable"
python scripts/cli.py search "why was vector search delayed" --hybrid
python scripts/cli.py context --topic "vector search reliability" --hybrid --limit 10

echo "[11/12] Validating retrieval evaluation..."
python scripts/cli.py eval validate-gold
python scripts/cli.py eval search --hybrid --min-top3 0.6

echo "[12/12] Building and testing graph..."
python scripts/graph_build.py
python scripts/cli.py graph stats
python scripts/cli.py graph export --format markdown
python scripts/cli.py graph export --format dot

echo "[Final] Running full check.sh..."
bash scripts/check.sh

echo "========================================="
echo "🎉 Full Zurvan E2E smoke test passed."
echo "========================================="
