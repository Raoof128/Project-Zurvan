#!/usr/bin/env python3
"""A/B parity harness: drive two MCP servers over raw stdio JSON-RPC with the
same requests on the same store, and compare what an agent would see.

    python scripts/mcp_parity.py [--a CMD] [--b CMD] [--root DIR]
        [--only GROUP[,GROUP]] [--protocol 2025-11-25|2026-07-28]
        [--bench N] [--report PATH] [--show-diff]

A defaults to the Python server, B to the Rust release binary. Exit 0 iff no
case FAILs. Design: docs/superpowers/specs/2026-10-19-rust-mcp-server-design.md §9.

PRIVACY. The live store holds private pages, and the read-gate group reads
paths (sources/, .claude/, .remember/) the Python server still serves. Nothing
read from a server is ever printed or written: the console and the report carry
case ids, verdicts, sizes and diff-line COUNTS only. Case ids never contain a
query text (instrument items are named by index) or a private file name
(`sources#0`). `--show-diff` prints diffs to the terminal for local debugging
and is never used by tests.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import queue
import re
import shlex
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
DEFAULT_A = f"{sys.executable} {REPO / 'scripts' / 'mcp_server.py'}"
DEFAULT_B = str(REPO / "rust" / "zurvan-mcp" / "target" / "release" / "zurvan-mcp")
GROUPS = ("listing", "status", "read_page", "read_gate", "graph", "search_hybrid",
          "search_keyword", "context", "writes", "eval")

# Lines a server adds that the other does not, stripped before comparison
# (design §7.8 stamp, §7A status lines, §7.9 write revision).
STRIP_PREFIXES = ("[index rev ", "Serving: ", "Model: ", "Vector cache: ", "Ledger: ", "Ledger revision: ")
_RELATION_SUFFIX = re.compile(r"( - (?:incoming|outgoing):\S+| \((?:incoming|outgoing):[^)]*\))$")


def is_error_text(s: str) -> bool:
    return s.startswith("Error") or s.startswith("Failed to")


def normalise(group: str, text: str) -> str:
    lines = [l for l in text.split("\n") if not l.startswith(STRIP_PREFIXES)]
    if group == "graph_expand":
        return "\n".join(sorted(_RELATION_SUFFIX.sub("", l) for l in lines))
    if group == "context":
        out, in_graph, graph = [], False, []
        for l in lines:
            if l.startswith("## Graph-Related Context"):
                in_graph = True
            (graph if in_graph else out).append(_RELATION_SUFFIX.sub("", l) if in_graph else l)
        return "\n".join(out + sorted(graph))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# A raw stdio JSON-RPC peer: exact control of the protocol version, identical
# treatment of both servers, nothing between us and the wire.
# ---------------------------------------------------------------------------

def meta_2026() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "zurvan-parity", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


class Peer:
    def __init__(self, cmd: str, env: dict, protocol: str):
        self.protocol = protocol
        self.proc = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, cwd=str(REPO), text=True, bufsize=1)
        self.lines: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0
        self.initialize = self._start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put("")

    def _send(self, msg: dict):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 900.0) -> dict:
        self.next_id += 1
        rid = self.next_id
        params = dict(params or {})
        if self.protocol == "2026-07-28":
            params["_meta"] = {**meta_2026(), **params.get("_meta", {})}
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            line = self.lines.get(timeout=max(0.1, deadline - time.monotonic()))
            if line == "":
                raise RuntimeError(f"server exited during {method}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                raise RuntimeError("server wrote a non-JSON line to stdout (protocol corruption)")
            if msg.get("id") == rid:
                return msg

    def _start(self) -> dict:
        if self.protocol == "2026-07-28":
            return self.request("server/discover")
        r = self.request("initialize", {"protocolVersion": self.protocol, "capabilities": {},
                                        "clientInfo": {"name": "zurvan-parity", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@dataclass
class Case:
    case_id: str
    group: str
    method: str
    params: dict = field(default_factory=dict)
    # How to compare: the normalise() group and an optional divergence rule
    # (a, b) -> bool that marks an EXPECTED_DIVERGENCE instead of a FAIL.
    norm: str = ""
    expected_divergence: Callable[[str, str], bool] | None = None
    bench_key: str = ""


def tool(case_id: str, group: str, name: str, args: dict, **kw) -> Case:
    return Case(case_id, group, "tools/call", {"name": name, "arguments": args}, bench_key=name, **kw)


def unsafe_path_error(path: str) -> str:
    return f"Error: Path {path} failed safety checks (no absolute paths, no '../', raw/ blocked)."


def listing_cases(root: Path) -> list[Case]:
    cases = [Case("listing:initialize.instructions", "listing", "<initialize>"),
             Case("listing:tools/list", "listing", "tools/list"),
             Case("listing:resources/list", "listing", "resources/list"),
             Case("listing:resources/templates/list", "listing", "resources/templates/list"),
             Case("listing:prompts/list", "listing", "prompts/list")]
    for p in ("zurvan_project_brief", "zurvan_pre_edit_context", "zurvan_post_edit_memory", "zurvan_research_audit"):
        cases.append(Case(f"listing:prompt:{p}", "listing", "prompts/get", {"name": p}))
    return cases


def read_page_cases(root: Path) -> list[Case]:
    cases = []
    for path in ("wiki/index.md", "README.md", "AGENTS.md", "docs/API.md", "eval/README.md"):
        cases.append(tool(f"read_page:{path}@0", "read_page", "zurvan_read_page", {"path": path}))
    for off, lim in ((24000, 24000), (1, 7), (10**9, 10)):
        cases.append(tool(f"read_page:wiki/index.md@{off}", "read_page", "zurvan_read_page",
                          {"path": "wiki/index.md", "offset": off, "limit": lim}))
    for path in ("wiki/missing-parity-zz.md", "/etc/passwd", "../outside.md", "raw/x.md", "Raw/x.md"):
        cases.append(tool(f"read_page:{path}", "read_page", "zurvan_read_page", {"path": path}))
    for uri in ("zurvan://wiki/index", "zurvan://wiki/log", "zurvan://wiki/overview",
                "zurvan://wiki/open-questions", "zurvan://eval/baseline", "zurvan://file/README.md",
                "zurvan://file/wiki/index.md"):
        cases.append(Case(f"read_page:resource:{uri}", "read_page", "resources/read", {"uri": uri}))
    return cases


def read_gate_cases(root: Path) -> list[Case]:
    """Paths the 2026-10-19 probe read through the Python server. B must refuse;
    A serving them is the known gap (IOU-5), reported, never passed."""
    def gate(a: str, b: str, path: str) -> bool:
        return b == unsafe_path_error(path) and not a.startswith("Error: Path")

    named = [".git/HEAD", "wiki/../.git/config", ".claude/settings.json", ".remember/now.md",
             "data/registry.sqlite", "scripts/cli.py"]
    cases = [tool(f"read_gate:{p}", "read_gate", "zurvan_read_page", {"path": p},
                  expected_divergence=lambda a, b, p=p: gate(a, b, p)) for p in named]
    for label, sub in (("sources", "sources"), ("wiki/traces", "wiki/traces")):
        d = root / sub
        first = next((f for f in sorted(d.glob("*.md"))), None) if d.is_dir() else None
        if first is not None:
            rel = f"{sub}/{first.name}"
            cases.append(tool(f"read_gate:{label}#0", "read_gate", "zurvan_read_page", {"path": rel},
                              expected_divergence=lambda a, b, p=rel: gate(a, b, p)))
    return cases


def status_cases(root: Path) -> list[Case]:
    return [tool("status:zurvan_status", "status", "zurvan_status", {}),
            tool("status:zurvan_graph_stats", "status", "zurvan_graph_stats", {}),
            Case("status:resource:zurvan://graph/stats", "status", "resources/read", {"uri": "zurvan://graph/stats"})]


CASE_BUILDERS: dict[str, Callable[[Path], list[Case]]] = {
    "listing": listing_cases,
    "status": status_cases,
    "read_page": read_page_cases,
    "read_gate": read_gate_cases,
}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def observe(peer: Peer, case: Case) -> tuple[str, Any, float]:
    """(comparable text, B-side isError or None, seconds)."""
    t0 = time.perf_counter()
    if case.method == "<initialize>":
        res = peer.initialize.get("result", {})
        return json.dumps(res.get("instructions")), None, 0.0
    msg = peer.request(case.method, case.params)
    dt = time.perf_counter() - t0
    if "error" in msg:
        return "JSONRPC_ERROR " + str(msg["error"].get("message")), None, dt
    res = msg["result"]
    if case.method == "tools/call":
        text = "\n".join(c.get("text", "") for c in res.get("content", []))
        return text, res.get("isError"), dt
    if case.method == "resources/read":
        return "\n".join(c.get("text", "") for c in res.get("contents", [])), None, dt
    if case.method == "prompts/get":
        return json.dumps({"description": res.get("description"), "messages": res.get("messages")}, sort_keys=True), None, dt
    key = {"tools/list": "tools", "resources/list": "resources",
           "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}[case.method]
    return json.dumps(res.get(key), sort_keys=True), None, dt


def compare(case: Case, a: str, b: str, b_flag) -> tuple[str, int]:
    na, nb = normalise(case.norm or case.group, a), normalise(case.norm or case.group, b)
    diff = sum(1 for l in difflib.unified_diff(na.split("\n"), nb.split("\n"), lineterm="", n=0)
               if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
    if case.method == "tools/call" and b_flag is not None and bool(b_flag) != is_error_text(b):
        return "FAIL", max(diff, 1)  # A5: B must flag exactly its error texts
    if na == nb:
        return "PASS", 0
    if case.expected_divergence and case.expected_divergence(a, b):
        return "EXPECTED_DIVERGENCE", diff
    return "FAIL", diff


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--a", default=DEFAULT_A)
    ap.add_argument("--b", default=DEFAULT_B)
    ap.add_argument("--root", help="ZURVAN_ROOT for both servers (read-only)")
    ap.add_argument("--only", default=",".join(CASE_BUILDERS))
    ap.add_argument("--protocol", default="2025-11-25", choices=("2025-11-25", "2026-07-28"))
    ap.add_argument("--bench", type=int, default=0, help="repeat each case N times and report p50/p95")
    ap.add_argument("--report", help="JSON report path (default ~/.zurvan/parity/<ts>.json)")
    ap.add_argument("--show-diff", action="store_true", help="print diffs (LOCAL debugging only; may show content)")
    args = ap.parse_args(argv)

    groups = [g.strip() for g in args.only.split(",") if g.strip()]
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        ap.error(f"unknown group(s) {unknown}; known: {', '.join(GROUPS)}")
    root = Path(args.root).resolve() if args.root else REPO

    env = os.environ.copy()
    env.update({"PYTHONPATH": str(REPO), "ZURVAN_MCP_READONLY": "1", "ZURVAN_MCP_ALLOW_RAW_READ": "0"})
    env.setdefault("ZURVAN_EMBED_PROVIDER", "sentence_transformers")
    env.setdefault("ZURVAN_EMBED_MODEL", "all-MiniLM-L6-v2")
    if args.root:
        env["ZURVAN_ROOT"] = str(root)

    cases: list[Case] = []
    for g in groups:
        builder = CASE_BUILDERS.get(g)
        if builder is None:
            print(f"SKIP group {g}: no cases in this build")
            continue
        cases += builder(root)

    a, b = Peer(args.a, env, args.protocol), Peer(args.b, env, args.protocol)
    rows, times = [], {}
    try:
        for case in cases:
            ta, a_flag, da = observe(a, case)
            tb, b_flag, db = observe(b, case)
            verdict, diff = compare(case, ta, tb, b_flag)
            rows.append({"case_id": case.case_id, "group": case.group, "verdict": verdict,
                         "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
            print(f"{verdict:<20} {case.case_id}  (a {len(ta)} ch, b {len(tb)} ch, diff {diff})")
            if args.show_diff and verdict != "PASS":
                for l in difflib.unified_diff(normalise(case.norm or case.group, ta).split("\n"),
                                              normalise(case.norm or case.group, tb).split("\n"),
                                              "A", "B", lineterm="", n=1):
                    print("    " + l)
            if args.bench and case.method != "<initialize>":
                key = case.bench_key or case.method
                for _ in range(args.bench):
                    times.setdefault((key, "a"), []).append(observe(a, case)[2])
                    times.setdefault((key, "b"), []).append(observe(b, case)[2])
    finally:
        a.close()
        b.close()

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("PASS", "EXPECTED_DIVERGENCE", "FAIL")}
    print(f"\n{len(rows)} cases: {counts['PASS']} PASS, {counts['EXPECTED_DIVERGENCE']} EXPECTED_DIVERGENCE, "
          f"{counts['FAIL']} FAIL  (protocol {args.protocol})")
    if times:
        print("\nlatency ms (p50 / p95), A vs B:")
        for key in sorted({k for k, _ in times}):
            def pct(v, p):
                v = sorted(v)
                return v[max(0, min(len(v) - 1, int(round(p / 100 * len(v) + 0.5)) - 1))] * 1000
            ta, tb = times[(key, "a")], times[(key, "b")]
            print(f"  {key:<28} A {pct(ta, 50):9.1f} / {pct(ta, 95):9.1f}   B {pct(tb, 50):9.1f} / {pct(tb, 95):9.1f}")

    report = Path(args.report) if args.report else Path.home() / ".zurvan" / "parity" / f"{time.strftime('%Y%m%dT%H%M%S')}.json"
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report.write_text(json.dumps(rows, indent=1))
    os.chmod(report, 0o600)
    print(f"report: {report}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
