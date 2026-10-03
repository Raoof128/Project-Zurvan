# Z1b Rev 3 — design (supersedes the Rev 2 plan's blocked parts)

**Date:** 2026-10-02
**Status:** DESIGN, approved in part. Isolation architecture decided by Raouf (VM per run). The other
nine defect fixes below are the design; they need no further decision except where flagged. Rev 2
(`docs/superpowers/plans/2026-10-02-z1b-sealed-eval.md`) is **BLOCKED** and must not be executed.
**Why Rev 3:** an external review (verified against the files) found the Rev 2 plan was split-brain —
several HIGH rows claimed "FIXED" that the plan's executable blocks did not contain — and surfaced two
confidentiality P0s the gauntlet never modelled. The progress record
`docs/superpowers/progress/2026-10-02-z1b-plan-gauntlet.md` holds the adopt/reject split with receipts.

This design is written so Rev 3's plan rewrites the **executable blocks themselves** and then mechanically
replays the final bytes. No "FIXED (rN)" prose is ever added above a task again.

---

## 0. The rule that was broken, and the rule for Rev 3

Rev 2 accreted prose patches above frozen task blocks until the narrative and the code disagreed. Rev 3:

1. Every fix lands **in the executable block**, not in a note above it.
2. After the blocks are final, replay from a clean `main` worktree and mint a **content-addressed replay
   record** at the top of the plan:
   ```
   plan_sha256       = <sha256 of the plan file at replay time>
   baseline_commit   = <main SHA the replay branched from>
   replay_tree_sha   = <git write-tree of the replayed worktree>
   z1b_tests         = N passed, 1 live skip
   full_suite_delta  = identical to baseline / explained
   real_py39         = M passed (sealed side, CLT 3.9)
   live_gates_pending= 1,1b,4,5,8,9, VM-iso
   ```
   A test for the plan recomputes `plan_sha256`; if it differs from the recorded value the replay record is
   `STALE` and the plan fails its own integrity gate. This makes the split-brain impossible to repeat.

---

## 1. Candidate isolation — disposable per-run VM (P0 #1)

**Decided (Raouf, 2026-10-02):** adversarial HOLDOUT/RESERVE candidates run inside a disposable headless
ARM64 Linux VM, one VM **per run** (boot once, stream the queries, destroy), via a digest-pinned,
root-owned Lima/VZ for the MVP (a native Virtualization.framework runner is a later option).

```
developer ──closed broker API──▶ zurvaneval host broker
                                        │  one query at a time, private channel
                                        ▼
                         ┌───────────────────────────────┐
                         │ disposable ARM64 Linux guest   │
                         │  candidate code                │
                         │  frozen candidate runtime      │
                         │  frozen corpus snapshot (ro)   │
                         │  NO network, NO host mounts,    │
                         │  NO socket/clipboard/agent,     │
                         │  writable overlay destroyed     │
                         └───────────────────────────────┘
                                        │ strict RESULT frames
                                        ▼
                         zurvaneval grades internally → public projection only
```

- Guest↔host protocol is a tiny framed channel, nothing else:
  ```
  QUERY <qid> <len>\n<query bytes>
  RESULT <qid> <len>\n<strict result JSON>
  ```
- The frozen candidate bundle, runtime and corpus snapshot are copied into a fresh writable overlay before
  boot; the overlay is destroyed after the run.
- Lima/VZ binaries and the VM template/config are **root-owned and digest-pinned**, same invariant as the
  broker wrappers (spec §2 T2). `sandbox-exec` is dropped from the confidentiality argument (Apple marks it
  deprecated); any SBPL use is belt-and-braces only.
- This replaces Task 9's sandbox-exec candidate runner and most of Task 12's candidate-runtime install.

**New gate (VM-ISO), required before Z1b closes:** a malicious guest attempts host-process discovery,
argv leakage, host filesystem reads, loopback/external network, host IPC, persistence and files outside
its overlay. From the developer account, no raw query byte or canary is observable before, during or after
the run. Live gate (needs the installed VM); unit side exercises the protocol framing and the overlay
teardown against a fake VM launcher.

**G20 stays mandatory.** A VM removes the ps/argv channel; it does not remove intentional signalling
through the allowed result/timing fields. The coarse public projection (≤ ~56 bits/run, measured) is still
the bound on that channel.

---

## 2. Authoring identity split (P0 #2)

Sealed authoring must not run as the key holder over untrusted corpus text.

- New identity **`zurvanauthor`**: reads only the immutable corpus snapshot, the public rubric and prompts,
  and its own authoring workspace. It **cannot** read `keys/`, `sets/`, `runs/`, `incidents/`, or earlier
  authoring material.
- `claude -p` runs as `zurvanauthor`. Its `--allowedTools Read,Grep,Glob` are a convenience, not the
  boundary; the OS ACL is the boundary (a corpus prompt-injection that says "read /Users/zurvaneval/keys"
  hits EACCES).
- Encryption of the drafted sets and signing of the manifest happen **afterwards, in the trusted broker as
  `zurvaneval`**, reading the author's workspace output (schema-validated), never the reverse.
- `setup.sh` creates the third identity and its ACLs; `seal-check` proves `zurvanauthor` cannot read the
  secret dirs.

---

## 3. Genuinely implement in the executable blocks

These exist on branch `z1b-gauntlet` or in prose only; Rev 3 puts them in the plan's blocks:

- **G20 `output.public_projection`** + client refusal of any non-projection (done on branch, port verbatim).
- **G19 stdlib helper** resolving against the running interpreter's `sysconfig` (done on branch, port).
- **Decision 2 `candidate_run.py prepare <run_id>`**: the guest (now the VM) decodes/unbundles/fscks the
  bundle and reads the declaration, returning a closed-schema `{head: hex40, has_commit, declaration}`. The
  broker as `zurvaneval` parses only the bundle **header** (`git bundle list-heads`/`verify`) to cross-check
  `head`, and never decodes a pack. **Not yet implemented anywhere.**
- **Candidate runtime**: now the VM guest image carries the frozen runtime; `install.sh` builds/pins the VM
  template. The old "root-owned venv" gap (G9) is subsumed by the VM image.
- **`allowed_signers` creation (P1 #7)**: generated deterministically at install from the freshly generated
  signing public key, written root-/eval-owned, and checked in `seal-check` (owner, mode, principal,
  namespace, exact key bytes). No longer a circular precondition.

---

## 4. Evaluation-policy fixes

- **Declaration mandatory for HOLDOUT (P1 #8).** The broker refuses any `--suite holdout` without a valid
  committed declaration (`DECLARATION_REQUIRED`). This resolves the Z1-design-§5.4-vs-canonical-§9 conflict
  in favour of canonical: no undeclared sealed-holdout runs. Caching is tested with a declared baseline, or
  a separate quality-blind diagnostic command, never an undeclared holdout run. Task 19 drops the
  "run twice undeclared" step.
- **`declaration_sha256` in the run key, the signed result, and the promotion record (P1 #9).** A commit may
  hold two declarations; binding the declaration digest into the key stops one declaration's cached result
  being served for another.
- **Operator-pinned initial baseline (P1 #10).** The first baseline is not "whatever run declares
  `role: baseline`"; it is an operator-signed pin of the current ACTIVE production commit. The broker takes
  the baseline only from that pin (or a prior PROMOTED run), never from a dev-chosen self-declared one.
- **Resource-only promotion route (P1 #12).** Implement canonical §9.5's second limb: equivalent quality
  (within the non-inferiority margin) **and** a materially improved predeclared resource metric
  (latency/RSS) is a PASS, even with no quality improvement. The declaration states which limb it claims.

---

## 5. Corpus completeness and atomic generations

- **Full indexed corpus (P1 #11).** The export copies the same declared immutable BUILD_INPUTS that built
  the search generation (every indexed root — `wiki/` **and** `docs/` and root files, the 2,417 non-`wiki/`
  chunks R6 found), not a hand-maintained `wiki/` subset. `corpus_sha256` covers all of it; the grader's
  `targets` resolve against the same space the frozen index returns.
- **Atomic sealed generations (P1 #14).** Replace the in-place swap of holdout/reserve/sig/manifest with
  immutable generations and one atomic pointer:
  ```
  sealed/generations/<generation-id>/{holdout.age,reserve.age,manifest.json,manifest.json.sig,canaries.age}
  sealed/active -> <generation-id>
  ```
  A burn/reseal builds and fully validates a new generation, then flips `active` with one `os.rename`. A
  crash leaves the old generation intact and active.

---

## 6. Oracle / gate fixes

- **Scanner matches gate 9 (P1 #13).** Gate 9 counts a leak as a query **or a target page** or a canary.
  The scan must fingerprint target-page locators too, not only query text + canaries, so the oracle measures
  what the gate claims.
- **Signed promotion attestation (P1/P2 #16).** The promotion record carries a broker ed25519 signature
  over `{commit, declaration_sha256, suite_id, suite_manifest_sha256, broker_sha, run_id, verdict, timestamp}`,
  not just an append-only line with a broker SHA in it.
- **Closure matrix honesty (#17).** Gate 5 and gate 9 rows cite the VM-ISO live gate as their real oracle,
  and the unit tests are labelled as framing/teardown checks, not confidentiality proofs.

---

## 7. What survived Rev 2 and is kept

Clean-environment design; root-owned digest-pinned broker wrappers + sudo digest pinning; the NOT
CONFIGURED vs PASS distinction; the irreversible burn ledger; charging crashed reservations; test isolation
from the live store; the requirement→task and gate→oracle matrices (with the overclaiming rows corrected).
The canonical philosophy holds: measurement inputs frozen, raw holdout never a normal dev output, promotion
evidence-based.

---

## 7b. Rev 3 execution status (branch `z1b-rev3`, 2026-10-02 20:00)

Executed inline, test-first, each commit mutation-checked. Full z1b suite on the branch: **1701 passed,
1 skipped** (was 1692 at the start of Rev 3). Branch is NOT merged to `main`.

| # | Task | State | Commit |
|---|---|---|---|
| #8 | HOLDOUT requires a declaration (`DECLARATION_REQUIRED`); undeclared HOLDOUT abolished (canonical §9) | DONE | `0b84b43` |
| #9 | `declaration_sha256` bound into the run key (v3) — two declarations in one commit never share a cached result | DONE | `0b84b43` |
| #11 | corpus export carries every indexed root (docs/ + root .md), digest excludes volatile snapshot metadata | DONE | `b8b526f` |
| #2 (P0) | `zurvanauthor` identity split | DONE `c77504b` (draft/seal phase split) + `b41df1a` (OS identity, ACLs, read-denial live gate). WAS: — redesign of Task 15: authoring must split into a draft phase (as `zurvanauthor`, corpus-read only) and a seal/sign phase (as `zurvaneval`), or the new ACL breaks signing. Its own focused pass. |
| #1 (P0) | VM-per-run candidate isolation | **DEVELOPER CLOSURE COMPLETE, not yet CLOSED.** `vm_runner.py` (`15edd86`), VM-ISO live gate `c3ddb28` (real VZ boots), opt-in/serial `26f8cd1`, broker->VM wiring + no-host-path `1adeb18`, read-only corpus provisioning boundary `18612aa` (live, §7g). The three developer-level properties are PROVEN on real VZ: VM isolation (no host crossover), broker-only execution (no host path, fail-closed), read-only corpus provisioning (candidate reads, cannot mutate; digest bound to run identity). REMAINING (operator-install, §7f): golden retrieval image + the full installed-broker closure gate. **Do NOT stamp #1 CLOSED until (b) is green on real identities.** |
| #12 | resource-only promotion route (canonical §9.5 second limb) | DONE `713cba9` (z1b-rev3). Predeclaration gains `resource_improvement` (null disables; else {metric, min_delta}); broker_verdict threads the baseline result and PASSes on non-inferiority + budget + (quality improvement OR a material resource improvement vs baseline). Both declaration validators + output schema updated; 6 route tests; mutation-checked. |
| #13 | scanner covers target-page leaks (gate-9 oracle match) | DONE `59d79a6` (z1b-rev3). Definition settled by Raouf: **explicit association only**, two outcomes. CONFIRMED (burns) = a deterministic answer-key grammar binding a holdout item id to one of its target pages (structured record / `id->page` mapping / relation-word line, so `answer to <id> is wiki/x.md` IS a leak); SUSPECT (review-only, NEVER auto-burns) = ambiguous co-occurrence; CLEAN = bare path/id, manifest, far-apart, basename-only. New `target_leak` burn reason + `broker_record_review`. Full verdict table + e2e + benign-FP guard; mutation-checked both ways (over-detection reddens the FP guard). |
| #14 | atomic sealed generations | DONE `081a9c2`+`e9fc7b1` (z1b-rev3). The four reseal files live in `generations/<seq>/`; a single `active` symlink is promoted by ONE os.replace (was 4 separate replaces -> torn/wedged on crash). New `sealed_store.py` is the sole authority (grep-guarded); readers resolve `active`, writers build a generation then promote; gc keeps the live + previous healthy generation. Fault-injection gate (os.fork crash at every durable boundary) proves only VALID gen N or N+1 is ever observable, never hybrid/MANIFEST_INVALID/recovery-to-read; mutation-checked. |
| #7 | `allowed_signers` created deterministically at install | DONE `f4d2005`. render.py derives `keys/allowed_signers` from the generated `signing_ed25519.pub` on every install (idempotent, self-healing, atomic), principal+namespace imported from manifest.py; signer==verifier (eval). Tested end-to-end (real ssh-keygen -Y accept/reject) + mutation-checked. |
| #16 | broker-signed promotion attestation | DONE `932653b` (z1b-rev3). New `promotion.py`: a holdout PASS emits a broker-signed, closed-schema attestation (ssh-keygen `-Y`, DISTINCT namespace `zurvan-z1b-promotion`) as EXPORTABLE proof; the hash-chained eval-owned ledger stays the authoritative internal anchor (R22). The install anchor now lists both namespaces (#7). Tests: round-trip, tamper, wrong-key, domain separation, closed schema, broker e2e; mutation-checked. |

The three done tasks are the ones that were pure-logic and self-contained. The two P0s and the four
remaining P1s are each a multi-file rework; they are the next focused passes, not one-turn patches. The
plan's executable blocks and the content-addressed replay record are re-minted only after the branch work
is complete (build-order step 7).

## 7c. VM boot experiment findings (2026-10-02, Lima 2.2.0 + lima-additional-guestagents)

Running the real end-to-end boot (not another unit proof) surfaced build constraints the orchestration code must honour. Each is measured on this host (arm64, macOS 27.0.1):

1. **`LIMA_HOME` path length.** The VZ `ssh.sock` path must be < 104 chars (`UNIX_PATH_MAX`). A deep scratch path overflows and `start` aborts. Production `LIMA_HOME` must be a SHORT path (e.g. `/Users/zurvancandidate/.lima`), asserted at install.
2. **Guest agent is a separate package.** Lima 2.2 needs `lima-additional-guestagents` for the Linux aarch64 guest agent; without it `start` fails fast with `guest agent binary could not be found`. Install must pin these binaries too.
3. **Pinning a bare `limactl` copy breaks asset discovery (refines R3-D1).** The entitlement and signature survive a copy, but lima resolves guest agents and templates RELATIVE TO THE BINARY (`../share/lima`). A lone copied `limactl` in an unrelated dir cannot find the guest agent. **Pinning must copy the whole lima asset tree** (`bin/limactl` + `share/lima/{templates,_images,guestagents}`) preserving lima's expected layout, or set `LIMA_HOME`/asset env explicitly — not just the one binary. This is the corrected form of the pinning invariant for the VM runner.
4. **Default config mounts the host home.** `mounts: []` must be set explicitly (done in the experiment config); the guest must see no host filesystem.
5. **Invalid-Linux-username warning is cosmetic.** Lima maps the host user (`dev`, and in production `zurvancandidate`) to the guest user `lima`; no action.

These convert "the entitlement survives a copy" into the real constraint set for the VM runner's install and launch. The end-to-end boot + in-guest candidate + no-host-crossover + teardown is being verified next with the real asset tree.

## 7d. VM boundary PROVEN end-to-end (2026-10-02 20:09)

The real experiment (pinned-asset `limactl`, VZ, Alpine aarch64 guest) demonstrated the actual security boundary, not just a boot:

- **Boot:** `ze Running vz aarch64`, guest `Linux lima-ze 6.18.22-0-virt aarch64`, ~98 s cold (image pull + boot).
- **Candidate workload inside the guest, over the defined protocol:** `printf 'QUERY 1 hello-holdout' | limactl shell ze -- sh -c 'read ...; echo RESULT ...'` returned `RESULT 1 got:hello-holdout`. The host↔guest channel is `limactl shell` stdin/stdout.
- **No host crossover:** inside the guest `ls /Users` -> "No such file or directory"; `cat <repo>/CLAUDE.md` -> "can't open". With `mounts: []` the guest sees no host filesystem, so host-side sealed secrets are unreachable.
- **Teardown:** `limactl delete -f` removed the instance; `LIMA_HOME` left only `_config`.

**Config the pinned launcher must use** (all measured-necessary): `mounts: []` (no host fs), `containerd: {system: false, user: false}` (Alpine has no systemd; without this lima marks the VM DEGRADED and `start` exits non-zero though the guest is usable), a SHORT `LIMA_HOME` (ssh.sock < 104 chars), and the pinned lima asset tree (`bin/limactl` + `share/lima/{templates,_images,guestagents}`) so guest-agent discovery works.

This closes the architectural unknown. The orchestration code (`vm_runner.py`) is now built against a proven mechanism: create a per-run instance from the pinned config, stream QUERY/RESULT over `limactl shell`, tear down in a finally covering success/failure/timeout/kill, and the VM-ISO live gate replays this real sequence (skips NOT CONFIGURED without lima). The full torch/corpus candidate provisioning is a golden-image + read-only corpus-disk concern for the operator install, exercised by the live gate; the boundary itself is proven independent of the workload's size.

## 7e. VM runner build findings (2026-10-02, measured)

Building `vm_runner.py` against the proven mechanism surfaced two more measured constraints:

1. **The socket path is longer than `ssh.sock`.** Lima's real control socket is
   `<LIMA_HOME>/<name>/ssh.sock.<16 digits>`; the 16-digit suffix pushes a per-run `LIMA_HOME` nested under
   `scratch/<run_id>` over the 104-char limit (measured 111). Fix: ONE short, persistent `LIMA_HOME` at
   `candidate_home/lima` (which also caches the pinned image across runs), with a unique per-run instance
   name (`z<run_id>`) and delete-after. Budget under `/Users/zurvancandidate/lima`: ~70 chars, well under 104.
2. **Pinning really must copy the whole asset tree.** Confirmed by boot: a copy of `limactl` into
   `broker/bin` PLUS `share/lima` copied to `broker/share/lima` boots cleanly ("Guest agent is running"),
   because lima resolves guest agents at `<dir-of-limactl>/../share/lima`. The Linux-aarch64 guest agent is
   a real 7.4 MB file in the Cellar `share/lima`; `lima-additional-guestagents` must be installed first.

Per-run isolation is therefore the unique instance name + `limactl delete -f` after the run, not a
per-run home; the image download is cached once in the shared home. `vm_leftover_instances` lists the
shared home and reports any instance a teardown left behind (the sweep's VM arm, burns the run's items).
## 7f. #1 remaining: golden image + read-only corpus, and the broker-path closure gate (spec)

Broker->VM wiring is DONE (`1adeb18`): production `launch_candidate` is the ONLY candidate executor
(`broker_vm_launch`), the host `run` subcommand is refused so the privileged wrapper cannot execute
candidate code, a VM failure fails closed (never host execution), the sweep sees VM leftovers, and the run
identity binds the guest-image digest + VM config. Two things remain before #1 is stamped CLOSED, and both
are operator-install construction (they need `sudo setup.sh`/`install.sh`, so their gate is a live gate
that SKIPS NOT CONFIGURED in a dev session, like the seal live gate):

**(a) Golden guest image + read-only corpus (install.sh).** Build/pin, from reviewed inputs, a guest image
carrying the retrieval runtime (python + torch + sentence-transformers + sqlite-vec + the MiniLM model)
under `/opt/z1b/runtime`, with the candidate entry at `/opt/z1b/candidate/z1b_candidate.py`. The frozen
corpus is attached to each run as a READ-ONLY virtio disk (`additionalDisks`, readonly) mounted at
`/opt/z1b/corpus` — never a host-home mount, never a dynamic package fetch during evaluation. The image
digest + the corpus-disk digest are recorded in the run identity (the image digest already is). Properties
the closure gate must prove: the candidate can READ the corpus, the candidate CANNOT write the corpus (the
mount is read-only), and a second run starts from a clean guest (fresh instance; no state carries over).

**(a.1) BASE-IMAGE FINDING (2026-10-03).** The guest pinned for the isolation proof is Alpine aarch64 (musl); it only needed `sh`. The retrieval runtime (torch + sentence-transformers + sqlite-vec + MiniLM) has NO musl wheels, so the golden image CANNOT be built on the Alpine base. The golden image must use a glibc aarch64 base (Debian/Ubuntu cloud image): re-pin `vm_runner.GUEST_IMAGE_URL/DIGEST`, re-run the VM-ISO isolation gate against the new base, then provision the runtime under `/opt/z1b/runtime` + the candidate entry at `/opt/z1b/candidate/z1b_candidate.py`, freeze and digest-pin the image, and wire `install.sh` to place it at `broker_dir/images/guest.qcow2` (0444) with a digest check. This is the remaining operator-install BUILD; it is the gate on `#1 CLOSED`.

**(b) Full #1 closure gate, through the installed broker (`tests/test_z1b_seal_live.py`, operator step).**
Not a direct vm_runner call: `zurvan eval z1b run --commit <sha> --suite holdout --declare ...` driving the
INSTALLED broker, pinned Lima asset tree, golden image, real corpus provisioning. Assert success, failure,
timeout and kill all behave, and the post-run sweep leaves no instance. Only when this gate is green on the
real identities is #1 stamped CLOSED. Until then #1 is: boundary PROVEN, broker-wired, no host path,
read-only corpus provisioning PROVEN (§7g); the golden retrieval image + the installed-broker closure gate
pending the operator steps.

## 7g. Read-only corpus provisioning PROVEN (2026-10-02 21:15), with a mechanism refinement

The read-only-corpus property of §7f(a) is now proven on real VZ, independent of the golden image. Branch
`z1b-rev3` `18612aa`.

**Mechanism refinement (honest delta from §7f(a)).** §7f(a) specified the corpus as an `additionalDisks:
readonly` virtio disk. The live gate instead provisions a **squashfs** image, staged into the guest with
`limactl copy` (scp over lima's ssh — *not* a host mount) and loop-mounted `-o ro`. This is a **stronger**
guarantee than a `readonly` flag: squashfs has no write path in the kernel at all, so a compromised root
inside the guest cannot `mount -o remount,rw` its way back to a writable corpus. A `readonly` virtio disk
relies on the flag being honoured; squashfs is physically unwritable. The operator install may still attach
it as `additionalDisks: readonly` for convenience, but the gate asserts the squashfs physical guarantee.

**`vm_runner` additions (`18612aa`).** `corpus_image_sha256` / `verify_corpus_image` fail closed
(`CORPUS_MISMATCH`) unless the image is a squashfs (`hsqs` magic) whose whole-file sha256 equals the
declared identity — *immutability* and *correct identity* are separate requirements, so a genuine read-only
disk that is simply the WRONG disk is still refused. `vm_copy_into_guest` stages via `limactl copy`
(`CANDIDATE_FAILED` on failure). Constants `GUEST_CORPUS_SQUASHFS`, `GUEST_CORPUS_MOUNT`.

**Live gate (measured, `tests/test_z1b_vm_live.py::test_t1_vm_iso_gate_read_only_corpus_boundary`, opt-in +
serial).** On a clean host: candidate READS the known file (`known-corpus-content-v1`); write / create /
delete / rename ALL fail "Read-only file system" even under sudo; no host mount (`/Users`) visible; the host
corpus image is byte-for-byte unchanged after the run; a SECOND guest starts from the pristine corpus (no
state carried over); teardown leaves no instance. `verify_corpus_image` rejecting a wrong digest is asserted
too. **Mutation / positive control:** the SAME write-probe against a writable tmpfs mount SUCCEEDS (rc=0),
so the read-only assertions are load-bearing — a corpus wired to a writable backing WOULD turn the gate red.
Full live module **3 passed in 237 s**; corpus gate with the positive control **1 passed in 121 s**; 53
fake-runner unit tests + 5 broker-vm wiring tests; `-k z1b` 1593 passed / 2 skipped; py3.9 parse clean;
public_repo_guard + boundary scan-diff clean.

**Operational finding (recorded, not a defect).** An INTERRUPTED live run (the broker SIGKILLed before its
teardown finally, or the `LIMA_HOME` `rm -rf`'d out from under a live instance) orphans a `limactl
hostagent` VZ process that keeps a guest alive and contends for VZ resources, making later back-to-back
boots flake (`CANDIDATE_FAILED` on the 2nd/3rd boot). This was diagnosed to the orphan — a clean host runs
all gates green, and two guests back-to-back both boot and both `limactl copy` succeed. **Production is
fail-closed against this**: `vm_start` REFUSES a leftover instance in the stable `LIMA_HOME`
(`SANDBOX_VIOLATION`) rather than contend, so an orphaned prior run blocks the next run instead of
poisoning it. Re-running the gate on a dev host requires reaping orphans first
(`ps | grep ZurvanEval/broker/bin/limactl`); the install/operator runbook must note the same.

## 7h. STATE at the end of Rev 3 implementation (2026-10-03) and the operator-closure plan

**Record this state exactly — do not round the remaining operator proof up into a completed gate:**

> **Z1b Rev 3 implementation complete. P1 queue closed. #1 developer closure complete. Operator
> installation and installed-broker live closure remain. Branch intentionally unmerged.**

SHAs at this point: `main` = `c86bada` (docs/status only), `z1b-rev3` = `11d5847` (all Rev 3 code; tip advanced by two setup-robustness fixes — see §7i). Both
clean. The branch is **intentionally unmerged**: if the operator gate fails, the failure belongs to
`z1b-rev3` and `main` stays the known-good control.

**Developer-closed (proven on real VZ, branch `z1b-rev3`):** #1 VM isolation + broker-only execution +
read-only corpus provisioning; P1 #7 #12 #13 #14 #16. **NOT yet claimable:** `#1 CLOSED`, `Z1b CLOSED`,
and the merge — all require the installed environment.

**Next session = operator closure only (narrow; Raouf runs the privileged steps):**
1. Verify `main` and `z1b-rev3` are clean; record both SHAs.
2. Review the exact `setup.sh` / `install.sh` commands from the accepted plan at the Rev 3 tip.
3. Run the privileged setup/install from the **real repo**, never a gauntlet/worktree.
4. Build/install the pinned golden retrieval image.
5. Confirm the installed identities: Lima asset-tree identity, guest-image digest, generated signer /
   `allowed_signers`, sealed-store **active generation**, broker installation identity.
6. Run the full installed-broker live gate **through the public CLI surface** — `zurvan eval z1b run` and
   the installed broker — **NOT direct Python internals**. This is the load-bearing acceptance condition:
   entering through the internals could prove every component while leaving the INTEGRATION SEAM unproven.
7. Exercise success, candidate failure, timeout, kill, corpus read-only, a clean second run, and the
   post-run leftover sweep.
8. Only if **every** operator oracle passes: stamp **#1 CLOSED → Z1b CLOSED** (spec status line + the
   CLAUDE.md bullet, in a separate commit; never redefine a failed gate to get there).
9. Then merge `z1b-rev3` into `main`.
10. On the exact merged tree, re-run the full test suite, `public_repo_guard`, the Boundary gate, and
    `git diff --check`. No remote push as part of closure unless Raouf explicitly asks for that push.

## 7i. REAL install VERIFIED on the operator machine (2026-10-03), then rolled back to pristine

The privileged install was actually run on Raouf's machine (he supplied the password; done from the
real repo at the Rev 3 tip, not a worktree). Findings and result:

- **Two real operator-install defects, found by running it and fixed on `z1b-rev3`:** setup.sh runs
  under `bash -euo pipefail`, and (1) its dev-side ACL **chmods** and (2) its ACL-target **preflight**
  (`/bin/test -d`/`/bin/ls -d`) both hard-required OPTIONAL paths (`REPO/logs`, `~/.zurvan/reports`, an
  empty `*Zurvan*` glob). `REPO/logs` was absent, so `test -d .../logs` exited 1 and aborted the whole
  install SILENTLY before any identity was created. Fixed (`e3889d4` guard the chmods; `11d5847` the
  preflight now requires only the ancestor traversal paths). Traced via `bash -euo pipefail -x`.
- **After the fixes: `setup.sh` exit 0, `install.sh <sha> ...` exit 0** — identities created, age key +
  signer generated, the #7/#16 `allowed_signers` derived (both namespaces), sudoers + pf anchor +
  launchd scan daemon installed.
- **`zurvan eval z1b seal-check` through the PUBLIC CLI on the installed broker = 20+ invariants GREEN:**
  `broker_sha_matches`, `*_root_owned`, `broker_not_writable`, `candidate_cannot_read_sealed`,
  `dev_cannot_read_sealed`, `keys_private`, `sets_private`, `runs_private`, `stage_private`,
  `pinned_binaries_match`, `interpreter_root_owned`, `local_deny_rules_present`, `pf_anchor_present`,
  `sudoers_valid`, `scan_agent_present`, `tm_excluded_keys`, `tm_excluded_private` (#2 author ws),
  `ledger_valid`. `zurvan eval z1b status` round-trips (`broker_sha` = installed sha). The ONLY `false`
  are `corpus_read_only` and `manifest_valid` — i.e. exactly the operator steps still outstanding.
- **Rolled back to PRISTINE** (`uninstall --purge-sealed`): identities/groups/broker/sudoers/launchd
  removed, `/etc/pf.conf` anchor + backup gone. The machine is exactly as before; the install is proven
  reproducible from the scripts.

**Remaining for `#1 CLOSED` -> `Z1b CLOSED` (unchanged, all operator-side):** build the golden glibc
aarch64 guest image (§7f a.1) and place it at `broker_dir/images/guest.qcow2`; install a read-only
corpus (`install_corpus`); author + seal a suite (model spend, as `zurvanauthor`); then the full
installed-broker live gate through `zurvan eval z1b run` incl. a real VM candidate run.

## 7j. Golden-image feasibility PROVEN on Debian 12 aarch64 (2026-10-03)

The load-bearing unknown of the golden-image build (does the retrieval runtime actually run on a glibc
aarch64 guest?) is settled empirically. A Debian 12 (bookworm) aarch64 Lima guest (Python 3.11.2),
booted with `mounts: []` + containerd off, `pip install torch sentence-transformers sqlite-vec numpy`
succeeded (all have glibc-aarch64 wheels: torch 2.14.1 cp311/manylinux_2_28, st 6.1.0, sqlite-vec
0.1.9, numpy 2.4.6), and `SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2").encode(...)`
returned a 384-dim vector -> `SPIKE_OK torch=2.14.1 dim=384`. Guest torn down clean. So **Debian 12
aarch64 is the golden-image base** (Python 3.11 matches the cp311 wheels; glibc 2.36 satisfies
manylinux_2_28). The pinned Alpine base stays ONLY for the isolation proof.

**Remaining golden-image build (now de-risked, reproducible):** a build script that boots a pinned
Debian 12 aarch64 base, installs python3.11 + the stack (prefer the CPU-only torch wheel to shrink the
image), stages the candidate commit's `scripts/` at `/opt/z1b/candidate` + a runtime venv at
`/opt/z1b/runtime`, bakes the MiniLM model HF cache offline-ready, flattens the disk to a self-contained
`guest.qcow2` (`qemu-img convert`), and digests it -> `GUEST_IMAGE_DIGEST`; then wire `install.sh` to
place+verify `broker_dir/images/guest.qcow2`. Separately: the candidate needs `ZURVAN_SEARCH_DB` built
from the read-only corpus (the broker's candidate-launch path must build/provide the index in-guest).

## 7k. The REAL remaining critical path for #1 CLOSED: VM candidate-execution orchestration

Found by inspecting the launch path (not just the status): `broker_vm_launch` boots the guest and
returns a `limactl shell` Popen on `GUEST_CANDIDATE_CMD`, but it does NOT stage the corpus into the
guest, mount it, build a search index, or pass the candidate env. The read-only corpus pieces
(`vm_copy_into_guest`, `verify_corpus_image`, squashfs `-o ro`) exist and are PROVEN in the live gate,
but are NOT wired into the broker launch path. And `z1b_candidate.py` hard-exits (`sys.exit(2)`) unless
`ZURVAN_SEARCH_DB` already points at an index built over the corpus. The candidate env
(`ZURVAN_SEARCH_DB`/`ZURVAN_CORPUS`) is currently set ONLY in the legacy host runner `candidate_run.py`,
which #1 made unreachable. So a golden image alone cannot run a candidate end-to-end yet.

**Ordered remaining build for a runnable candidate (each part TDD, no sudo except the final install):**
1. **[DONE `70046e5`]** **Orchestration in the broker launch path** (code): `broker_vm_launch` now
   does vm_start(golden) -> `broker_provision_corpus` (verify corpus digest, stage via `limactl copy`,
   loop-mount `-o ro`) -> `vm_launch(boot=False, guest_env=...)`. KEY SIMPLIFICATION: the corpus export
   is a coherent snapshot that ALREADY contains `search.sqlite`, so there is NO in-guest index build -
   the candidate reads its frozen index off the read-only corpus mount; the sealed env is
   `ZURVAN_SEARCH_DB=<mount>/search.sqlite, ZURVAN_CORPUS=<mount>, HF_HUB_OFFLINE=1, HF_HOME, TMPDIR`
   forced with `env -i`. Unit-tested (fake runner); no-host-fallback kept. REMAINING sub-piece:
   `install_corpus` must produce `corpus.squashfs` + `corpus.squashfs.sha256` from the export.
1. ~~**Orchestration in the broker launch path** (code): stage the corpus squashfs into the guest
   (`vm_copy_into_guest` + `verify_corpus_image` against the run-identity digest), mount it read-only,
   run the candidate commit's index build over the mounted corpus to produce `search.sqlite` in the
   guest, and launch `z1b_candidate.py` with `ZURVAN_SEARCH_DB`/`ZURVAN_CORPUS`/`HF_HUB_OFFLINE=1`/
   `HF_HOME` in the guest env (via `limactl shell --env` or a pinned in-guest wrapper). Unit-test with
   the fake runner; keep the no-host-fallback invariant.
2. **Golden image** (feasibility proven, §7j): bake Debian 12 aarch64 + runtime venv + candidate tree +
   offline MiniLM cache, flatten to a self-contained `guest.qcow2`, digest-pin `GUEST_IMAGE_DIGEST`,
   wire `install.sh` to place+verify `broker_dir/images/guest.qcow2`.
3. **End-to-end live gate**: a real candidate run over a synthetic corpus returns graded rows through
   the protocol; success/failure/timeout/kill/corpus-RO/clean-second-run/sweep all hold.
4. **Installed-broker closure gate** via `zurvan eval z1b run` (the integration seam), then stamp
   `#1 CLOSED -> Z1b CLOSED` and merge.

Hardest unknowns are now retired: VM isolation (proven), read-only corpus (proven), runtime on glibc
aarch64 (proven §7j). What remains is this orchestration wiring + the image bake + the live gates.

## 7k.1. CORRECTION (2026-10-03): the read-only corpus image is built IN-GUEST, not on the host

Attempting to have `install_corpus` build the corpus squashfs on the operator Mac surfaced a design
flaw, caught before it was committed:
- macOS ships NO system tool that produces a Linux-mountable read-only filesystem (`mksquashfs` is
  brew-only; `hdiutil` makes Mac-only images). A brew binary also violates the standing invariant that
  no privileged plan command may name a user-owned / `/opt` binary (test
  `test_t12_no_command_line_names_a_user_owned_binary`), and bundling `mksquashfs` + its dylibs into
  the root-owned broker tree is fragile.
- Putting the image inside `corpus/` is also wrong independently: `corpus_sha256` walks every file in
  `corpus/` and must equal the sealed manifest, so a packaging artifact cannot live there.

Correct design: the host only STAGES the corpus CONTENT into the guest (the content is already
installed root-owned in `corpus/`; copy it in via `limactl copy`, e.g. a tar). The read-only image is
built INSIDE the Linux guest, whose golden image carries `squashfs-tools` (a clean `apt` package), then
loop-mounted `-o ro`. The digest bound to the run is `corpus_sha256` (content), already in the run key;
the broker verifies the staged content against the sealed manifest before building the image in-guest.
So `install_corpus` stays CONTENT-ONLY (no host squashfs), and the §7k-step-1 `broker_provision_corpus`
(committed `70046e5`, which stages a HOST `corpus.squashfs`) must be REWORKED to stage content + build
the image in-guest. The read-only-corpus MECHANISM itself (squashfs `-o ro`, physical unwritability,
digest identity) remains proven; only WHERE the image is built changes (guest, not host).

## 7l. Golden-image FLATTEN mechanic PROVEN (2026-10-03)

The second golden-image unknown (can a provisioned guest be frozen into a portable base image lima
boots fresh?) is now settled. Spike: booted Debian 12 aarch64 (vz), wrote a marker in-guest, stopped
it, flattened the instance disk and booted a NEW instance from the result:
- The vz instance disk is `<LIMA_HOME>/<inst>/disk` — a sparse RAW file (100 GiB virtual, ~1.1 GiB
  used for a fresh Debian), NOT a `diffdisk` overlay. `qemu-img convert -O qcow2 disk golden.qcow2`
  produces a compact standalone qcow2 (~1.2 GiB used).
- A fresh instance booted with `.images=[{location:"file://…/golden.qcow2", arch:"aarch64"}]` still
  had the marker (`GOLDEN-MARKER-v1`) — provisioned state persists through the flatten into a clean
  boot. (cloud-init re-runs harmlessly per new instance-id; `/opt` content persists.)

So the golden-image build is: boot Debian 12 aarch64 → provision in-guest (apt python3/venv +
`squashfs-tools`; `/opt/z1b/runtime` venv with torch/sentence-transformers/sqlite-vec/numpy; the
candidate commit's `scripts/` at `/opt/z1b/candidate`; the MiniLM cache at `/opt/z1b/runtime/hf`,
offline) → stop → `qemu-img convert -O qcow2 <inst>/disk guest.qcow2` → sha512-pin `GUEST_IMAGE_DIGEST`
→ `install.sh` places + verifies `broker_dir/images/guest.qcow2`. Both hard unknowns (runtime §7j,
flatten §7l) are retired; the bake is heavy (torch download) but mechanically de-risked.

## 8. Build order for the Rev 3 plan

1. Re-sync the ported branch fixes (G19, G20) into fresh executable blocks, from a clean `main`.
2. Identity split (`zurvanauthor`) — setup/ACL/seal-check.
3. VM isolation: pinned Lima/VZ install, the guest image build, the `prepare`+run protocol over the VM,
   the VM-ISO gate (unit + live).
4. Policy: mandatory-declaration, `declaration_sha256` in the key/result/record, pinned baseline,
   resource-only route.
5. Corpus completeness; atomic generations; scanner target coverage; signed promotion record.
6. `allowed_signers` deterministic creation + seal-check.
7. Mechanical replay from clean `main`; mint the content-addressed replay record; full-suite delta; one
   real-CLT-3.9 run.

Operator steps and the six live gates (plus VM-ISO) run only after Raouf executes setup/install/authoring.

---

## 9. Rev 3 design gauntlet (measured before acceptance)

- **VZ primitive exists here:** `Virtualization.framework` present, `arm64`, macOS 27.0.1. The chosen
  architecture is runnable on this host in principle.
- **Lima is NOT installed** (`limactl`/`lima`/`colima` all absent). Rev 3's VM task starts from a bare
  install, so the operator-step cost is real: install Lima, build and pin the guest image, boot once.
- **OPEN RISK R3-D1 (must settle empirically in Rev 3, before the VM task is accepted):** the invariant
  "sealed-side executables are root-owned, digest-pinned COPIES" may be incompatible with
  Virtualization.framework. VZ requires the `com.apple.security.virtualization` entitlement, which is bound
  to the binary's code signature; copying a notarized, entitled binary to `/Library/ZurvanEval/...` can
  invalidate the signature and make VZ refuse to launch. Resolution to test first: pin Lima/VZ by
  **installed path + digest of the original, signed, in-place binary** (verify the digest, run it where it
  was installed) rather than copying it into the broker tree — i.e. a different pinning shape from the
  Python wrappers. Until this is settled, the VM MVP is not buildable as specified; it is the first thing
  Rev 3's gauntlet must falsify in a throwaway worktree.
- **R3-D1 RESOLVED (measured 2026-10-02, Lima 2.2.0 installed):** the feared failure does NOT occur on this
  host. `limactl` carries `com.apple.security.virtualization`, and a plain byte COPY of it keeps both the
  entitlement (`codesign -d --entitlements`) and a valid signature (`codesign -v` exits 0). The entitlement
  is embedded in the Mach-O and `com.apple.security.virtualization` is not an Apple-only restricted
  entitlement, so a validly-signed copy may claim it. The "pin a root-owned COPY, digest-checked" invariant
  therefore works for the VM runner exactly as for the Python wrappers — no special installed-path pinning
  is needed. **Residual, still to close with a live boot:** that VZ actually accepts the copied binary when
  it boots a guest (the signature+entitlement surviving is necessary; a real `limactl start` of the pinned
  copy is the sufficient check, and is part of the VM-ISO live gate).
- **Consequence for the operator steps:** VM install + image build is added to the Raouf-only setup, and
  the VM-ISO live gate cannot be shown on a machine without Lima installed (it reports NOT CONFIGURED, like
  the other live gates).
