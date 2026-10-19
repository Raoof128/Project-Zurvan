# Zurvan MCP server in Rust — design

**Date:** 2026-10-19 (Australia/Sydney)
**Status:** APPROVED rev 3 (Raouf, 2026-10-19 11:07: "Design rev 3
approved … Answer open questions with your recommendations"). Gauntleted
by builder check + simurgh-arise pass. Open questions answered in §12.
Plan: `docs/superpowers/plans/2026-10-19-rust-mcp-server.md`.
**Implemented** on branch `rust-mcp` (plan Tasks 0–14, 2026-10-19); live
gate green on both protocols (§10.4). Cut-over of the harness configs is
**not done**: it waits for the whole-branch review and the merge.
**Scope:** replace `scripts/mcp_server.py` (+ `mcp_tools.py`, `mcp_resources.py`,
`mcp_prompts.py`, `mcp_security.py`) with a native binary that speaks the same
MCP surface, is an order of magnitude faster on every call, and is smoother
for the agent on the other end.
**Out of scope:** the CLI, ingest, extract, rebuilds, Boundary, the ledger,
eval sealing. Those stay Python. This is a *server* rewrite, not an engine
rewrite.

---

## 0. Why — the measured baseline

Measured 2026-10-19 10:31 on the live store (303,080 chunks, 384-dim
`all-MiniLM-L6-v2`, 11,884-node graph, 11,911 Markdown files / 235 MB), warm
disk, `/opt/anaconda3/bin/python`:

Two measurements: in-process (first table, 10:31) and over a real stdio MCP
client (second, 10:51 — the numbers an agent actually sees).

| Tool | In-process | Over MCP stdio | Where the time goes |
|---|---|---|---|
| launch → `initialize` answered | — | **0.66 s** | the SDK import only; the model is NOT loaded at start (corrected from a ~20 s guess in rev 1) |
| `tools/list` | — | 3 ms | fine |
| `zurvan_search` hybrid, first call | 29.4 s | **26.8 s** | torch import + model load + first full vector scan |
| `zurvan_search` hybrid, warm | 6.8 s | **6.8 s** | sqlite-vec reads all 303k float32 BLOBs (~465 MB) out of a 1.2 GB SQLite file on every query, then a Python heap loop over 303k rows |
| `zurvan_search` keyword (`hybrid=false`) | 4.6 s | — | not FTS at all: `glob` + open + read every `.md` under `wiki/` and `docs/` on every call (0.43 s of that is the glob itself) |
| `zurvan_status` | 11.7 s | **11.9 s** | `SELECT MAX(indexed_at)` is a full scan of the 1.2 GB index (**5.5 s**, no index on that column); `scan_markdown_files` walks 11.9k files in Python; `count(*)` 0.6 s |
| `zurvan_graph_stats` | — | **0.8 s** | two `count(*)` on a 14 MB db per call |
| `zurvan_context` | 7.0 s | — | hybrid search + graph expansion + bundle assembly |
| `zurvan_graph_neighbours` | 0.03 s | — | fine |

An agent that follows the skill ("call `zurvan_status` first, then search,
then read a page") waits **~40 s** before it has read anything. Every hybrid
query afterwards costs 7 s. That is the problem.

Two more facts from the same capture that shape the design:

- The server negotiated **protocol 2025-11-25** with the SDK v2 client and
  put `ttlMs: 0` on `tools/list` — the 5-minute cache hint in the source is
  not what reaches the wire today. Caching list results is therefore a new
  gain, not a preserved one.
- The 14 tool schemas cost **~4.2k tokens** per session (16,991 chars). That
  is paid once per session by every client that does not defer tools.

None of the slow paths is algorithmically necessary. The vector scan is 116 M
multiply-adds — single-digit milliseconds in SIMD over a contiguous matrix. The
staleness check is 12k `stat` calls — tens of milliseconds in a compiled
walker. The keyword scan is 235 MB of `memchr` — well under a second with
threads.

## 0.1 The gap hunt — where this sits in the field (2026-10-19)

Swept four fronts before committing to the shape below. Figures from
secondary sources are marked *reported* until pinned to a primary.

- **Standard.** MCP 2026-07-28 is final: the `initialize`/`initialized`
  handshake is removed, protocol version and capabilities ride in `_meta` on
  every request, `server/discover` replaces up-front negotiation, Tasks moved
  to a capability-negotiated extension, list and resource-read results carry
  `ttlMs` + `cacheScope`, Logging is deprecated in favour of stderr for stdio
  ([MCP blog, primary](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)).
  Anthropic says support "is rolling out across Claude products" without
  naming Claude Code or a date ([claude.com, primary](https://claude.com/blog/bringing-mcp-2026-07-28-to-claude)).
  Consequence: the server must speak **both** the legacy handshake and the
  stateless form; `rmcp` 3.5.1 does (verified in its source:
  `fill_missing_cache_hints` for 2026-07-28 peers, `strip_result_type_for_legacy_peer`
  otherwise, `discover` handler present).
- **Incumbents, in their own numbers.** Exomem (Python, FTS5 + sqlite-vec,
  the closest architectural twin) *reports* 864 ms for hybrid find over
  50,000 notes on its reference desktop. QMD (the pattern Zurvan 2's spec
  cites for warm local serving) is *reported* at 2–3 s warm / ~19 s cold with
  LLM query expansion and reranking, and its Rust port states "no usable
  functionality yet". Memory Wiki (Rust, fastembed) stores JSON, not a
  Markdown vault. None is a drop-in for a 303k-chunk, 11.9k-file store with
  a revision ledger behind it. A sub-200 ms exhaustive hybrid scan over 303k
  chunks on an 8 GB laptop would be a publishable number (G1) — with the
  disclaimer that it buys no reranking and no query expansion, which is the
  policy Zurvan chose, not a shortcut.
- **The seam nobody fills.** Freshness in the field is handled by per-memory
  labels (fresh/aging/stale), temporal validity windows, or recall-time
  thresholds. **No matching prior pattern in our current source map stamps
  each retrieval result with the knowledge revision it answered from and the
  ledger revision at call time.** Zurvan already mints both numbers; the
  server only has to say them. That is the one new species this project
  adds (§7.8), and the sentence is scoped so a reviewer can falsify it.
- **The wound in our own house.** The adversarial-user pass (G11) against
  the live Python server read `.git/config`, `.claude/settings.json`,
  `.remember/now.md` and an ingested legal document under `sources/` through
  `zurvan_read_page`. The gate blocks `raw/` and traversal and nothing else.
  Boundary guards *commits*; nothing guards *MCP reads*, and the server runs
  in every project's session with `ZURVAN_MCP_READONLY=0`. §5 closes it in
  Rust; the Python server gets the same fix as a separate, immediate change.
- **Security climate.** Thirty-plus CVEs against MCP implementations in
  early 2026 (*reported*), tool-result and tool-description injection as the
  dominant class. This server exposes no network listener and treats every
  page it returns as data; the allow-list above is the concrete change.

## 1. Intent

A `zurvan-mcp` binary that an MCP client launches over stdio and that, from
the agent's seat, behaves exactly like today's server except that every
answer arrives fast enough that the agent never has to ration calls — and
that tells the agent, on every answer, which revision of the knowledge it
is looking at.

**Targets** (warm, same machine, same store):

| Tool | Target | Depends on |
|---|---|---|
| `initialize` / `discover` answered | < 50 ms | nothing loaded yet |
| first hybrid search after launch | < 2 s | model load (~0.5 s) + mmap of an existing vector cache; **~6 s** when the cache must be built for a new index generation |
| hybrid search, warm | < 150 ms | embed ~30 ms, scan ~15 ms resident (~300 ms if the 465 MB map was evicted and re-read from SSD), hydrate ~10 ms |
| keyword search | < 400 ms | parallel live-file scan, byte-for-byte same hit set and order as today |
| status | < 100 ms | `MAX(indexed_at)`, chunk/file counts, graph counts computed **once per index generation** (keyed on inode) and the mtime walk done in parallel |
| context bundle | < 400 ms | search + graph + assembly |
| graph tools | < 20 ms | counts cached per generation |
| read_page | < 5 ms | |
| write tools | 2–4 s | one Python subprocess (0.13 s import + ledger work) |

Targets are design goals, not gate numbers; §9 says what is gated.

## 2. Goals, non-goals, anti-goals

**Goals**

1. **Drop-in.** Same server name, same 14 tool names, same argument names,
   types, defaults, ranges and descriptions; same 7 resources (including the
   `zurvan://file/{+path}` template); same 4 prompts; same annotations
   (`readOnlyHint` on the 9 read tools, `destructiveHint: false` on the 5
   write tools). The existing `~/.claude.json` / Codex entries change only
   `command`/`args`.
2. **Identical answers.** For every read tool, the Rust server returns the
   same ranked results, the same paging headers, the same error strings, the
   same character budget behaviour (`[truncated: N more characters…]`) as
   the Python server on the same store. Parity is a test, not a hope (§9).
3. **Fast and warm.** Numbers above. The server loads once and stays
   resident; the client keeps it alive across the session.
4. **Smoother for the agent.** The things that make an agent fumble today are
   fixed without changing semantics (§7).
5. **Every invariant kept.** The ledger, the read-only gate, path safety,
   raw/ blocking, `print()`-free stdout, published-store immutability, no
   remote exposure (§5).

**Non-goals**

- No new retrieval policy. Full scan, never KNN; same fusion weights
  (`KEYWORD_WEIGHT = 0.2`, `MIN_HYBRID_SCORE = 0.2`); same canonical tie order
  (`score DESC, chunk_id ASC`). A ranking change needs its own eval re-run
  and is a different project.
- No Rust reimplementation of the knowledge ledger (`knowledge_revision.py`,
  1,296 lines of two-phase, lease, reconcile and attribution logic). Writes
  go *through* it, not around it (§6).
- No HTTP/SSE transport, no daemon, no auth layer. Z11 of the canonical spec
  (stateless MCP + local daemon + capability classes) is a later phase; this
  server is stdio-only and must not foreclose Z11.
- No change to the CLI's search output (`zurvan search --json` stays Python).
- R3 (tool-call provenance events) is not built by this work and must not be
  claimed.

**Anti-goals** (what would make it a failure even if it "works")

- A server that is fast because it answers from a different candidate set,
  a different embedding space, or a stale cache.
- A write path that mints half-operations in the ledger (the 2026-09-19 and
  2026-09-28 defects, both caused by bypassing the operation boundary).
- A server whose stdout can carry anything but JSON-RPC.
- A binary that, on an 8 GB M2, pushes the machine into swap to hold vectors.

## 3. Constraints inherited from the repo

From `CLAUDE.md` / `AGENTS.md`, restated because each one shapes a decision
below:

- **Published stores are never written in place.** `data/search.sqlite` and
  `data/graph.sqlite` are 0444, promoted atomically by `os.replace`. The
  server opens both `mode=ro` and never creates a file at either path.
- **The index is the source of truth for query embeddings.** Provider/model
  come from the `embeddings` table, never from env.
- **A production index holds exactly one embedding space:**
  `sentence_transformers / all-MiniLM-L6-v2 / 384`. The server refuses to
  serve semantic search if the index says anything else.
- **A write with no operation above it is refused.** One MCP tool call = one
  ledger operation = one revision.
- **Read-only gate fails closed:** writes only when `ZURVAN_MCP_READONLY`
  is exactly `"0"`.
- **`raw/` is blocked** unless `ZURVAN_MCP_ALLOW_RAW_READ=1`, case-insensitive
  on the first path component (macOS).
- **Never quote the retrieval instrument.** `zurvan_eval_search` output names
  items by index only; the server runs the eval through the existing Python
  entry point and must not add any raw-query text to the result.
- **Retrieval ranking/indexing changes require an eval re-run documented in
  CHANGELOG.md.** This work intends *no* ranking change; the re-run is the
  proof of that (§9.3).
- **Never run `check.sh` during an index rebuild.** The server is a reader,
  but its vector cache build (§4.3) reads the index; it must detect an
  in-flight promotion and retry, never hold a lock.

## 4. Architecture

```
MCP client (Claude Code / Codex / Cursor)
        │ stdio JSON-RPC (MCP 2026-07-28)
        ▼
zurvan-mcp (Rust, single binary, tokio + rmcp 3.5)
 ├── protocol/      initialize, tools/resources/prompts list+call, cache hints
 ├── security/      is_safe_path, read-only gate, raw/ gate, stdout guard
 ├── store/         ro SQLite (rusqlite, bundled sqlite + FTS5), hot-reload watcher
 ├── search/        FTS5 BM25 → normalised keyword score
 │                  query embed (candle BERT on the local all-MiniLM-L6-v2 safetensors)
 │                  full-scan cosine over an mmap'd f32 matrix → bounded heap
 │                  fuse → canonical order → hydrate
 │                  keyword mode: parallel live-file scan (rayon + memchr)
 ├── graph/         neighbours, expand (BFS, nearest-first, type_rank), stats
 ├── context/       export_context bundle (Markdown), budget + paging
 ├── pages/         read_page slices, resources, prompts
 ├── status/        staleness (parallel mtime walk vs MAX(indexed_at)), counts
 ├── eval/          zurvan_eval_search, zurvan_validate_gold → same Python
 │                  bridge as writes; they evaluate the PYTHON reference
 │                  retriever and say so in their result header (§4.8)
 └── writes/        bridge → `python -m scripts.mcp_bridge` (one op each)
```

### 4.1 Protocol layer

- `rmcp` 3.5.1 (already in the local cargo cache; `server` + `transport-io`
  features only — no `reqwest`, no `auth`, no `client-side-sse`), stdio
  transport only.
- **Both lifecycles.** Legacy `initialize`/`initialized` for clients on
  2025-xx (the Python SDK v2 client negotiated 2025-11-25 on 2026-10-19),
  and the 2026-07-28 stateless form (`_meta` per request, `server/discover`).
  `rmcp` routes both; the parity harness exercises both by pinning the
  client's protocol version.
- Tools declared with the exact JSON schema the Python SDK emits today
  (captured 2026-10-19 as `fixtures/tools_list.json`, 16,991 chars; the
  parity test diffs the Rust `tools/list` against it, ignoring key order).
  Every string-returning tool keeps `outputSchema {result: string}` and
  `structuredContent {"result": …}`; `zurvan_graph_stats` keeps
  `{nodes, edges}`.
- `ttlMs: 300000`, `cacheScope: private` set explicitly on the four list
  results (`rmcp` `with_cache_scope` / cache-hint builders, verified in
  `model.rs`). Today's wire value is `ttlMs: 0`, so this is a new saving for
  any client that honours it, worth ~4.2k tokens per re-list.
- Logging to stderr only (the 2026-07-28 deprecation of the Logging
  capability names exactly this for stdio).
- Logging goes to stderr only. The binary has no `println!`; a clippy lint
  (`clippy::print_stdout` = deny) enforces it at compile time — the Rust
  equivalent of `_protocol_safe`.

### 4.2 Store access

- `rusqlite` with `bundled` feature (ships SQLite with FTS5 compiled in, so
  the binary does not depend on the system SQLite's FTS5 build).
- Both databases opened with `SQLITE_OPEN_READ_ONLY | SQLITE_OPEN_URI`;
  `?immutable=1` is **not** used because the files can be replaced underneath
  us, and immutable mode would hide that.
- Hot reload: the atomic promote replaces the inode. Connections are opened
  per call (an open fd keeps its inode, so one call never straddles two
  generations), and each call compares `(dev, ino, size, mtime)` of the
  store with the generation its metadata and vector cache were built for.
  On a change, hybrid calls **wait** for the new generation's cache
  (~6 s, progress-notified) instead of serving the old matrix against the
  new `chunks` table, which would mix generations. (Corrected in the plan,
  2026-10-19: rev 3 said the old cache "keeps serving".) `zurvan_status`
  reports the generation it is serving (`built_from_revision` from
  `index_meta` / `graph_meta`).

### 4.3 Vector cache — the one new derived artefact

sqlite-vec's full scan is slow because it reads 465 MB of BLOBs through
SQLite's page layer per query. The fix is a **contiguous float32 matrix**
the OS can keep in page cache:

- Location: `data/cache/vectors-<index_digest>.f32` plus a sidecar
  `.ids` (newline-separated `chunk_id` in row order). `data/` is gitignored
  and `data/cache/` is new; it is never a published store, so writing it is
  legal. `<index_digest>` = `index_meta.built_from_manifest` (falls back to a
  hash of `(dev, ino, size, mtime, built_at)` for an unstamped index).
- Built on first start for a given index generation: one `SELECT chunk_id,
  vector FROM embeddings` streamed to a temp file, then `rename` into place.
  Expected 3–6 s once; subsequent starts `mmap` it in milliseconds.
- Served via `mmap` (`memmap2`), **not** loaded into heap. 465 MB of mapped
  file is evictable page cache, which is what keeps an 8 GB M2 out of swap.
  A full scan touches it sequentially; after the first scan it is resident.
- Scan: `rayon` over row chunks, dot product with SIMD-friendly inner loop
  (the vectors are already L2-normalised by sentence-transformers, but the
  Python path computes cosine properly — the Rust path computes cosine
  properly too, same formula, so a non-normalised vector behaves identically).
- The cache is **validated on open**: row count must equal
  `SELECT count(*) FROM embeddings` and the digest in its header must match
  the open index's; otherwise it is rebuilt. A mismatched cache is never
  served.
- Stale caches from old generations are pruned on start (keep newest 2).

### 4.4 Query embedding

- **`candle` running the same `model.safetensors` the index was built
  with**, read from the local Hugging Face cache
  (`~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/`),
  with the `tokenizers` crate on that model's `tokenizer.json`. The BERT
  forward pass, mean pooling and L2 normalisation are ported from the
  sentence-transformers module config (`max_seq_length` **256**, not the
  BERT default 512 — truncation length changes the vector for long queries).
  CPU only; a 22 M-parameter model embeds one query in ~10–30 ms.
- Why not ONNX/`fastembed`: it pulls `hf-hub` and `ort` into the dependency
  tree, both of which fetch over the network at build or first run. That
  breaks the "no network crate in `cargo tree`" invariant in §5 and the
  single-binary claim (`ort` may need a dylib). `candle` is pure Rust and
  needs nothing downloaded. (Builder check, 2026-10-19.)
- The model path is resolved at start (`ZURVAN_MODEL_DIR` overrides the HF
  cache lookup). If the files are absent the server still starts, says so in
  `zurvan_status` and in every hybrid search result (`Semantic search
  unavailable: …`), and serves keyword results only — it never silently falls
  back to hash vectors (the `mock` trap, measured once at 0 % recall).
- **Parity is a gate, not an assumption.** Same weights, different
  accumulation order: cosine between the Rust and PyTorch vectors for the
  same text is expected > 0.9999. The phase-0 spike measures this and the
  rank agreement defined in §9.1 on the instrument before the Rust path is
  allowed to serve. If the gate fails, the design stops and reports rather
  than shipping a different retriever.

### 4.5 Keyword mode

Today's `hybrid=false` is a live scan of `wiki/**/*.md` + `docs/**/*.md`
(it finds unindexed files — a feature, not a bug). Its exact contract,
verified 2026-10-19 by reproducing `glob`'s order with a hand-written walk
over all 11,912 files (identical, stable across runs, **not** name-sorted):

1. For `wiki` then `docs`: visit directories **pre-order in `scandir`
   order**, skipping dot-directories; in each directory take `*.md` files in
   `scandir` order, skipping dot-files.
2. Skip any path with a `traces` component.
3. Lower-case the whole file once; `score` = number of **distinct** query
   tokens (`query.lower().split()`) that occur as substrings. Files with
   score 0 are dropped.
4. Stable sort by score descending; return the first `limit`.

Because scores are small integers, ties are everywhere and **tie order is
traversal order**. The Rust port uses `std::fs::read_dir` without sorting —
the same `readdir` the Python `scandir` uses on the same filesystem — and the
parity test asserts identical order on the full corpus, not just the hit
set. Work is parallelised per file (`rayon`); ordering is restored by index
after scoring. Switching keyword mode to FTS5 would be a semantics change
and is **not** done here; it is listed in §11 as an option.

### 4.6 Graph

Same three SQL statements, same `type_rank` ordering in `expand_graph_context`,
same BFS. The parity test covers the hub node (3,659 edges) and depth-2
expansion with paging.

**Finding (builder check, 2026-10-19): the Python `expand_graph_context` is
nondeterministic.** It builds each BFS frontier with `list(set_of_str)`, so
the order — and, for a node reached both ways, whether the relation reads
`incoming:` or `outgoing:` — depends on `PYTHONHASHSEED`, which is random per
process. Measured: the hub node at depth 2 moved a line between two seeds;
twelve top-degree seeds at depth 1 differed in 2–13 of 82 lines across six
seeds. The Python server has been returning a different first page for the
same call across restarts. Byte-identical parity against it is impossible by
construction.

Decision (mine): the Rust port **defines the canonical order** the Python
code never had — frontier expansion in `node_id ASC`, relation chosen by the
first edge encountered under that order, then the existing `type_rank`
sort. The parity gate for `graph_expand` and for `context` with `graph=true`
compares **order-normalised sets of lines** (and total counts and paging
arithmetic), exact for everything else. A follow-up fix to the Python side
(sort the frontier the same way) is listed in §11 so the two agree exactly
afterwards; it is a one-line change and its own CHANGELOG entry.

### 4.7 Context bundle

`export_context` (536 lines) is the largest port. It is Markdown assembly
over search results + graph neighbours + page excerpts. Ported with the
Python output captured as golden files for ~10 topics. Bar: byte-identical
for the search-derived sections (agents and tests pattern-match on the
headings); order-normalised for the graph-neighbour section, per §4.6.
Golden files live under `~/.zurvan/parity/`, never in git: they contain
`wiki/` excerpts, and `wiki/**` is untracked by design.

### 4.8 Eval tools

`zurvan_eval_search` and `zurvan_validate_gold` call `run_search_evaluation`
/ `validate_gold_dataset`, which drive Python's `search_hybrid` directly.
They are served through the Python bridge (§6) unchanged, and the result
header gains one line: `Evaluates the Python reference retriever, not this
server.` That is the truth and the agent should see it. Cost is a torch
import plus the queries (~40 s for the smoke's `max_queries=2`, minutes for
the full set); these tools are liveness checks that an agent calls rarely,
so the cost is accepted rather than hidden behind a native port that would
measure a different thing.

The **Rust retriever's** measurement is `scripts/mcp_parity.py` (§9.1): it
drives the Rust server over the instrument by index, computes top-1 / top-3
/ MRR from the Rust results, and prints both servers' numbers side by side.
No query text is logged or written.

## 5. Security and invariants, concretely

| Invariant | Mechanism in Rust |
|---|---|
| read-only gate | `ZURVAN_MCP_READONLY` read at call time; only exact `"0"` enables; same error string |
| path safety | `is_safe_path`: reject absolute; canonicalise under `PROJECT_ROOT` (follows symlinks, so a link out of the tree is rejected); reject escape; reject first component eq-ignore-case `raw` unless `ZURVAN_MCP_ALLOW_RAW_READ=1` |
| **read allow-list (new)** | a readable path must sit under `wiki/` (minus `wiki/traces/`), `docs/`, or `eval/`, or be one of `README.md`, `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md` — i.e. what the search index itself covers plus the four repo-root docs. Everything else (`.git/`, `.claude/`, `.remember/`, `.worktrees/`, `sources/`, `data/`, `scripts/`, `tests/`, `static/`, `templates/`, `dist/`) is refused with the same `failed safety checks` error shape. Applies to `zurvan_read_page`, `zurvan://file/{+path}` and the `source` argument of `zurvan_claim_add`. `wiki/traces/` is excluded because a full-mode trace twin carries raw query text (Z1a). Named test family `test_mcp_read_gate_*`, one case per path the 2026-10-19 probe reached. |
| stdout purity | `#![deny(clippy::print_stdout, clippy::print_stderr)]` except in the transport; subprocess stdout is captured, never inherited |
| published stores untouched | opened `READ_ONLY`; cache written only under `data/cache/`; a write-mode open of either store is a compile-time impossibility (no code path) |
| one op per write | §6 |
| no instrument leak | eval runs in Python unchanged; Rust returns its stdout verbatim |
| no remote exposure | stdio only; no listener; no network crate in the dependency tree (checked by `cargo tree`) |
| no write path to `wiki/` | the crate contains **no code that opens a file for writing under `PROJECT_ROOT`** except the vector cache under `data/cache/`; a reviewer can `grep -rn "File::create\|OpenOptions" src/` and find exactly that one site. Writes exist only as a subprocess call (§6). This makes the ledger's trilemma (G6) hold by construction: a write either goes through the bridge (ledgered), is refused by the gate, or cannot happen at all — there is no silent third path from this binary. |
| PROJECT_ROOT | `--root` if given, else `ZURVAN_ROOT`, else the path compiled in at build time; then resolved, as `config.py` does (`ZURVAN_ROOT or <repo>` then `.resolve()`) |

## 6. Writes — through the ledger, not around it

Decision (mine, flagged): **the five write tools shell out to Python**, one
subprocess per call, which is exactly one CLI invocation = one operation
boundary. This is the design the ledger amendments (§8.4, §9.2) mandate,
and it means zero ledger logic is duplicated.

- New entry point `scripts/mcp_bridge.py` (≈60 lines): reads one JSON
  request on stdin (`{"tool": "decision_add", "args": {...}}`) and calls
  the same `tools.tool_zurvan_*` function the Python server calls today.
  It opens **no operation of its own**: the tool function already carries
  `@mcp_operation`, which opens `kr.operation(writer="mcp",
  caller="mcp:tool_zurvan_decision_add")`, and `kr.operation` joins an
  enclosing one, so an outer operation in the bridge would silently change
  the ledger's `writer` attribution and break
  `tests/test_mcp_operation_boundary.py`. The mutation row is therefore
  identical to today's. `ZURVAN_MCP_READONLY` is passed through from the
  Rust env so the Python-side `enforce_read_only` is the second line of
  defence. The bridge writes one JSON reply on stdout and exits; all its
  own `print()`s are redirected to stderr exactly as `_protocol_safe` does.
  The same bridge serves the two eval tools (§4.8).
- Interpreter: `ZURVAN_PYTHON` if set, else the path recorded at install
  time by `install_mcp_config.py` (the same absolute interpreter rule the
  skill already enforces). Never a bare `python`.
- Cost: ~2–4 s per write (Python import time). Writes are rare and the
  agent is told the write is durable in the ledger; this is acceptable and
  honest. A warm resident Python worker was considered and rejected: it
  would be a second long-lived process holding ledger state, exactly the
  shape that produced orphaned mutations before.
- The Rust side validates arguments (slug regex for `project`, enum values,
  non-empty tags) *before* spawning, so a malformed call never reaches the
  ledger. Validation errors use the Python server's current wording.

## 7. Smoother for the agent (no semantic change)

Each item is something an agent trips on today; each is fixed inside the
existing contract.

1. **Fast `zurvan_status` means it gets called.** The skill says "call it
   first"; at 11.7 s agents skip it and search a stale index. At < 100 ms
   there is no reason to skip.
2. **Status says what generation it serves** (`revision 745, built
   2026-10-18T08:23Z`) and whether a newer index was detected and is being
   loaded. Today an agent cannot tell a reloaded server from a stale one.
3. **Output schemas unchanged.** Every string-returning tool today declares
   `outputSchema {result: string}` and returns `structuredContent
   {"result": text}` (the Python SDK does this for all 13, not only
   `graph_stats`). The Rust server emits exactly that wrapper. A richer
   structured search result was considered and dropped: it would change the
   declared schema and break the `tools/list` fixture gate.
4. **Honest timing line.** Every search result ends with one stderr log
   line (`fts 3ms embed 28ms scan 14ms`) for the operator, never in the
   result — the agent should not be reading telemetry.
5. **Model-absent and index-absent states are explicit** in the result
   (`Semantic search unavailable: model not found at … — keyword results
   only`), never a silent degrade.
6. **Paging and budgets unchanged** (`Showing 1-25 of 3659. More available:
   call again with cursor=25.`; 24,000-char budget; `read_page` slices).
   Agents have learned these strings; they stay.
7. **Startup never blocks the client.** `initialize` and `tools/list` answer
   immediately; the model and cache load in the background; a search that
   arrives before they are ready waits on them (bounded, with a clear error
   at 60 s) rather than failing.
8. **Every retrieval result says which knowledge it answered from** — the
   one new species (§0.1). One header line on `zurvan_search` and
   `zurvan_context`:

   ```
   [index rev 745 built 2026-10-18T08:23Z · ledger rev 751 · 6 writes since · 8 files newer]
   ```

   `index rev` is `index_meta.built_from_revision`; `ledger rev` is
   `knowledge_state.current_revision` read from `data/registry.sqlite`
   (one row, read-only, < 1 ms); `files newer` is the same mtime count
   `zurvan_status` reports. An agent can now cite "answered from revision
   745" and a later reader can check what 745 contained. **Disclaimer, in
   the same breath:** the stamp says what the index was *built from*, not
   that the index is *complete* — the 2026-09-16 incident produced a
   structurally valid index holding 8,000 of 185,303 chunks, and only
   `index_health` can tell that apart. This is a freshness stamp, not R3
   (tool-call provenance events), which remains unbuilt. The parity harness
   strips this line before diffing; `ZURVAN_MCP_STAMP=0` disables it.
9. **Write results name the revision they minted.** The Python sentence
   is kept and a final line is added: `Ledger revision: 752` — the bridge
   reads `kr.current()` after the tool returns. Today an agent is told
   "added" and cannot reference what it added. (A final line rather than a
   reworded sentence so parity can strip it by prefix; plan decision.)

## 7A. Agent-readiness contract

"Agent ready" has a test: an agent with **no human in the loop, no Zurvan
skill file installed, and no prior session** must be able, from the server
alone, to learn how to use it, orient on the store in one cheap call, find
and read what it needs inside bounded results, know what every answer was
based on, recover from every error with a named next call, write back
safely, diagnose and repair its own installation, and run beside other
agents without interfering. Each row is a requirement with its mechanism;
the Python column is what was measured on 2026-10-19.

| # | Requirement | Python today | Rust mechanism |
|---|---|---|---|
| A1 | **Self-describing.** The server tells the client how to use it without a skill file. | `initialize` carries no `instructions` field (verified: keys are `protocolVersion`, `capabilities`, `serverInfo`). Guidance exists only in `~/.claude/skills/zurvan/SKILL.md`, which Codex, Cursor and a fresh machine may not have. | `instructions` on `initialize` and `server/discover`: ~250 words — orient with `zurvan_status`, search then `read_page`, paging contract, what the revision stamp means, write-mode state, write back before finishing. Claude Code injects server instructions into the system prompt (observed in this session for other servers). Text lives in `rust/zurvan-mcp/instructions.md`, `include_str!`-ed, and is pinned by the fixture gate. Cost: ~300 tokens per session, replacing a 4k-token skill for clients that lack it. |
| A2 | **Orientation in one call.** | `zurvan_status` 11.9 s; agents skip it and search a stale index. | < 100 ms (§1); adds generation, model availability, cache state, `CLEAN`/`SUSPECT` from the ledger. |
| A3 | **Bounded, paged, deterministic.** Same call → same page. | Bounded and paged; `graph_expand` order varies per process (§4.6). | Unchanged strings; canonical order. |
| A4 | **Every answer names its basis.** | Nothing on a result says which index it came from. | §7.8 stamp; §7.9 revision on writes. |
| A5 | **Errors are actionable and flagged.** | Error text names the next call (good) but is returned as a normal result with `isError: false`, so a client cannot tell an error from an answer without parsing. | Same text, `isError: true`. The parity harness compares text and asserts the flag. |
| A6 | **Long calls do not look dead.** | No progress; a client timeout on `zurvan_eval_search` or a cold first search is indistinguishable from a hang. | `notifications/progress` when the client supplies a `progressToken` (rmcp supports it): cache build, model load wait, eval tools, bridge writes. Without a token, nothing changes. |
| A7 | **Safe writes.** | Gated, ledgered, but the result does not say what was minted. | Unchanged gates; argument validation before spawn; revision in the result. |
| A8 | **Self-diagnosis and repair.** | `doctor_mcp.py` and `e2e_mcp_smoke.py` exist but need the right Python and `PYTHONPATH`; an agent on a fresh machine cannot run them from the binary. | `zurvan-mcp doctor` — model files, index generation and health, cache validity, registry state, bridge interpreter, both protocol lifecycles — each line ending in the command that fixes it; `zurvan-mcp self-test` runs the stdio smoke against its own binary; `install_mcp_config.py --engine rust` is idempotent for claude-code, codex and cursor. |
| A9 | **Many agents at once.** | One torch-loaded process per session. | One process per session, but the 465 MB vector map is a shared file in the page cache (one physical copy for N sessions); cache build races end in identical bytes via temp + `rename`; no locks on any store. Per-process memory is *not* quoted: `ps rss` on macOS reported 1 MB for idle Python servers and is not trusted; §9.5 measures the Rust binary with `footprint`/`vmmap` instead. |
| A10 | **Content is data.** | Pages are returned verbatim; tool descriptions are static. | Same, stated as an invariant: tool descriptions and `instructions` never derive from store content, so description-injection through the wiki is impossible by construction; returned pages are never interpreted by the server. |
| A11 | **Buildable by agents.** | — | Each phase's plan is written for a zero-context engineer (exact paths, complete code, verbatim constraints) and runs in its own session; phases 1–2 ∥ 3. |
| A12 | **Fewer round trips** (optional, Q5). | The prescribed workflow is three calls: status → search → read. | `zurvan_orient(topic)`: status line + hybrid top-10 + first 2,000 chars of the top 3 pages in one result. Additive tool; +~350 schema tokens per session; phase 7. |

Rows A1, A5, A6 and A8 are the ones that change what an agent *sees*; the
rest are speed or invariants already described. None changes a result
string the parity gate compares.

## 8. Repository layout

```
rust/zurvan-mcp/            new crate (not a workspace member of anything else)
  Cargo.toml                rmcp, tokio, rusqlite(bundled), candle-core,
                            candle-nn, candle-transformers, tokenizers, memmap2,
                            rayon, walkdir, memchr, serde, serde_json, anyhow, tracing
                            (`cargo tree` must show no hf-hub/ort/reqwest/hyper;
                            crates.io fetches at build time are the one network use)
  src/main.rs … (modules as in §4; plus doctor.rs, selftest.rs)
  instructions.md           the A1 text, include_str!-ed and fixture-pinned
  tests/                    Rust unit tests (path safety, fusion arithmetic, paging)
  fixtures/tools_list.json  golden tools/resources/prompts listing from Python server
  fixtures/adhoc_queries.json
                            the 20 ad-hoc parity queries (no instrument items, no
                            wiki content)
~/.zurvan/parity/           golden outputs (contain wiki excerpts; never in git)
scripts/mcp_bridge.py       new (§6, §4.8)
scripts/install_mcp_config.py
                            gains --engine {python,rust}; rust emits the binary path
scripts/e2e_mcp_smoke.py    gains --server <cmd>; run against both servers
scripts/mcp_parity.py       new: drives both servers over stdio with the same
                            calls, diffs results; used by the gate (§9)
tests/test_mcp_rust_parity.py
                            pytest wrapper that skips if the binary is not built
docs/mcp/rust-server.md     build, install, verify, rollback
~/.claude/skills/zurvan/SKILL.md §12a and its ~/.codex twin
                            gain the Rust install block (the two copies must stay
                            identical — diff them after editing)
```

The Python server is **not deleted** in this project. It remains the
reference implementation and the fallback; `install_mcp_config.py --engine
python` restores it in one command. Deletion is a separate decision once the
Rust server has run for a while.

## 9. Verification — what is gated

### 9.1 Parity (the main gate)

`scripts/mcp_parity.py` launches both servers, sends identical requests,
and requires **byte-identical** text results for:

- `tools/list`, `resources/list`, `resources/templates/list`, `prompts/list`
  (modulo key order);
- `zurvan_status` minus the timing/generation lines it newly adds;
- `zurvan_search` hybrid and keyword for the **50-item RETIRED instrument**
  (`~/.zurvan/instruments/`, referenced by index; it may measure, never
  gate a promotion, and this is a measurement) plus 20 ad-hoc queries
  written fresh for this project and checked in as a fixture. The 203-item
  Z1b DEV set is **not** used: it is frozen under Z1b Rev 3 and has a
  leak-scan incident on record. Rank agreement is defined with a
  **tolerance**: sqlite-vec accumulates cosine in f32 and Rust cannot
  reproduce its accumulation order, so two chunks whose Python hybrid
  scores differ by less than `1e-6` are a *near-tie* and may appear in
  either order; every other position must match exactly, and the number of
  near-tie swaps is printed. Exact ties are already resolved by `chunk_id`
  in both implementations.
- `zurvan_graph_neighbours` on 10 nodes including the hub, across 3 pages
  each (exact); `zurvan_graph_expand` on the same nodes (order-normalised,
  §4.6);
- `zurvan_context` for 10 topics (exact for search sections,
  order-normalised for the graph section);
- `zurvan_read_page` on 5 files including one > 256 KB (slicing) and the
  three error cases (absolute, traversal, raw/); plus the **read-gate
  family** (`.git/HEAD`, `wiki/../.git/config`, `.claude/settings.json`,
  `.remember/now.md`, `sources/<any>`, `data/registry.sqlite`,
  `wiki/traces/<any>`) which must be *refused* by Rust — here the gate
  asserts Rust ≠ Python until the Python fix lands, then equality;
- `zurvan_search` keyword mode on the full corpus: identical **order**, not
  just hit set (§4.5);
- both protocol lifecycles (§4.1): the harness runs once pinned to
  2025-11-25 and once to 2026-07-28;
- the 5 write tools in read-only mode (identical refusal string) and in
  write mode against a throwaway `ZURVAN_ROOT` (page content and ledger
  mutation row identical except timestamps/ids).

### 9.2 Existing suites

`pytest` 0 failed; `e2e_mcp_smoke.py --server rust` passes **and its stderr
shows no "Failed to parse JSONRPC"**; `doctor_mcp.py` extended to the Rust
binary; `public_repo_guard.py`; `git diff --check`; `check.sh` gates
unaffected (the Rust server is not in any gate's path).

### 9.3 Retrieval invariance

On one coherent snapshot (`measurement_snapshot.py --require search`):
`eval_search --hybrid` for the Python reference, and `mcp_parity.py`'s
top-1 / top-3 / MRR computed from the **Rust server's** results (§4.8) —
both recorded in CHANGELOG.md with the snapshot id. Expected: identical
numbers. Any difference beyond the near-tie tolerance of §9.1 is a stop,
not a note. The 50-item instrument is RETIRED and used here to *measure*
parity, never to promote — exactly its declared status.

### 9.4 Performance

A `bench` subcommand prints p50/p95 for each tool over 50 warm calls, plus
cold-launch-to-first-search; the numbers go in the closure entry next to
the §0 baseline, measured the same way (over stdio, same store, same
generation). They are reported, not gated, because the machine is shared
and noisy; the gate is parity. If warm hybrid p50 is not under the §1
target, the closure entry says so and why; the number is never re-run
"until it looks good".

### 9.5 Memory

Measured with `footprint` (or `vmmap -summary`) after 100 hybrid searches:
heap (malloc) < 300 MB; the 465 MB matrix appears as a file-backed mapping,
not dirty memory. Checked once and recorded. `ps rss` is not used (§7A A9).

### 9.6 Agent-readiness

`e2e_mcp_smoke.py --server rust` gains checks for A1 (`instructions`
present and non-empty on both lifecycles), A5 (an error result carries
`isError: true`), A6 (a progress notification arrives during a forced
cache build), and A8 (`zurvan-mcp doctor` exits 0 on a healthy install and
names a fix on a deliberately broken one — model dir moved).

## 10. Phased plan

| Phase | Deliverable | Falsifies |
|---|---|---|
| **0 — spike (throwaway, ~½ day)** | a ~200-line Rust program that loads the index, builds the f32 matrix, embeds the 50 instrument queries + 20 ad-hoc with candle from the local safetensors, and prints Rust-vs-Python vector cosine, top-10 agreement under the §9.1 tolerance, near-tie count, and scan timing | the two risks that decide the whole project: candle embedding parity (incl. `max_seq_length` 256 and pooling) and scan speed on this machine |
| **1 — skeleton** | crate, rmcp stdio, both lifecycles, full `tools/list`/resources/prompts from the golden fixture, `zurvan_status` with per-generation caching, `zurvan_read_page` with the **read allow-list** and its test family, resources, prompts, stdout lint, smoke passes | protocol compatibility with Claude Code and Codex; the read gate |
| **2 — graph** | three graph tools, paging, parity on hub node | SQL/ordering port |
| **3 — search** | FTS + embed + mmap scan + fuse; keyword live scan; parity on dev set; `eval_search` before/after | ranking invariance |
| **4 — context** | `export_context` port, golden-file parity | the biggest line-for-line port |
| **5 — writes** | bridge script, five tools, ledger parity in a throwaway root | one op per write |
| **6 — ops & agent readiness** | hot reload, cache pruning, `instructions` (A1), `isError` (A5), progress (A6), `doctor` + `self-test` (A8), `install_mcp_config --engine rust`, `docs/mcp/rust-server.md`, skill §12a twins, bench + memory numbers, AGENTS/CHANGELOG entries, decision write-back | the thing is installable, reversible and self-explaining |
| **7 — optional** (Q5) | `zurvan_orient` composite tool (A12) with its own parity fixture | one round trip instead of three |

Each phase ends with its parity slice green before the next starts. Phases
1–2 and 3 can run in parallel in separate sessions; 4 depends on 3; 5 and 6
depend on 1.

**Outside this project, before or alongside phase 1:** the Python server's
read gate gets the same allow-list (`scripts/mcp_security.py`,
`mcp_resources.py`, `mcp_tools.py`, the `test_mcp_read_gate_*` family). It
is a separate change with its own CHANGELOG entry because it is live today
in every session that has the server enabled, and it must not wait on a
rewrite.

## 10.1 Scorecard (rev 2; re-scored at closure)

| Axis | Now | What moves it higher (named artefact) |
|---|---|---|
| Agent latency (skill-prescribed first three calls) | **2/10** — ~40 s | `rust/zurvan-mcp` phases 1–3; closure target 9/10 at < 3 s |
| Answer identity with the reference | **n/a** — no second implementation exists | `scripts/mcp_parity.py` green on both lifecycles → 9/10 |
| Read surface honesty | **3/10** — reads `sources/`, `.git`, session memory | the allow-list + `test_mcp_read_gate_*` in both servers → 8/10 (symlinks and future top-level dirs keep it off 10) |
| Freshness legibility to the agent | **4/10** — only via a tool nobody calls at 12 s | §7.8 stamp + fast status → 8/10; `index_health` in the stamp would make it 9 |
| Invariant preservation (ledger, stores, stdout) | **8/10** by design — nothing new can write | stays 8 until the bridge's ledger-row parity test exists → 9 |
| Agent readiness (§7A, no skill file, no human) | **3/10** — no `instructions`, errors unflagged, no progress, no self-repair from the binary | A1 + A5 + A6 + A8 → 8/10; A12 and `index_health` in the stamp → 9 |

## 10.2 IOU ledger

Named promises this document makes and does not pay:

- **IOU-1 `fts-keyword-mode`** — FTS5-backed keyword search as an extra
  `mode`; changes results; needs its own eval entry. Owner: Raouf's call.
- **IOU-2 `structured-search-result`** — richer `structuredContent` for
  search under a versioned schema bump (spec §Breaking CLI/MCP/schema
  semantics). Dropped from this project to keep the fixture gate exact.
- **IOU-3 `terse-tool-descriptions`** — the 4.2k-token schema cost is
  unchanged here because descriptions are part of parity; a deliberate
  rewrite of the 14 descriptions is a separate, measurable change.
- **IOU-4 `python-expand-determinism`** — sort the Python BFS frontier
  (§4.6) so expand/context parity becomes exact.
- **IOU-5 `python-read-gate`** — the allow-list in the Python server
  (§10, "outside this project"). Due before phase 1 closes.
- **IOU-6 `index-health-in-stamp`** — carry `index_health`'s verdict in the
  §7.8 stamp so "built from rev 745" cannot hide a truncated index.
- **IOU-7 `orient-tool`** — A12. **PAID** by plan Task 14 (`611d6d2`): in
  both servers; measured schema cost +1,053 characters (~+263 tokens/session).
- **IOU-8 `python-instructions`** — give the Python server the same A1
  `instructions` text. **PAID** by plan Task 3 (`903023e`).
- **IOU-9 `python-fts-rowid`** — the spike found Python's FTS stage costs
  5.3 s p50 because it selects `chunk_id` from an external-content FTS
  table (one `chunks` lookup per matching row). Selecting `rowid` returns
  the identical score set at 0.2 s. Same results, so no ranking change, but
  it touches the retrieval path, so it ships with a before/after
  `eval_search` entry in CHANGELOG.md. Not in this project.
- **IOU-10 `python-graph-stats-creates-db`** — found in Task 5:
  `graph_query.get_stats` opens read-WRITE, so with `data/` present and the
  graph missing it CREATES an empty `data/graph.sqlite` (the invariant every
  other reader honours with `mode=ro`). Rust reproduces the message, not the
  side effect.
- **IOU-11 `python-validate-gold-none`** — found in Task 11:
  `tool_zurvan_validate_gold` passes `DEFAULT_GOLD = None` and returns
  `Error: expected str, bytes or os.PathLike object, not NoneType` on every
  call. The bridge reproduces it faithfully.
- **IOU-12 `persist-store-metadata`** — measured at closure: the first
  `zurvan_status` in a fresh Rust process waits for the one full scan of
  `chunks` (6.5 s cold) that the startup warm-up performs. Caching the
  per-generation metadata next to the vector cache would make the first call
  as fast as the rest (8 ms).
- **IOU-13 `keyword-scan-speed`** — Rust keyword mode is 618 / 1,224 ms
  p50 / p95 against the 400 ms target: it reads all 235 MB of corpus files per
  query, now through the read gate. An FTS5 keyword mode (IOU-1) would make it
  milliseconds but changes results.

## 10.4 Closure measurements (2026-10-19, live store, index rev 745)

Parity gate, `scripts/mcp_parity.py`, every group, on **both** protocols:

| Protocol | Cases | PASS | NEAR_TIE | EXPECTED_DIVERGENCE | FAIL | Instrument (hybrid) A = B |
|---|---|---|---|---|---|---|
| 2025-11-25 | 312 | 302 | 0 | 10 | 0 | top-1 0.72, top-3 0.74, MRR 0.7372 |
| 2026-07-28 | 312 | 302 | 0 | 10 | 0 | top-1 0.72, top-3 0.74, MRR 0.7372 |

The 10 expected divergences are the 8 read-gate probes (Rust refuses what
Python serves, IOU-5) and the 2 eval tools (Rust's text is Python's plus the
one-line "Evaluates the Python reference retriever" prefix, exactly).

Latency, Rust measured against itself (the machine shared only with other
sessions' idle servers); Python from the same-day A/B runs:

| Tool | Python p50 / p95 | Rust p50 / p95 | §1 target |
|---|---|---|---|
| hybrid search | 11,844 / 27,983 ms (6.8 s quiet) | 129 / 194 ms | < 150 ms — p50 met, p95 not |
| keyword search | ~5,000–15,000 ms | 618 / 1,224 ms | < 400 ms — **not met** (IOU-13) |
| context | 10,962 / 27,662 ms | 174 / 283 ms | < 400 ms — met |
| orient | 18,149 / 22,447 ms | 138 / 208 ms | — |
| status | 2,103 / 12,051 ms | 7.6 / 62 ms warm; 6.5 s first call | < 100 ms — met warm, **not** first call (IOU-12) |
| graph neighbours / expand | 3.3 / 17 · 4.4 / 89 ms | 2.5 / 15 · 4.1 / 59 ms | < 20 ms p50 — met |
| read_page | 0.9 / 1.1 ms | 0.2 / 0.3 ms | < 5 ms — met |

Memory after 100 hybrid searches (`vmmap -summary`): physical footprint
**137.4 MB** (peak 142.8); the vector cache is one 464.8 MiB file-backed
mapping, 454.4 MiB resident, 0 dirty. Target < 300 MB heap — met.

Retrieval invariance (§9.3): the coherent snapshot refused
`SNAPSHOT_NOT_READY` (search built from revision 745, knowledge revision
beyond it), so there is no snapshot id; the evidence is the A/B run on the
identical rev-745 bytes, with identical metrics on both protocols.

Final whole-branch review (fresh reviewer, 2026-10-19) found no Critical and
four Important defects, each reproduced and then fixed test-first:
`zurvan_claim_add`'s `source` was not held to the §5 allow-list (a claim could
copy `.git/config` into a readable page); a promote between reading the
generation and opening the index by path mixed two generations in one call
(1,866 of 6,064 calls in a promote storm; 0 after); hybrid search needed a
writable `data/cache/` (now a private temp-dir fallback); and the fixed
`wiki/` and `eval/` resources read a planted symlink. §9.3's coherent-snapshot
check is **UNVERIFIED** for this closure (no snapshot could be pinned); the
identity evidence is the A/B run above, not a Z0 measurement.

Post-fix end-to-end re-run (2026-10-20, binary `8ea25bf`, live index rev 785,
303,453 chunks): the parity gate again gave 312 cases per protocol, 302 PASS,
10 EXPECTED_DIVERGENCE, 0 FAIL on both 2025-11-25 and 2026-07-28, with the
instrument identical A = B (top-1 0.72, top-3 0.74, MRR 0.7372). Also green:
`cargo test` (94 passed) plus the ignored candle-vs-Python embedding parity
test run explicitly; `zurvan-mcp doctor` PASS (one WARN: 5 files newer than
the index); `self-test` 7/7; `e2e_mcp_smoke.py` on both servers with 0
JSON-RPC parse failures and the live wiki untouched. Gate timings are one
sample per case with both servers running side by side, so they are not a
benchmark; the latency table above stands.

Re-scored (§10.1): agent latency **8/10** (first three calls ≈ 6.7 s cold,
< 0.3 s warm; the cold first status keeps it off 9); answer identity **9/10**;
read surface **8/10** in Rust, still 3/10 in Python until IOU-5; freshness
legibility **8/10**; invariant preservation **9/10** (ledger rows equal in
parity, and the publication-control defect the review found is fixed and
tested); agent readiness **8/10**.

## 10.3 Spike verdict (phase 0, 2026-10-19)

**GO.** On the live store (index rev 745, 303,080 chunks), for the 50
instrument items by index plus 20 ad-hoc queries: hybrid top-10 identical
to Python on **70/70**, query-vector cosine min 1.000000, max score
difference 1.91e-7. Warm Rust hybrid search **139 ms p50 / 200 ms p95**
against 6.8 s quiet (11.9 s same-session) for Python; matrix build 6.1 s
cold; model load under 0.3 s; no network crates; 7.5 MB binary. p95 misses
the < 150 ms target, because of FTS. One plan change came out of it: FTS
selects `rowid` and the vector cache stores `chunks.rowid` per row (IOU-9
for the Python side). Full table in the plan's "Spike verdict" section.

## 11. Decisions I made (check these while reading)

1. Writes (and the two eval tools) via a per-call Python subprocess that
   opens no operation of its own, so the ledger row is identical. (§6, §4.8)
2. `candle` on the local safetensors for query embedding — no ONNX, no
   network crates; hard parity gate with a stated `1e-6` near-tie
   tolerance. (§4.4, §9.1)
3. Vector matrix as an mmap'd derived file under `data/cache/`, keyed by the
   index manifest digest; never a heap copy. (§4.3)
4. Keyword mode stays a live file scan (same results as today), not FTS5.
   An FTS5 keyword mode is a cheap *later* addition if you want it, but it
   changes results and so needs its own eval entry. (§4.5)
5. Python server kept as reference and fallback; nothing deleted. (§8)
6. stdio only; Z11 (daemon, capabilities, Tasks) deliberately not started.
7. `rmcp` as the protocol crate; if the spike shows it cannot express a
   needed feature (`{+path}` template, cache hints), hand-rolled JSON-RPC is
   the fallback — the surface is 9 methods.
8. Rust defines a canonical graph-expansion order the Python code lacks;
   parity for expand/context-graph is order-normalised. (§4.6)
9. Output schemas kept exactly (`{result: string}`); no richer structured
   search result. (§7.3)
10. Parity instrument: the 50-item RETIRED set by index + 20 fresh ad-hoc
    queries; goldens under `~/.zurvan/parity/`; Z1b DEV untouched. (§9.1)
11. A read allow-list, not a longer deny-list: the readable surface is
    what the index covers plus four root docs. (§5)
12. The per-result revision stamp is on by default, strippable for parity,
    and explicitly not R3. (§7.8)
13. Both MCP lifecycles supported; neither is assumed of Claude Code. (§4.1)
14. Error results carry `isError: true` with unchanged text. (§7A A5)
15. `instructions` is sent on initialize/discover and pinned by the
    fixture; tool descriptions and instructions never derive from store
    content. (§7A A1, A10)
16. Progress notifications only when the client asks for them. (§7A A6)
17. `doctor` and `self-test` live in the binary, not in Python. (§7A A8)

**Follow-up outside this project (found by the builder check):** make
`expand_graph_context` deterministic in Python by sorting the frontier the
same way (§4.6). One line, its own CHANGELOG entry; the MCP smoke and any
test that compares graph output across runs has been flaky-by-design
without it.

## 12. Open questions for Raouf

1. Is a **2–4 s write** acceptable (one Python subprocess per write tool
   call)? The alternative, a resident Python worker, is faster but is the
   process shape that caused orphaned mutations before.
2. Should the Rust server become the **default** in `~/.claude.json` and
   Codex at the end of phase 6, or run side-by-side under a second server
   name (`zurvan-rs`) for a trial period first? Side-by-side doubles the
   schema tokens every session; default-with-rollback is my recommendation.
3. Do you want the **FTS5 keyword mode** (§11.4) in this project as an
   *additional* `mode` argument, or left out entirely?
4. The **read allow-list** (§5) will refuse paths the Python server serves
   today. Is there any path outside `wiki/`, `docs/`, `eval/` and the four
   root docs that an agent legitimately needs to read through Zurvan? If
   not, the Python fix (IOU-5) goes in first, as its own change.
5. **`zurvan_orient`** (A12): one extra tool, three round trips become one
   for the prescribed workflow, ~350 more schema tokens every session. In
   (phase 7) or out?

### 12.1 Answers (Raouf delegated them to the recommendations, 2026-10-19 11:07)

1. **Writes at 2–4 s through a per-call Python subprocess: yes.** No
   resident Python worker.
2. **Cut-over: default with rollback** at the end of phase 6. One command
   (`install_mcp_config.py --engine python`) restores the Python server; the
   prior harness configs are backed up under `~/.zurvan/backups/`.
3. **FTS5 keyword mode: out of this project.** Stays IOU-1.
4. **Read allow-list: no extra paths.** The Python fix (IOU-5) goes first as
   its own change; until it lands, the parity harness reports the read-gate
   group as an expected divergence, not a pass.
5. **`zurvan_orient`: in, as phase 7**, added to both servers so the
   listings stay equal, with the measured schema-token delta recorded.
