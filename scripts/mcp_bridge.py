#!/usr/bin/env python3
"""One write or eval tool call from the Rust MCP server, in its own process.

    stdin : {"tool": "<name>", "args": {...}}           (one JSON object)
    stdout: {"ok": true, "text": "...", "revision": N}  (exactly one JSON line)

The Rust server (rust/zurvan-mcp) serves reads natively and runs these here so
the knowledge ledger's operation boundary stays exactly where it is (design
2026-10-19 §6): each call goes to the SAME `scripts.mcp_tools.tool_zurvan_*`
function the Python server calls, whose `@mcp_operation` opens the operation
with writer="mcp". This script opens NO operation of its own — an outer one
would be joined by the inner one and silently change the ledger's attribution.

Everything the tools print (memory.py prints "Created <path>") goes to stderr,
because stdout is the reply channel. Eval tools evaluate the PYTHON reference
retriever, and their text says so.
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WRITES = {
    "remember": "tool_zurvan_remember",
    "decision_add": "tool_zurvan_decision_add",
    "decision_set_project": "tool_zurvan_decision_set_project",
    "decision_supersede": "tool_zurvan_decision_supersede",
    "claim_add": "tool_zurvan_claim_add",
    "question_add": "tool_zurvan_question_add",
    "claim_supersede": "tool_zurvan_claim_supersede",
    "claim_retract": "tool_zurvan_claim_retract",
}
#: Z2 claim reads served by Python for both servers (no write, no eval prefix).
CLAIM_READS = {
    "claim_show": "tool_zurvan_claim_show",
    "claim_history": "tool_zurvan_claim_history",
}
EVALS = {
    "eval_search": "tool_zurvan_eval_search",
    "validate_gold": "tool_zurvan_validate_gold",
}
EVAL_PREFIX = "Evaluates the Python reference retriever, not this server.\n"


def main() -> int:
    real_stdout = sys.stdout

    def reply(**fields) -> int:
        real_stdout.write(json.dumps(fields) + "\n")
        real_stdout.flush()
        return 0

    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        return reply(ok=False, error=f"bad request: {exc}")
    tool, args = request.get("tool"), request.get("args") or {}
    name = WRITES.get(tool) or EVALS.get(tool) or CLAIM_READS.get(tool)
    if name is None:
        return reply(ok=False, error=f"unknown bridge tool {tool!r}")

    with contextlib.redirect_stdout(sys.stderr):
        try:
            import scripts.mcp_tools as tools

            text = getattr(tools, name)(**args)
        except Exception as exc:  # the Python server would surface this as a tool error
            return reply(ok=False, error=f"{type(exc).__name__}: {exc}")
        # The revision THIS call minted (None when it wrote nothing), not
        # `kr.current()`, which can already include another session's write.
        revision = getattr(tools, "LAST_MINTED_REVISION", None) if tool in WRITES else None
    if tool in EVALS:
        text = EVAL_PREFIX + text
    return reply(ok=True, text=text, revision=revision)


if __name__ == "__main__":
    sys.exit(main())
