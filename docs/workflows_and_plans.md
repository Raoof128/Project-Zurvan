# Zurvan MVP Workflows and Plans

## 4. Retrieval Evaluation Workflow (Phase 4.5 & 4.6)

Before relying on Zurvan's memory retrieval in automated contexts, we evaluate its accuracy against a known gold dataset.

1. **Gold Dataset**: `eval/search_gold.jsonl` contains questions and expected paths.
2. **Validation**: We run `zurvan eval validate-gold` to ensure all target files actually exist. Graph retrieval CANNOT begin until validation passes and baseline retrieval is > 0.
3. **Execution**: 
   ```bash
   zurvan eval search --hybrid --min-top3 0.6
   ```
4. **Metrics Computed**: Top-1 Accuracy, Top-3 Accuracy, and Mean Reciprocal Rank (MRR).
5. **Quality Gates**: Failing the minimum threshold yields an exit code of 1, effectively stopping CI or tests if search reliability degrades. This is a critical prerequisite before moving onto Graph Retrieval.

## Phase 9: Multi-Project Workspace Support
Allows multiple Zurvan projects to be managed independently on the same machine without leaking private absolute paths into public Git repositories.
**Key Scripts**:
- `scripts/config.py`: Local `~/.zurvan` path resolution.
- `scripts/project_registry.py`: Manage projects in `~/.zurvan/projects.json`.
- `scripts/workspace.py`: Safety and sanity checks for project targets.

### Phase 10: Cross-Project Search + Federation ✅
- Implemented `zurvan project search-all` and `context-all`.
- Implemented safe read-only subprocess federation.
- Completed cross-project context merging.

### Phase 11: Cross-Project Decision Memory ✅
- Added `scripts/decision_memory.py`, `scripts/decision_compare.py`, `scripts/decision_federation.py`.
- Added commands: `decisions-all`, `decisions-similar`, `decisions-conflicts`, `decisions-stale`.
- Integrated decision memory cache with cross-project federation.

### Phase 12: Cross-Project Contradiction + Policy Radar ✅
- Phase 10: Cross-Project Search + Federation [COMPLETED]
- Phase 11: Cross-Project Decision Memory [COMPLETED]
- Phase 12: Cross-Project Contradiction + Policy Radar [COMPLETED]
- Phase 13: Evidence Pack Builder [COMPLETED]
- Phase 14: Report Composer [COMPLETED]
- Phase 15: Local Report UI / Review Workbench [COMPLETED]
- Phase 16: Review Workbench Hardening + UX Polish [COMPLETED]
- Phase 17: Export & Publication Pack [COMPLETED]
- Detect "Project A says X, Project B says not-X" assumptions.

**Key Scripts**:
- `scripts/federation.py`: Validates and filters healthy projects from the registry.
- `scripts/cross_project_search.py`: Searches across registered projects securely.
- `scripts/cross_project_context.py`: Builds a consolidated context bundle across multiple projects.

**Federation Safety Model**:
- **Read-Only**: Federation commands never mutate project data.
- **Strict Privacy**: Files are never copied between projects, and absolute paths are hidden unless `--verbose` is provided.
- **Isolation**: Search and context expansion use subprocesses executed in the target project's `cwd` to prevent path bleed.
- **No Cloud**: All federation logic operates exclusively over local SQLite databases.
- **Known Limitations**: Graph expansion (`cross_project_context`) groups items by project but does not attempt to merge cross-project graph edges.

## 5. Knowledge Graph Lite (Phase 5)

Zurvan builds a local SQLite-backed knowledge graph directly from Markdown files.

1. **Trigger**: User runs `python scripts/cli.py graph rebuild`.
2. **Node Rules**: Every Markdown file in `wiki/`, `docs/`, and root level docs (`README.md`, `AGENTS.md`) is a node. `raw/` and `data/` are strictly excluded.
3. **Edge Rules**:
   - Obsidian wikilinks: `[[example]]`
   - Explicit Markdown links.
   - YAML fields: `source`, `source_path`, `source_id`, `claim_id`, `related`, etc.
   - Evidence lines like `Source: [file.md]`.
