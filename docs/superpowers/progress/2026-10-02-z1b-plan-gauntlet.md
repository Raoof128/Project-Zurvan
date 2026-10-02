# Z1b plan + dry-run gauntlet — progress record (2026-10-02)

**Status:** the plan is **written and gauntleted, awaiting Raouf's acceptance**. It is at
`docs/superpowers/plans/2026-10-02-z1b-sealed-eval.md`: 19 tasks and 23.8k lines.
- No production Z1b code is on `main`.
- The dry-run code lives only on local throwaway branches (`z1b-gauntlet`, `z1b-g-*`, under `.worktrees/`).
- Nothing was pushed from those branches.
- No `sudo`, no identity creation, no broker install, no DEV or sealed authoring, and no model spend.

## What the gauntlet established

- **Writers:** seven Sonnet writers, each in its own worktree. Each wrote the real code test-first (red, then green), mutation-checked every security guard, checked Python 3.9 compatibility for the sealed package, and emitted task text byte-identical to the code it ran.
- **Integration:** an inline pass (Task 18) fixed the cross-task defects. All 9 of its new guards are mutation-checked red; one of them survived at first and a test was added to kill it.
- **Replay:** a mechanical replay applied every code block of the assembled plan, in task order, to a fresh worktree from `main`: 151 blocks by engine, 9 prose-anchored edits by hand.
  - It gives 1671 passed and 1 skipped on the Z1b set. The skip is the live seal reporting NOT CONFIGURED.
  - Its code is unit-for-unit identical to the gauntlet branch; only comments, blank lines and function order differ.
- **Full suite**, replay tree vs a clean `main` worktree:
  - The same 10 failures in both, all present on `main`.
  - The same class of 6 teardown errors in both: the worktree live-store guard.
  - The 3 tests that differed pass serially in both trees, so they are load flakes.
  - The replay introduces no regression.
- **Checks:** `tests/test_plan_integrity.py` passes on the plan, and the Boundary scan of the plan is clean.

## Found on `main` while doing this (not caused by Z1b, not fixed: out of scope)

`tests/test_check_gate_index_rebuild.py` fails 7 of its 10 tests on the current `main` checkout, with
`TypeError: _stub_gitignore.<locals>._run() got an unexpected keyword argument 'timeout'`.

Its test stub does not accept the `timeout` argument that `scripts/rebuild_index.py` now passes to
`subprocess.run` (last touched by `fe4fd1e`).

## Decisions waiting for Raouf

These are listed in the plan's "Decisions for Raouf" section; the plan takes the fail-closed default for each:
1. the added refusal codes;
2. untrusted git pack parsing as `zurvaneval` (recommended: move it to `zurvancandidate`);
3. the candidate runtime base;
4. the explicit baseline run;
5. `page|section` locators;
6. per-category reject counts;
7. incident acknowledgement by incident id.

## Discrepancies and rulings

The plan's Gauntlet section lists defects G1–G18 with severity and fix.

Discrepancies D1–D16 are in this file's history (commit `fcd81d6`). The orchestrator rulings, in full:

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
- R24 (T10 vs T16): R20 wins over writer E's parent-commit reading (spec-literal, simpler): the declaration is read from <commit> itself, has no candidate_commit field; broker step 8 and Task 16 align on R20. Each declaration filename unique per run is NOT required (a new commit is).
- R25 (T10 MED, decision for Raouf): no all-zero baseline. The first promotion needs an explicit baseline: a completed HOLDOUT run of the currently active commit, declared with `"role": "baseline"` (charged 1 budget). Until one exists, declared candidate runs report verdict FAIL with rule non_inferiority_* false. Alternative offered to Raouf: allow a one-time bootstrap promotion.
- R26 (T10 HIGH): render.EXPORT_PATHS exports eval/z1b/spec.json; installed broker loads spec from broker_lib/eval/z1b/spec.json; Task 12 fixtures gain the file. Task 15 seal_suite must emit canaries.age in the format scan.py reads. refresh_acl.sh added to Task 12 (render-tested).
- R27 (T10): accept scan_run(paths, ctx, ledger); reserve budget 1 per commit; ack-incident requires a tty; result ci = candidate mean CI (schema has no signed difference); verdict rules = paired-bootstrap lower-bound tests vs baseline.
