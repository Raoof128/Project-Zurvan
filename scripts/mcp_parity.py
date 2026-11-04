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
          "search_keyword", "context", "orient", "writes", "eval")

# Lines a server adds that the other does not, stripped before comparison
# (design §7.8 stamp, §7A status lines, §7.9 write revision).
# "[live files " and "[index unavailable ": the F6 stamp names what a keyword
# search or a missing-index call actually served instead of the index path.
STRIP_PREFIXES = ("[index rev ", "[live files ", "[index unavailable ", "Serving: ", "Model: ", "Vector cache: ", "Ledger: ", "Ledger revision: ")
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
    def __init__(self, cmd: str, env: dict, protocol: str, cwd: Path = REPO):
        self.protocol = protocol
        self.proc = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env, cwd=str(cwd), text=True, bufsize=1)
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


def graph_cases(root: Path) -> list[Case]:
    """The highest-degree nodes (the hub has thousands of edges), three pages
    each. Named by rank only: node paths can be private page names."""
    import sqlite3
    db = root / "data" / "graph.sqlite"
    nodes: list[str] = []
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT n.node_id, n.path, (SELECT count(*) FROM edges e WHERE e.from_node_id = n.node_id)"
                " + (SELECT count(*) FROM edges e WHERE e.to_node_id = n.node_id) AS deg"
                " FROM nodes n ORDER BY deg DESC, n.node_id LIMIT 9").fetchall()
            nodes = [path or node_id for node_id, path, _ in rows]
        finally:
            con.close()
    nodes.append("zurvan-parity-no-such-node")
    cases = []
    for rank, node in enumerate(nodes):
        for cursor in (0, 25, 50):
            cases.append(tool(f"graph#{rank}:neighbours@{cursor}", "graph", "zurvan_graph_neighbours",
                              {"path_or_node_id": node, "limit": 25, "cursor": cursor}))
            for depth in (1, 2):
                cases.append(tool(f"graph#{rank}:expand{depth}@{cursor}", "graph", "zurvan_graph_expand",
                                  {"path_or_node_id": node, "depth": depth, "limit": 25, "cursor": cursor},
                                  norm="graph_expand"))
    return cases


def _instrument() -> list[dict]:
    """The RETIRED 50-item instrument: it may MEASURE, never gate a promotion.
    Read from ~/.zurvan/instruments only; items are named by index."""
    from scripts.instruments import gold_path
    return [json.loads(l) for l in gold_path().read_text(encoding="utf-8").splitlines() if l.strip()]


def _adhoc() -> list[str]:
    return json.loads((REPO / "rust" / "zurvan-mcp" / "fixtures" / "adhoc_queries.json").read_text(encoding="utf-8"))


def search_cases(hybrid: bool) -> Callable[[Path], list[Case]]:
    group = "search_hybrid" if hybrid else "search_keyword"

    def build(root: Path) -> list[Case]:
        cases = [tool(f"{group}:instrument#{i}", group, "zurvan_search",
                      {"query": item["query"], "hybrid": hybrid, "limit": 10})
                 for i, item in enumerate(_instrument())]
        cases += [tool(f"{group}:adhoc#{i}", group, "zurvan_search", {"query": q, "hybrid": hybrid, "limit": 10})
                  for i, q in enumerate(_adhoc())]
        return cases
    return build


def context_cases(root: Path) -> list[Case]:
    topics = [(f"instrument#{i}", item["query"]) for i, item in enumerate(_instrument()[:5])]
    topics += [(f"adhoc#{i}", q) for i, q in enumerate(_adhoc()[:5])]
    cases = []
    for label, topic in topics:
        for graph in (True, False):
            cases.append(tool(f"context:{label}:graph={graph}", "context", "zurvan_context",
                              {"topic": topic, "hybrid": True, "graph": graph, "limit": 10}))
    return cases


def orient_cases(root: Path) -> list[Case]:
    topics = [(f"instrument#{i}", item["query"]) for i, item in enumerate(_instrument()[5:10], start=5)]
    topics += [(f"adhoc#{i}", q) for i, q in enumerate(_adhoc()[5:10], start=5)]
    return [tool(f"orient:{label}", "orient", "zurvan_orient", {"topic": t, "limit": 10}) for label, t in topics]


EVAL_PREFIX = "Evaluates the Python reference retriever, not this server.\n"


