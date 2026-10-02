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
- **Consequence for the operator steps:** VM install + image build is added to the Raouf-only setup, and
  the VM-ISO live gate cannot be shown on a machine without Lima installed (it reports NOT CONFIGURED, like
  the other live gates).
