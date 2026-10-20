# The Rust MCP server (`zurvan-mcp`)

A drop-in replacement for `scripts/mcp_server.py`: the same 15 tools, 7
resources and 4 prompts, the same argument schemas and the same answers, served
by a native binary. Design: `docs/superpowers/specs/2026-10-19-rust-mcp-server-design.md`.
The Python server stays as the reference implementation and the fallback.

## What runs where

| Tools | Engine |
|---|---|
| `zurvan_search`, `zurvan_context`, `zurvan_orient`, `zurvan_read_page`, `zurvan_status`, the three graph tools, all resources and prompts | Rust, native |
| `zurvan_remember`, `zurvan_decision_add`, `zurvan_decision_set_project`, `zurvan_claim_add`, `zurvan_question_add`, `zurvan_eval_search`, `zurvan_validate_gold` | Python, one process per call through `scripts/mcp_bridge.py` |

Writes stay in Python on purpose: each one goes through the same
`scripts.mcp_tools` function and the same knowledge-ledger operation boundary as
before, so a write is still exactly one mutation attributed `writer=mcp`. The
eval tools measure the **Python** retriever and their output says so.

## Build

```bash
cargo build --release --manifest-path rust/zurvan-mcp/Cargo.toml
```

The binary is `rust/zurvan-mcp/target/release/zurvan-mcp` (about 10 MB). It
needs no network at run time and no Python for reads. Query embeddings use the
`all-MiniLM-L6-v2` weights already in the Hugging Face cache
(`~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/`);
`ZURVAN_MODEL_DIR` points elsewhere.

## Install

Print the config for your client and paste it where the generator says:

```bash
python scripts/install_mcp_config.py --client claude-code --write-mode --engine rust
```

```bash
python scripts/install_mcp_config.py --client codex --write-mode --engine rust
```

The Rust entry differs from the Python one in two places: `command` is the
binary (with `args = ["serve"]`), and `env` gains `ZURVAN_PYTHON`, the absolute
interpreter the write bridge runs. Restart the client afterwards; a server that
is already running keeps its old command.

## Verify

```bash
rust/zurvan-mcp/target/release/zurvan-mcp doctor
```

```bash
rust/zurvan-mcp/target/release/zurvan-mcp self-test
```

```bash
python scripts/e2e_mcp_smoke.py --server rust/zurvan-mcp/target/release/zurvan-mcp
```

`doctor` checks the root, the index and its freshness, `index_health`, the
graph, the ledger, the model, the vector cache, the write bridge and both MCP
lifecycles; every non-OK line is followed by a `fix:` line. `self-test` drives
the binary over stdio and never writes. `python scripts/doctor_mcp.py --engine
rust` runs the same doctor.

To compare the two servers on your own store (read-only; writes use throwaway
stores the harness creates and removes):

```bash
python scripts/mcp_parity.py --root ~/Desktop/Raouf/Zurvan --only listing,status,graph,search_hybrid
```

The console and the report (`~/.zurvan/parity/<time>.json`) carry case ids,
verdicts and sizes only — never page content, never an instrument query.

## Roll back

```bash
python scripts/install_mcp_config.py --client claude-code --write-mode --engine python
```

Paste the printed Python entry back and restart the client.

## What an agent sees that is new

- `initialize` carries server **instructions** (the workflow, the paging
  contract, the write rules) — the Python server now sends the same text.
- Search, context and orient results start with a **revision stamp**:
  `[index rev 745 built 2026-10-18T08:23Z · ledger rev 758 · 13 writes since · 18 files newer]`.
  It says what the index was built from, not that it is complete. `ZURVAN_MCP_STAMP=0` removes it.
- Successful writes end with `Ledger revision: N`, the revision that call
  minted; a write that minted nothing (a refused claim, a bad slug) has no
  such line.
- Error texts are returned with **`isError: true`** (the Python server returns them unflagged).
- **Progress notifications** when a call carries a `progressToken`: write and
  eval calls tick every 2 s, a first hybrid search reports the vector-cache build
  and the model load.
- `zurvan_status` adds `Serving:`, `Ledger:`, `Model:` and `Vector cache:` lines.
- **Reads are allow-listed.** `zurvan_read_page`, every `zurvan://` file
  resource, keyword search and the `source` of `zurvan_claim_add` read only `wiki/` (never `wiki/traces/`), `docs/`, `eval/` and
  `README.md`, `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md`, judged on the names the
  filesystem stores, minus `docs/evaluation/retrieval/` (retriever reports,
  excluded from the index too). Since 2026-10-20 the Python server applies
  the same allow-list (IOU-5), so the two servers refuse the same paths.