def eval_cases(root: Path) -> list[Case]:
    """B runs the same Python eval through its bridge and says so: B's text
    must be EXACTLY the prefix plus A's text."""
    same_plus_prefix = lambda a, b: b == EVAL_PREFIX + a  # noqa: E731
    return [tool("eval:eval_search@2", "eval", "zurvan_eval_search", {"hybrid": True, "min_top3": 0.0, "max_queries": 2},
                 expected_divergence=same_plus_prefix),
            tool("eval:validate_gold", "eval", "zurvan_validate_gold", {}, expected_divergence=same_plus_prefix)]


def _write_store() -> Path:
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="zurvan-parity-writes-"))
    for d in ("wiki/decisions", "wiki/claims", "wiki/notes", "data"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "decisions" / "evidence.md").write_text("the evidence line for the parity claim\n", encoding="utf-8")
    # Outside the read allow-list, plus a planted symlink to it at a fixed
    # resource (wiki/overview.md: writes append to log.md, never to this).
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "legal.md").write_text("PARITY SECRET LEGAL TEXT\n", encoding="utf-8")
    os.symlink(root / "sources" / "legal.md", root / "wiki" / "overview.md")
    return root


def _ledger_rows(store: Path) -> list[tuple]:
    import sqlite3
    db = store / "data" / "registry.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT state, writer, caller, implicit, store_project, completed_paths FROM mutations ORDER BY rowid").fetchall()
    finally:
        con.close()
    # Claim pages carry a fresh random id per write (claim-<hex>.md): mask it.
    name = lambda x: re.sub(r"^claim-[0-9a-f]+\.md$", "claim-<id>.md", Path(x).name)  # noqa: E731
    return [(s, w, c, i, p, sorted(name(x) for x in json.loads(cp or "[]"))) for s, w, c, i, p, cp in rows]


