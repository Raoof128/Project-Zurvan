# Z1b plan + dry-run gauntlet — progress record (2026-10-02, in flight)

**Status:** the Z1b plan is being written and gauntleted. It is **not finished and not accepted**. No production Z1b code is on `main`. The dry-run code lives only on local throwaway branches (`z1b-gauntlet` and the per-writer `z1b-g-*` branches, under `.worktrees/`). None of it has been pushed.

**Scope (Raouf, 2026-10-02):** plan and gauntlet only. Not done, and not to be done until the plan is accepted:
- no `sudo`;
- no identity creation;
- no broker install;
- no sealed or DEV authoring;
- no model spend.

The Z1a leftovers (#8, #23a), Boundary literal narrowing, Boundary audit optimisation and the public mirror are out of scope.

## Method

- Ground truth came from three read-only surveys: Z0 snapshot APIs plus the canonical spec; Z1a eval/CLI/guards; Boundary plus the platform.
- An orchestrator-owned interface contract came next, followed by 7 writer agents (Sonnet), each in its own throwaway worktree. Each writer wrote the real code and tests red-then-green, ran mutation checks on its security guards, checked Python 3.9 compatibility for the sealed package, and emitted plan task text byte-identical to the code it ran.
- Writer branches are merged into `z1b-gauntlet`.

| Plan task | State |
|---|---|
| 1 foundation (codes, SealPaths, clean_env) | done, 100 tests |
| 2 strata spec + coverage | done, 107 tests |
| 3 closed serializer (+4,500-case fuzz, gate 2) | done, 195 tests |
| 4 deterministic grader | done, 79 tests |
| 5 corpus content digest + export | done, 24 tests |
| 6 signed manifest (ssh-keygen -Y) | done, 50 tests |
| 7 hash-chained ledger, budget, cache, incidents | done, 61 tests |
| 8 bundle intake (O_NOFOLLOW, uid/nlink, unbundle+fsck) | done, 87 tests |
| 9 candidate protocol, runner, real sandbox-exec, sweep | done, 156 tests |
| 10 broker | **in progress** |
| 11 leak scan, canaries, burn, reseal | **in progress** |
| 12 operator scripts (render-only) | done, 130 tests |
| 13 dev client + `zurvan eval z1b` CLI | done, 91 tests |
| 14 DEV set tooling | done, 17 tests |
| 15 sealed authoring (fake model runner) | done, 83 tests |
| 16 promotion workflow (first production caller of `require_promotion_eligible`) | done, 99 tests |
| 17 guards: repo-guard allowlist, deny rules, check.sh gate 24, live tests, closure matrix, py3.9 | done |
| 18 red-team + docs | execution time only |

Merged gauntlet branch: **1396 passed, 1 skipped**. The skip is the live-seal module, which reports `Z1b: NOT CONFIGURED`. Tasks 10 and 11 are not yet merged.

## Spec ↔ implementation discrepancies found (recorded, not bridged silently)

- **D1.** No content-based corpus digest exists. `manifest_digest`/`corpus_manifest_sha` hash mtime and inode. Z1b defines `corpus_sha256` over relpath and file content.
- **D2.** The index has no line numbers; chunks carry only `source_path` + `heading`. Locator kinds are therefore `page|section`, and `line_range` is refused.
- **D3.** `eval_search.evaluate` has no Recall@10, no per-stratum metrics and no bootstrap. Z1b has its own grader.
- **D4.** `capture_coherent` returns `BuildSnapshot`, and the snapshot's `search.sqlite` is a hardlink of the live inode. The sealed corpus must be a real copy.
- **D5.** The retriever needs the MiniLM model from the dev user's HF cache, which the candidate identity cannot read. A runtime copy works offline under the sandbox (measured: 14.9 s cold, of which 13.5 s is the torch import).
- **D6.** macOS sudo keeps `HOME`, so a sealed-side `git` would read the dev user's `~/.gitconfig` (`core.fsmonitor` gives code execution). The sealed side never reads `$HOME`, and every subprocess gets a clean environment.
- **D7.** `protected_eval_context()` is a ContextVar and does not cross processes. The candidate adapter sets it itself.
- **D8.** No Claude Code deny rules existed for sealed paths.
- **D9.** `public_repo_guard.py` had no allowlist mechanism.
- **D10.** `age` and `claude` live in dev-writable locations. The sealed side would execute them as `zurvaneval`, which is a T2-class breach. `install.sh` copies pinned, root-owned binaries.
- **D11.** Canonical §9.3 names 16 strata in prose with no ids. Z1b defines the ids.
- **D12.** A predeclaration cannot name the commit that contains it. The binding is the commit itself (spec §5.4, "committed at `<sha>`").
- **D13.** `git bundle verify` passes a corrupted full-history bundle, and a bundle can list the frozen commit on a side ref while running another HEAD. Intake unbundles with fsck and requires HEAD == commit.
- **D14.** For queries in flight, the candidate's confidentiality does rest on the sandbox, because the candidate reads the queries. That is contrary to spec §2 T3's wording. A per-user pf block, cron/at deny, and a post-run sweep for candidate-owned files and processes (sweep → burn) are added.
- **D15.** Authoring audit files and `claude` session state hold plaintext items. They move to a Time Machine-excluded `private/` dir (T7).
- **D16.** The scheduled leak scan reads `~/Desktop` (TCC-protected) as another user. Unverified without the identity; an unreadable target refuses the run (`SCAN_INCOMPLETE`).

## Fail-closed refusal codes added beyond spec §8 (need Raouf's ack)

`BAD_ARGS`, `BUNDLE_INVALID`, `LEDGER_INVALID`, `RESERVE_NOT_ELIGIBLE`, `DECLARATION_INVALID`, `SCAN_INCOMPLETE`, `CANDIDATE_FAILED`, `SANDBOX_VIOLATION`, `SEAL_COMPROMISED`, `INTERNAL`. A `RUN_IN_FLIGHT` code is proposed.

## Orchestrator rulings so far

- R1 (T1): accept clean_env extra add-only + reserved prefixes; sudo_uid canonical decimal, uid 0 refused.
- R2 (T7): same-key in-flight -> LEDGER_INVALID accepted for now; propose RUN_IN_FLIGHT code for Raouf ack. Broker MUST fail_run(INTERNAL) every unresolved_runs() id at startup under lock (charges budget; fail closed).
- R3 (T6): hex-run>=33 ban outside 5 whitelisted keys accepted. Burn reasons enum: canary_hit,item_leak,sandbox_violation,operator.
- R4 (T5): corpus export omits wiki/traces/; page path space = relative to wiki/. Candidate adapter + grader + authoring targets use this space.
- R5 (T7): ledger tail truncation undetectable w/o anchor: LOW, threat is crash not adversary (only zurvaneval writes runs/).
- R6 (supersedes R4 path space): page path space = store-relative path EXACTLY as chunks.source_path in the index (e.g. "wiki/x.md"; 2417 chunks live outside wiki/, e.g. docs/). Matches spec §4.3 "wiki/…". corpus_pages() must return store-relative paths over every indexed root in the export (wiki/, docs/...), not wiki-relative. Candidate adapter passes source_path through unchanged. Grader compares literally (after normalisation). Integration fix needed in corpus.py (T5).
- R7 (T3): accept status gains ok/schema/installed_tree; sealcheck gains schema. CHECK_IDS amended by broker writer: drop tm_excluded_sets, tm_excluded_runs (spec §2 T7 only requires keys exclusion; sets are ciphertext); add pinned_binaries_match, interpreter_root_owned, ledger_valid, manifest_valid. Per-item rows in runs/ hold opaque ids + ranks only, never query/target text.
- R8 (T4): accept set_fraction_at, optional corpus_pages, truncate-then-dedupe, abstain=max score in window, paired bootstrap CI of mean difference.
- R9 (T2): abstain_threshold 0.5 provisional (abstain excluded from promotion per spec §4.4). predeclaration schema uses "element" key.
- R10 (T8): accept unbundle+fsck verify (stage volume needs <=2GB headroom), HEAD==commit (client: `git bundle create <f> <commit>` then the bundle HEAD must be that commit — client must create with a ref pointing at commit and HEAD), strict env check.
- R11 (plan integrity): module entry names must be unique across the plan. candidate_run keeps main/run/probe/wipe; broker uses broker_main/broker_run/broker_status/broker_seal_check; other modules prefix (author_main, scan_main...). Assembly re-checks with tests/test_plan_integrity.py.
- R12 (T9): no hard-coded dev username in candidate.sb; dev home only via -D DEVHOME (from pwd of SUDO_UID). Runner python flags -B -E -s accepted. Candidate runtime python = runtime_dir/bin/python3 (install.sh must provide it).
- R13 (T9 sweep): unparsable ps output => SANDBOX_VIOLATION (assume worst: burn sent items), not INTERNAL. Sweep roots add candidate's DARWIN_USER_TEMP_DIR/DARWIN_USER_CACHE_DIR (/private/var/folders/..) and candidate_home minus scratch.
- R14 (T12 HIGH gap): candidate runtime = root-owned venv at runtime_dir/venv built by install.sh: `<anaconda python> -m venv --copies runtime_dir/venv` + `pip install --require-hashes -r scripts/eval_seal/runtime-requirements.txt` (pinned torch/sentence-transformers/sqlite-vec/numpy/pyyaml as in the dev env), then chown root:wheel, 0555/0444; runtime_dir/bin/python3 -> venv python. Run key gains runtime_sha256 (digest of venv tree listing + base interpreter binary) so a runtime change is never served from cache. Residual (documented, integrity-only, budget-bounded): venv base stdlib lives in dev-writable /opt/anaconda3; confidentiality unaffected because candidate code is dev-controlled by design. Raouf decision offered: fully root-owned base (python.org framework pkg).
- R15 (T12): broker reads broker_dir/SCAN_TARGETS.json (written by install.sh) instead of REPO_PATH/DEV_USER; SealPaths gains installed_tree_file, scan_targets_file, pinned_file, empty_mcp_file properties. Add `scripts/eval_seal/refresh_acl.sh` (operator, render-tested) to re-grant read ACL on new transcript dirs; scan reports SCAN_INCOMPLETE until refreshed (fail closed).
- R16 (T12): sudoers env: `Defaults>zurvaneval env_reset, !setenv, !env_keep, !env_check` (visudo-verified); validate_sudoers enforces exact two-rule shape.
- R17 (T12/T15 HIGH integration): render.WRAPPERS gains entry name; wrapper_text(module, entry) renders `from scripts.eval_seal.<m> import <entry> as _entry; sys.exit(_entry(sys.argv[1:]))`. Entries: broker->broker_main, candidate_run->main, author->author_main. Fix in assembly integration pass (Task 12 text updated).
- R18 (T15 HIGH, T7 backup channel): authoring audit files and claude session state contain PLAINTEXT items. New SealPaths.private_dir = eval_home/private (0700, TM-excluded like keys/); authoring audit_dir = private_dir/authoring; claude_runner sets CLAUDE_CONFIG_DIR=private_dir/claude (check it's not reserved by clean_env extra); setup.sh adds `tmutil addexclusion -p` for private_dir; seal-check adds check id tm_excluded_private. runs/ holds only opaque ids+ranks (R7) so it may be backed up.
- R19 (T15): accept not_found re-solve inversion (blind resolver must return no pages); accept per-category reject COUNTS in manifest; accept renames author_*; signature extensions accepted as built.
- R20 (T16 HIGH contract defect, writer F): a file cannot name the commit that contains it. Spec §5.4 says the predeclaration is "committed at <sha>" — the binding IS the commit: the broker reads <declare path> from <sha> itself. Drop candidate_commit from the predeclaration schema (Task 2 spec.json + validate_declaration T16 + broker step 8). Keep suite_id in the file. A changed declaration needs a new commit => new run key => new budget charge, so no post-hoc redeclaration channel.
- R21 (T3 MED): fix output.validate_broker_json at source: refusal = document with a `code` key; a seal-check document with ok:false is a valid seal-check, not a refusal. Remove client _client_validate workaround in integration.
- R22 (T16 MED): promotions.jsonl is informational, NOT the trust anchor. The sealed ledger (completed holdout run with verdict PASS) is authoritative; broker step 9 consults only the ledger. Documented; no signing added.
- R23: CLAUDE.md/AGENTS.md "24 gates" -> 25 updated in Task 18 docs.

## Remaining before the plan can be accepted

1. Tasks 10 and 11 finish and merge.
2. Integration pass: R6, R7, R13–R15, R17, R18, R20, R21.
3. Assemble the single plan file, with matrices (requirement → task, §9 gate → test).
4. Run `tests/test_plan_integrity.py`.
5. Fresh-worktree replay of the assembled plan, task by task.
6. Full suite.
7. Adversarial review (fail-open, TOCTOU, privilege confusion, leaks, scope).
8. Revise until no unresolved blocker remains.

Premises a worktree cannot show without root are listed in the plan and verified by the operator steps:
- identity creation;
- the `tmutil` exclusion;
- pf user-rule effectiveness;
- TCC/ACL traversal;
- sudo digest refusal;
- `claude` login as the eval identity.