- **Invalid UTF-8 late in a file:** Rust decodes the whole file and reports
  it as binary; Python decodes lazily and can serve a slice that ends before
  the bad bytes. Treat a binary answer as "not a text page".

## Operations

- **FTS pool.** Hybrid search keeps four read-only connections per index
  build, memory-mapped, and runs the full-text stage over four rowid ranges in
  parallel. Each row's bm25 is the same as in one query (bm25's statistics are
  table-wide), so results are identical; it is reopened when the index is
  rebuilt.
- **Keyword cache.** `data/cache/keyword-<key>.zkw` (0600) holds the
  lowercased text of every readable file, memory-mapped and shared by all
  servers; it is built at start-up and reused across restarts. Each query
  re-walks `wiki/` and `docs/`, re-applies the read gate, and checks every
  file's identity (device, inode, size, mtime), so an edit is seen at once (the
  changed file is read fresh). After 256 changed files it is rebuilt. Results
  are exactly those of the uncached scan.
- **Index metadata.** `data/cache/meta-<key>.json` (0600) holds the counts
  `zurvan_status` reports for one index build, so a fresh server answers its
  first status in ~75 ms instead of rescanning `chunks` (~6.5 s).
- **Vector cache.** The first hybrid search after an index rebuild (or the
  background warm-up at start) writes `data/cache/vectors-<key>.zvec`: the
  index's vectors as one memory-mapped matrix, about 490 MB, built in about 9 s.
  The key binds the index file's identity, build time and embedding space, so a
  rebuilt index gets a new cache; the newest two are kept. The directory is 0700
  and the files 0600. `zurvan-mcp build-cache` builds it ahead of time.
  A server that cannot write `data/cache/` (a sandboxed session) builds a
  private copy in the temp directory instead (`zurvan-mcp-vectors/`, 0700,
  refused if it is a symlink or owned by another account) and never writes
  the store; if neither place is writable, hybrid search falls back to
  keyword results and the notice names both paths and the fix.
- **Hot reload.** `zurvan index search` promotes a new index atomically; the
  server notices on its next call and serves the new generation, waiting for the
  new cache rather than mixing generations: every connection is checked
  against the generation its metadata and vectors came from, and a call that
  a promote lands in starts again on the new one (twice at most; under a
  storm of promotes it answers `the search index was replaced during this
  call; call again`).
- **Environment.** `ZURVAN_ROOT` (or `--root`) picks the store;
  `ZURVAN_SEARCH_DB` the index; `ZURVAN_MCP_READONLY` (writes only when exactly
  `0`); `ZURVAN_MCP_ALLOW_RAW_READ`; `ZURVAN_PYTHON` (write bridge);
  `ZURVAN_CODE_ROOT` (where `scripts/` lives, default: the checkout the binary
  was built from); `ZURVAN_MODEL_DIR`; `ZURVAN_MCP_STAMP`.

## Measured (2026-10-19, live store, index rev 745, 303,080 chunks)

| | Python server | Rust server |
|---|---|---|
| hybrid search p50 / p95 | 11,844 / 27,983 ms (6.8 s warm on a quiet machine) | 70 / 92 ms (2026-10-20; was 129 / 194 ms) |
| keyword search p50 / p95 | several seconds | 80 / 110 ms (2026-10-20; was 618 / 1,224 ms) |
| context p50 / p95 | 10,962 / 27,662 ms | 174 / 283 ms |
| `zurvan_status` p50 / p95 | 2,103 / 12,051 ms | 7.6 / 62 ms warm; 74 ms on a fresh process's first call (2026-10-20; was 6.5 s) |
| physical footprint after 100 searches | — | 137 MB (+ the 465 MiB file-backed vector map, clean) |

Parity on the same store, every group, on both MCP protocol versions: 312 cases,
302 identical, 10 expected divergences (the read allow-list refusing what Python
serves, and the eval tools' one-line prefix), 0 failures. The instrument scores
the same on both servers. Since 2026-10-20 keyword search and the first
`zurvan_status` meet their targets (see Operations), and so does hybrid p95
under 150 ms (92 ms, after the FTS pool). At closure these targets were missed: hybrid p95 under 150 ms,
keyword under 400 ms, and the first `zurvan_status` of a fresh process, which waits for
the start-up metadata scan. Design §10.4 has the full table.