def run_writes(args, env: dict) -> list[dict]:
    """Writes against two FRESH throwaway stores (one per server), never the
    live one: same calls, then the same texts and the same ledger rows."""
    import shutil
    rows: list[dict] = []
    stores = {"a": _write_store(), "b": _write_store()}
    calls = [
        ("remember", "zurvan_remember", {"type": "note", "title": "Parity note", "body": "body", "tags": ["parity"]}),
        ("decision_add", "zurvan_decision_add", {"title": "Parity decision", "reason": "because", "status": "accepted",
                                                 "tags": ["parity"], "project": "zurvan"}),
        ("decision_add_bad_slug", "zurvan_decision_add", {"title": "Bad slug", "reason": "r", "status": "accepted",
                                                          "tags": [], "project": "ClientX"}),
        ("claim_add", "zurvan_claim_add", {"text": "a parity claim", "source": "wiki/decisions/evidence.md",
                                           "evidence": "the evidence line", "confidence": "high", "tags": ["parity"]}),
        ("claim_add_bad_evidence", "zurvan_claim_add", {"text": "c", "source": "wiki/decisions/evidence.md",
                                                        "evidence": "not in the file", "confidence": "low", "tags": []}),
        ("question_add", "zurvan_question_add", {"question": "Parity question?", "reason": "r", "tags": ["parity"]}),
        # Both servers hold a claim's source to the read allow-list.
        ("claim_add_source_refused", "zurvan_claim_add", {"text": "leak", "source": "sources/legal.md",
                                                          "evidence": "PARITY SECRET LEGAL TEXT", "confidence": "high",
                                                          "tags": []}),
    ]
    try:
        peers = {}
        for side, store in stores.items():
            e = {**env, "ZURVAN_ROOT": str(store), "ZURVAN_MCP_READONLY": "0"}
            peers[side] = Peer(args.a if side == "a" else args.b, e, args.protocol)
        for label, name, targs in calls:
            case = tool(f"writes:{label}", "writes", name, targs)
            ta, _, _ = observe(peers["a"], case)
            tb, b_flag, _ = observe(peers["b"], case)
            verdict, diff = compare(case, ta, tb, b_flag)
            if "SECRET" in ta or "SECRET" in tb:
                verdict = "FAIL"  # a refused source must never be quoted back
            rows.append({"case_id": case.case_id, "group": "writes", "verdict": verdict,
                         "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
        # A symlink planted at a fixed resource is refused by both readers.
        case = Case("writes:static_resource_symlink_refused", "writes", "resources/read", {"uri": "zurvan://wiki/overview"})
        ta, _, _ = observe(peers["a"], case)
        tb, b_flag, _ = observe(peers["b"], case)
        verdict, diff = compare(case, ta, tb, b_flag)
        if "SECRET" in ta or "SECRET" in tb or not tb.startswith("Error"):
            verdict = "FAIL"
        rows.append({"case_id": case.case_id, "group": "writes", "verdict": verdict,
                     "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
        # set_project on the page decision_add created (same slug in both stores).
        # Python resolves a RELATIVE page against the process cwd, not the store
        # (measured: "not a decision page under wiki/decisions"), so each side
        # gets its own absolute path and its store path is masked afterwards.
        page = sorted(p.name for p in (stores["a"] / "wiki" / "decisions").glob("*.md") if p.name != "evidence.md")[0]
        texts = {}
        for side in ("a", "b"):
            case = tool("writes:decision_set_project", "writes", "zurvan_decision_set_project",
                        {"pages": [str(stores[side] / "wiki" / "decisions" / page)], "project": "acme"})
            text, flag, _ = observe(peers[side], case)
            texts[side] = (text.replace(str(stores[side].resolve()), "<STORE>").replace(str(stores[side]), "<STORE>"), flag)
        (ta, _), (tb, b_flag) = texts["a"], texts["b"]
        verdict, diff = compare(case, ta, tb, b_flag)
        if ta.startswith("Error"):
            verdict = "FAIL"  # the success path is the one under test
        rows.append({"case_id": case.case_id, "group": "writes", "verdict": verdict,
                     "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
        for p in peers.values():
            p.close()
        la, lb = _ledger_rows(stores["a"]), _ledger_rows(stores["b"])
        if args.show_diff and la != lb:
            for ra, rb in zip(la, lb):
                print(f"    A {ra}\n    B {rb}")
        rows.append({"case_id": "writes:ledger_rows", "group": "writes",
                     "verdict": "PASS" if la == lb and la else "FAIL",
                     "a_chars": len(la), "b_chars": len(lb), "diff_lines": 0 if la == lb else max(len(la), len(lb))})
        # A decision with NO project= takes its subject from the server's
        # working directory (memory.resolve_project). Launched from a foreign
        # directory, both must stamp the same subject — a bridge that ran in
        # the code checkout would stamp the (public) zurvan project instead.
        import tempfile
        foreign = Path(tempfile.mkdtemp(prefix="zurvan-parity-foreign-cwd-"))
        fs = {"a": _write_store(), "b": _write_store()}
        stores.update({"foreign": foreign, "fs_a": fs["a"], "fs_b": fs["b"]})
        texts = {}
        for side in ("a", "b"):
            p = Peer(args.a if side == "a" else args.b, {**env, "ZURVAN_ROOT": str(fs[side]), "ZURVAN_MCP_READONLY": "0"},
                     args.protocol, cwd=foreign)
            case = tool("writes:decision_add_no_project_foreign_cwd", "writes", "zurvan_decision_add",
                        {"title": "Subject from cwd", "reason": "r", "status": "accepted", "tags": []})
            text, flag, _ = observe(p, case)
            # The "unknown" warning names the page inside each side's own store.
            texts[side] = (text.replace(str(fs[side].resolve()), "<STORE>").replace(str(fs[side]), "<STORE>"), flag)
            p.close()
        stamp = {}
        for side in ("a", "b"):
            page = next((fs[side] / "wiki" / "decisions").glob("subject-from-cwd*.md"), None)
            stamp[side] = next((l for l in page.read_text().splitlines() if l.startswith("project:")), None) if page else None
        (ta, _), (tb, b_flag) = texts["a"], texts["b"]
        verdict, diff = compare(case, ta, tb, b_flag)
        if stamp["a"] != stamp["b"] or stamp["a"] is None:
            verdict = "FAIL"
        rows.append({"case_id": case.case_id, "group": "writes", "verdict": verdict,
                     "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
        if args.show_diff and verdict != "PASS":
            print(f"    project stamps: A {stamp['a']!r}  B {stamp['b']!r}")

        # Read-only: both refuse with the same text and write nothing.
        ro = {"a": _write_store(), "b": _write_store()}
        stores.update({"ro_a": ro["a"], "ro_b": ro["b"]})
        for side in ("a", "b"):
            peers[side] = Peer(args.a if side == "a" else args.b, {**env, "ZURVAN_ROOT": str(ro[side]), "ZURVAN_MCP_READONLY": "1"}, args.protocol)
        case = tool("writes:readonly_refusal", "writes", "zurvan_question_add", {"question": "q?", "reason": "r", "tags": []})
        ta, _, _ = observe(peers["a"], case)
        tb, b_flag, _ = observe(peers["b"], case)
        verdict, diff = compare(case, ta, tb, b_flag)
        if _ledger_rows(ro["a"]) or _ledger_rows(ro["b"]):
            verdict = "FAIL"
        rows.append({"case_id": case.case_id, "group": "writes", "verdict": verdict,
                     "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
        for p in peers.values():
            p.close()
    finally:
        for s in stores.values():
            shutil.rmtree(s, ignore_errors=True)
    for r in rows:
        print(f"{r['verdict']:<20} {r['case_id']}  (a {r['a_chars']} ch, b {r['b_chars']} ch, diff {r['diff_lines']})")
    return rows


_RESULT_HEAD = re.compile(r"^(\d+)\. (.+) \(score ([^)]*)\)$")


def result_blocks(text: str) -> list[tuple[str, str, str]]:
    """[(path, displayed score, whole block without its rank number)]."""
    blocks: list[list[str]] = []
    for line in text.split("\n"):
        if _RESULT_HEAD.match(line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    out = []
    for b in blocks:
        m = _RESULT_HEAD.match(b[0])
        out.append((m.group(2), m.group(3), "\n".join([f"{m.group(2)} (score {m.group(3)})"] + b[1:])))
    return out


def near_tie_only(a: str, b: str) -> bool:
    """Same results, and every position where the order differs holds two
    results whose displayed scores are equal. The 1e-6 rule of design §9.1
    cannot be applied to 2-decimal text, so an equal DISPLAYED score is the
    (looser, stated) proxy."""
    ba, bb = result_blocks(a), result_blocks(b)
    if not ba or sorted(x[2] for x in ba) != sorted(x[2] for x in bb):
        return False
    return all(x[2] == y[2] or x[1] == y[1] for x, y in zip(ba, bb))


def retrieval_metrics(rows: list[tuple[int, str, str]]) -> dict:
    """top-1 / top-3 / MRR from each server's own result paths, with the
    eval's own matchers (scripts.metrics)."""
    from scripts.metrics import (calculate_accuracy, calculate_mean_reciprocal_rank,
                                 calculate_reciprocal_rank, calculate_top_k_hit)
    gold = _instrument()
    out = {}
    for side in ("a", "b"):
        top1 = top3 = 0
        rrs = []
        for index, ta, tb in rows:
            retrieved = [p for p, _, _ in result_blocks(ta if side == "a" else tb)]
            expected = gold[index]["expected_paths"]
            top1 += calculate_top_k_hit(expected, retrieved, 1)
            top3 += calculate_top_k_hit(expected, retrieved, 3)
            rrs.append(calculate_reciprocal_rank(expected, retrieved))
        n = len(rows)
        out[side] = {"queries": n, "top1": calculate_accuracy(top1, n), "top3": calculate_accuracy(top3, n),
                     "mrr": round(calculate_mean_reciprocal_rank(rrs), 6)}
    return out


CASE_BUILDERS: dict[str, Callable[[Path], list[Case]]] = {
    "listing": listing_cases,
    "status": status_cases,
    "graph": graph_cases,
    "search_hybrid": search_cases(True),
    "search_keyword": search_cases(False),
    "context": context_cases,
    "orient": orient_cases,
    "eval": eval_cases,
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
    if case.group == "search_hybrid" and near_tie_only(na, nb):
        return "NEAR_TIE", diff
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
    ap.add_argument("--bench", type=int, default=0, help="repeat each case N more times and report p50/p95")
    ap.add_argument("--bench-sides", default="a,b", help="which servers the --bench repeats time (a, b or a,b)")
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
    # The Rust server's write/eval bridge: this interpreter, this checkout's scripts.
    env.setdefault("ZURVAN_PYTHON", sys.executable)
    env.setdefault("ZURVAN_CODE_ROOT", str(REPO))
    if args.root:
        env["ZURVAN_ROOT"] = str(root)

    cases: list[Case] = []
    for g in groups:
        if g == "writes":
            continue  # its own throwaway stores, below
        builder = CASE_BUILDERS.get(g)
        if builder is None:
            print(f"SKIP group {g}: no cases in this build")
            continue
        cases += builder(root)

    rows, times = [], {}
    if "writes" in groups:
        rows += run_writes(args, env)
    a, b = Peer(args.a, env, args.protocol), Peer(args.b, env, args.protocol)
    metric_rows: dict[str, list] = {}
    try:
        for case in cases:
            ta, a_flag, da = observe(a, case)
            tb, b_flag, db = observe(b, case)
            verdict, diff = compare(case, ta, tb, b_flag)
            if case.group.startswith("search_") and ":instrument#" in case.case_id:
                index = int(case.case_id.rsplit("#", 1)[1])
                metric_rows.setdefault(case.group, []).append((index, normalise(case.group, ta), normalise(case.group, tb)))
            rows.append({"case_id": case.case_id, "group": case.group, "verdict": verdict,
                         "a_chars": len(ta), "b_chars": len(tb), "diff_lines": diff})
            if case.method != "<initialize>":
                # The parity pass itself is one latency sample per side.
                key = case.bench_key or case.method
                times.setdefault((key, "a"), []).append(da)
                times.setdefault((key, "b"), []).append(db)
            print(f"{verdict:<20} {case.case_id}  (a {len(ta)} ch, b {len(tb)} ch, diff {diff})")
            if args.show_diff and verdict != "PASS":
                for l in difflib.unified_diff(normalise(case.norm or case.group, ta).split("\n"),
                                              normalise(case.norm or case.group, tb).split("\n"),
                                              "A", "B", lineterm="", n=1):
                    print("    " + l)
            if args.bench and case.method != "<initialize>":
                key = case.bench_key or case.method
                for _ in range(args.bench):
                    if "a" in args.bench_sides:
                        times.setdefault((key, "a"), []).append(observe(a, case)[2])
                    if "b" in args.bench_sides:
                        times.setdefault((key, "b"), []).append(observe(b, case)[2])
    finally:
        a.close()
        b.close()

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("PASS", "NEAR_TIE", "EXPECTED_DIVERGENCE", "FAIL")}
    print(f"\n{len(rows)} cases: {counts['PASS']} PASS, {counts['NEAR_TIE']} NEAR_TIE, "
          f"{counts['EXPECTED_DIVERGENCE']} EXPECTED_DIVERGENCE, {counts['FAIL']} FAIL  (protocol {args.protocol})")
    metrics_differ = False
    for group, mrows in metric_rows.items():
        m = retrieval_metrics(mrows)
        print(f"{group} on the instrument ({m['a']['queries']} items): "
              f"A top1 {m['a']['top1']:.2f} top3 {m['a']['top3']:.2f} MRR {m['a']['mrr']:.4f} | "
              f"B top1 {m['b']['top1']:.2f} top3 {m['b']['top3']:.2f} MRR {m['b']['mrr']:.4f}")
        if m["a"] != m["b"]:
            metrics_differ = True
            print(f"FAIL {group}: retrieval metrics differ between A and B")
    if times:
        print("\nlatency ms (p50 / p95), A vs B:")
        for key in sorted({k for k, _ in times}):
            def pct(v, p):
                v = sorted(v)
                return v[max(0, min(len(v) - 1, int(round(p / 100 * len(v) + 0.5)) - 1))] * 1000
            ta, tb = times[(key, "a")], times[(key, "b")]
            print(f"  {key:<28} A {pct(ta, 50):9.1f} / {pct(ta, 95):9.1f} (n={len(ta)})   "
                  f"B {pct(tb, 50):9.1f} / {pct(tb, 95):9.1f} (n={len(tb)})")

    report = Path(args.report) if args.report else Path.home() / ".zurvan" / "parity" / f"{time.strftime('%Y%m%dT%H%M%S')}.json"
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report.write_text(json.dumps(rows, indent=1))
    os.chmod(report, 0o600)
    print(f"report: {report}")
    return 1 if counts["FAIL"] or metrics_differ else 0


if __name__ == "__main__":
    sys.exit(main())