4. **Safety Rules**: No LLM calls. No cloud calls. Strictly parses existing Markdown and frontmatter.
5. **Phase 5.5 Graph-Assisted Context Expansion**: 
   - When running `zurvan context --topic <query> --hybrid --graph`, Zurvan performs a hybrid search first.
   - It takes the resulting chunks, traces their source files, and uses those files as seeds for a graph traversal.
   - It expands outwards up to a specified `--depth` (default 1).
   - Expanded nodes are deduplicated, grouped by type (Decisions, Claims, Concepts, Open Questions, etc.), and appended to the context bundle.
   - This prevents LLMs from missing critical related context that doesn't share the exact keywords but is explicitly linked.

## Initial Ingestion Workflow
1. **Trigger**: User runs `python scripts/ingest.py <path_to_raw_file>`.
2. **Validation**: The script checks if the file exists and is located in the `raw/` directory.
3. **Hashing**: A SHA-256 hash of the file is calculated.
4. **Registry Check**: The script queries `data/registry.sqlite` to ensure this file (or exact content hash) hasn't already been ingested.
5. **Text Extraction**: Text is parsed based on file type (e.g., `.txt`, `.md`, `.pdf`).
6. **Wiki Generation**:
   - A new source page is created in `wiki/sources/`.
   - The text is passed to an LLM (stubbed in MVP) to extract entities, concepts, claims, and open questions.
   - Separate markdown files for extracted concepts/claims are created in their respective folders (`wiki/concepts/`, `wiki/claims/`).
7. **Index & Log**: `wiki/index.md` is updated with the new source, and `wiki/log.md` receives a timestamped entry.

## Initial Audit Workflow
1. **Trigger**: User runs `python scripts/audit_wiki.py`.
2. **Traversal**: The script recursively walks the `wiki/` directory reading all `.md` files.
3. **Validation Checks**:
   - **Missing Frontmatter**: Checks if the file starts with `---` (YAML frontmatter).
   - **Duplicate Titles**: Collects all `title` tags from frontmatter and flags duplicates.
   - **Orphan Pages**: Builds a lightweight graph of internal links and flags pages that have no incoming links (excluding root indices).
   - **Uncited Claims**: Checks files in `wiki/claims/` for citation links or an explicit "evidence is missing" statement.
4. **Reporting**: Outputs a console summary of all failed checks and their file paths.

## Python Script Plan
- **`scripts/db.py`**: Handles SQLite connections and schema initialization for `registry.sqlite`.
- **`scripts/ingest.py`**: The main entry point for adding documents. It coordinates hashing, text extraction, DB registration, and file creation. Currently relies on basic text extraction but built to easily wrap an LLM API call for entity/claim extraction.
- **`scripts/query.py`**: Implements a keyword search through the wiki files. Stubs are in place for `vector_search` and `graph_search` to be added later.
- **`scripts/audit_wiki.py`**: The integrity checker. Implements heuristics to ensure the wiki stays clean and linked.
- **`scripts/rebuild_index.py`**: Utility to re-scrape the `wiki/` directory and reconstruct `wiki/index.md` cleanly if things get out of sync.
- **`scripts/llm.py`**: Adapter to standardize LLM API interactions.
- **`scripts/schemas.py`**: Defines Pydantic schemas for the expected LLM JSON output.
- **`scripts/extract.py`**: Automates LLM extraction via prompt `extract_source.md` and generates structured Markdown for claims/entities.
- **`scripts/validate_extraction.py`**: Enforces strict JSON output schema, valid markdown filenames, and exact-match citation verification.

## Phase 2 LLM Extraction Workflow
1. **Trigger**: User runs `python scripts/extract.py --source wiki/sources/example.md`
2. **LLM Query**: Loads `scripts/prompts/extract_source.md`, injects source text, and calls the LLM with `temperature=0`.
3. **JSON Parse & Validation**: Validates the output against schema requirements and explicitly asserts that every extracted evidence quote exists verbatim in the source text.
4. **Data Persistence**: Saves raw validated JSON to `data/extractions/<id>.json`.
5. **Markdown Generation**: Scatters the structured data into specific semantic files (e.g., `wiki/claims/<claim_id>.md`, `wiki/entities/`, etc.) with proper `type`, `confidence`, and link metadata for Obsidian compatibility.

## Phase 3 PDF Stress Test Workflow
1. **Ingest**: `python scripts/ingest.py raw/papers/example.pdf` (extracts text via `pypdf`).
2. **Extract**: `python scripts/extract.py --source wiki/sources/example.pdf.md` (uses configured LLM provider via `ZURVAN_LLM_PROVIDER`).
3. **Audit**: `python scripts/audit_wiki.py` to ensure all generated markdown components are correctly formed and citations are valid.

