"""Refusal codes for the sealed evaluation broker (spec s4-s9; contract s1).

SealRefusal is the only exception allowed to cross the broker boundary. It carries a
code and never free text, so a refusal cannot be used as a side channel for sealed data.
"""
from __future__ import annotations

SPEC_CODES = (
    "SEAL_NOT_CONFIGURED", "BROKER_STALE", "MANIFEST_INVALID", "CORPUS_MISMATCH",
    "CANDIDATE_NOT_FROZEN", "SUITE_RENEWAL_REQUIRED", "INTEGRITY_INCIDENT", "PROTECTED_TRACE",
)
# Fail-closed additions (each flagged in the plan for Raouf's ack; none weakens a spec code):
ADDED_CODES = (
    "BAD_ARGS",            # argv outside the fixed grammar
    "BUNDLE_INVALID",      # drop-dir file fails name/owner/nlink/symlink/size/verify checks
    "LEDGER_INVALID",      # hash chain broken, malformed line, unknown schema
    "RESERVE_NOT_ELIGIBLE",  # reserve run without --final-acceptance or without a prior HOLDOUT pass
    "DECLARATION_INVALID",  # --declare path/pattern/schema bad
    "SCAN_INCOMPLETE",     # a leak-scan target unreadable (TCC/ACL) -> run refused
    "CANDIDATE_FAILED",    # candidate crashed/timed out/protocol violation (run charged to budget)
    "SANDBOX_VIOLATION",   # post-run sweep found candidate-owned files outside scratch or live processes
    "SEAL_COMPROMISED",    # seal-check found a writable/foreign-owned broker file, missing TM exclusion, etc.
    "INTERNAL",            # any unexpected exception; detail goes ONLY to the sealed errors log
)
ALL_CODES = SPEC_CODES + ADDED_CODES


class SealRefusal(Exception):
    """The only exception that crosses the broker boundary. Carries a code, never text."""

    def __init__(self, code):
        # type(...) is str: a str subclass with a rigged __eq__/__str__ must not pass as a code.
        if type(code) is not str or code not in ALL_CODES:
            code = "INTERNAL"
        super().__init__(code)
        self.code = code