## Phase 3.5 Reliability Gauntlet
1. **Trigger**: User runs `python scripts/run_reliability_gauntlet.py raw/path1 raw/path2 ...`
2. **Batch Processing**: Safely ingests, extracts, and audits each provided source sequentially.
3. **Resilience**: A failure in one file's ingestion/extraction does not crash the rest of the batch.
4. **Documentation**: Test coverage and success across formats (TXT, MD, PDF, OCR PDF) is tracked manually in `docs/extraction_test_matrix.md`.

## Phase 3.6 Agent CLI Interface
1. **Trigger**: Agent runs `python scripts/cli.py <command>`
2. **Commands**: `remember`, `decision add`, `claim add`, `question add`, `search`, `context`, `audit`, `index rebuild`.
3. **Safety**: `safe_write.py` intercepts path traversal attempts and refuses to write to `raw/`.
4. **Verification**: `claim add` will read the source file to verify the `evidence` string exists before creating the claim.
5. **Context**: `context` creates an LLM-friendly Markdown bundle of matched documents.

## Phase 6 Local MCP Server Workflow
1. **Trigger**: An MCP-compatible client (e.g. Claude Code) spawns `python scripts/mcp_server.py` over `stdio`.
2. **Read-only Default**: By default, `ZURVAN_MCP_READONLY=1` is enforced. Write tools will return descriptive error messages if invoked.
3. **Write Mode**: Setting `ZURVAN_MCP_READONLY=0` allows agents to use write tools (like `zurvan_remember` or `zurvan_decision_add`). This should only be enabled in trusted local repositories.
4. **Safety & Threat Model**:
   - The server performs strict path validation: absolute paths and `../` traversal are always blocked.
   - The `raw/` directory cannot be read through MCP unless `ZURVAN_MCP_ALLOW_RAW_READ=1` is explicitly set. Writing to `raw/` is never allowed.
   - The server is purely a thin wrapper around existing CLI and python scripts. It does not introduce arbitrary execution boundaries or new shell subprocess inputs.
   - `stdio` transport prevents external network requests from directly addressing the server. Future upgrades may introduce HTTP transport only after proper authentication layers are added.

## Phase 6.5 MCP Client Integration Pack
1. **Trigger**: User runs `python scripts/doctor_mcp.py` to assert system health before connection.
2. **Config Generation**: User runs `python scripts/install_mcp_config.py` to safely generate configurations for `claude-code`, `cursor`, etc., with explicit warnings for write-mode.
3. **Documentation**: Clear integration guides are provided in `docs/mcp/` to safely onboard various agents without violating Zurvan's local-first rules.

## Phase 7 Agent Workflow Orchestration
1. **Trigger**: An agent (or human) starts a task using `session start`.
2. **Context Gathering**: The agent runs `agent preflight` to get a dense, graph-expanded context bundle specifically formatted for LLM context windows, including recent logs and open questions.
3. **Execution**: The agent performs the coding task.
4. **Recording**: The agent runs `agent postedit` to write a structured memory of files changed and checks run to `wiki/log.md`.
5. **Closure**: The agent runs `session close` to finalize the Markdown session file in `wiki/sessions/`.

## Phase 7.5 Obsidian Integration Pack
1. **Trigger**: User opens the Zurvan root repository as an Obsidian vault.
2. **Configuration**: The predefined `.obsidian/` configuration automatically filters out noisy directories (like `data/` and `scripts/`).
3. **Template Usage**: User can utilize provided YAML-compatible templates in `wiki/templates/` for creating uniform Claims, Decisions, Concepts, etc.
4. **Execution**: The local graph perfectly mirrors the Obsidian knowledge graph without complex plugin dependencies.
## Phase 8 Release Packaging + Versioned Snapshots
1. **Trigger**: User runs `zurvan snapshot create`.
2. **Execution**: A snapshot `tar.gz` is securely generated in `dist/snapshots/`, automatically filtering out the `raw/` directory to prevent data leakage, unless `--include-raw` is passed.
3. **Restore Mechanism**: When `zurvan snapshot restore <name> --force` is run, Zurvan takes an automatic safety backup of the current state, explicitly blocks any paths targeting `raw/`, and extracts the snapshot.
4. **Health Checking**: Users can run `zurvan doctor` and `zurvan version` at any time to verify their local installation integrity and feature flags.
