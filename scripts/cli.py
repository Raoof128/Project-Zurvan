import argparse
import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import subprocess
from scripts.memory import add_decision, add_note, add_claim, add_question
from scripts.context_export import search_memory, export_context
from scripts.config import PROJECT_ROOT


def _run_script(script_name: str, extra_args: list[str] | None = None) -> None:
    """Run a helper script with the current interpreter from the repo root and
    propagate its exit code. Previously these used a bare `python` and
    CWD-relative paths (broken from any other directory) and always exited 0
    even when the child failed (e.g. `zurvan eval search --min-top3`)."""
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / script_name)] + (extra_args or [])
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        sys.exit(result.returncode)

def main():
    args_list = sys.argv[1:]
    project_name = None
    project_root_override = None
    if "--project" in args_list:
        idx = args_list.index("--project")
        if idx + 1 < len(args_list):
            project_name = args_list[idx + 1]
            args_list.pop(idx)
            args_list.pop(idx)
    if "--project-root" in args_list:
        idx = args_list.index("--project-root")
        if idx + 1 < len(args_list):
            project_root_override = Path(args_list[idx + 1]).resolve()
            args_list.pop(idx)
            args_list.pop(idx)
    
    if project_name is not None:
        from scripts.workspace import resolve_project_root
        root = resolve_project_root(project_name)
        print(f"Running against project: {project_name}")
        cmd = [sys.executable, "scripts/cli.py"] + args_list
        sys.exit(subprocess.run(cmd, cwd=str(root)).returncode)

    if project_root_override is not None:
        import scripts.context_export as _context_export
        import scripts.wiki_merge as _wiki_merge
        _context_export.PROJECT_ROOT = project_root_override
        _wiki_merge.PROJECT_ROOT = project_root_override

    parser = argparse.ArgumentParser(description="Zurvan - Local-first CLI Memory Interface")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # zurvan project
    project_parser = subparsers.add_parser("project", help="Manage workspaces")
    project_sub = project_parser.add_subparsers(dest="action")
    
    p_reg = project_sub.add_parser("register")
    p_reg.add_argument("--name", required=True)
    p_reg.add_argument("--path", required=True)
    p_reg.add_argument("--force", action="store_true")
    
    project_sub.add_parser("list")
    project_sub.add_parser("current")
    
    p_use = project_sub.add_parser("use")
    p_use.add_argument("name")
    
    p_doc = project_sub.add_parser("doctor")
    p_doc.add_argument("name")
    
    p_snap = project_sub.add_parser("snapshot")
    p_snap.add_argument("name")
    
    p_search_all = project_sub.add_parser("search-all")
    p_search_all.add_argument("query")
    p_search_all.add_argument("--hybrid", action="store_true")
    p_search_all.add_argument("--limit", type=int, default=10)
    p_search_all.add_argument("--projects", nargs="+")
    p_search_all.add_argument("--strict", action="store_true")
    p_search_all.add_argument("--verbose", action="store_true")
    
    p_context_all = project_sub.add_parser("context-all")
    p_context_all.add_argument("--topic", required=True)
    p_context_all.add_argument("--hybrid", action="store_true")
    p_context_all.add_argument("--graph", action="store_true")
    p_context_all.add_argument("--limit", type=int, default=10)
    p_context_all.add_argument("--projects", nargs="+")
    p_context_all.add_argument("--strict", action="store_true")
    p_context_all.add_argument("--verbose", action="store_true")
    
    p_fed = project_sub.add_parser("federation")
    p_fed_sub = p_fed.add_subparsers(dest="fed_action")
    
    fed_stats = p_fed_sub.add_parser("stats")
    fed_stats.add_argument("--verbose", action="store_true")
    
    fed_doc = p_fed_sub.add_parser("doctor")
    fed_doc.add_argument("--strict", action="store_true")
    fed_doc.add_argument("--verbose", action="store_true")
    
    p_dec_all = project_sub.add_parser("decisions-all")
    p_dec_all.add_argument("--projects", nargs="+")
    p_dec_all.add_argument("--strict", action="store_true")
    p_dec_all.add_argument("--verbose", action="store_true")
    
    p_dec_sim = project_sub.add_parser("decisions-similar")
    p_dec_sim.add_argument("query")
    p_dec_sim.add_argument("--projects", nargs="+")
    p_dec_sim.add_argument("--limit", type=int, default=10)
    p_dec_sim.add_argument("--strict", action="store_true")
    p_dec_sim.add_argument("--verbose", action="store_true")
    
    p_dec_conf = project_sub.add_parser("decisions-conflicts")
    p_dec_conf.add_argument("--projects", nargs="+")
    p_dec_conf.add_argument("--strict", action="store_true")
    p_dec_conf.add_argument("--verbose", action="store_true")
    
    p_dec_stale = project_sub.add_parser("decisions-stale")
    p_dec_stale.add_argument("--days", type=int, default=90)
    p_dec_stale.add_argument("--projects", nargs="+")
    p_dec_stale.add_argument("--strict", action="store_true")
    p_dec_stale.add_argument("--verbose", action="store_true")
    
    p_dec_mem = project_sub.add_parser("decision-memory")
    p_dec_mem_sub = p_dec_mem.add_subparsers(dest="dec_action")
    p_dec_mem_rebuild = p_dec_mem_sub.add_parser("rebuild")
    p_dec_mem_rebuild.add_argument("--projects", nargs="+")
    p_dec_mem_rebuild.add_argument("--strict", action="store_true")
    p_dec_mem_rebuild.add_argument("--verbose", action="store_true")

    p_radar = project_sub.add_parser("radar")
    p_radar_sub = p_radar.add_subparsers(dest="radar_action")
    
    p_radar_scan = p_radar_sub.add_parser("scan")
    p_radar_scan.add_argument("--projects", nargs="+")
    p_radar_scan.add_argument("--strict", action="store_true")
    p_radar_scan.add_argument("--verbose", action="store_true")
    
    p_radar_contradictions = p_radar_sub.add_parser("contradictions")
    p_radar_contradictions.add_argument("--projects", nargs="+")
    p_radar_contradictions.add_argument("--strict", action="store_true")
    p_radar_contradictions.add_argument("--verbose", action="store_true")
    
    p_radar_policies = p_radar_sub.add_parser("policies")
    p_radar_policies.add_argument("--projects", nargs="+")
    p_radar_policies.add_argument("--strict", action="store_true")
    p_radar_policies.add_argument("--verbose", action="store_true")
    
    p_radar_drift = p_radar_sub.add_parser("drift")
    p_radar_drift.add_argument("--projects", nargs="+")
    p_radar_drift.add_argument("--strict", action="store_true")
    p_radar_drift.add_argument("--verbose", action="store_true")
    
    p_radar_report = p_radar_sub.add_parser("report")
    p_radar_report.add_argument("--projects", nargs="+")
    p_radar_report.add_argument("--strict", action="store_true")
    p_radar_report.add_argument("--verbose", action="store_true")
    p_radar_report.add_argument("--format", choices=["markdown"], default="markdown")
    
    # zurvan evidence
    evidence_parser = subparsers.add_parser("evidence", help="Manage evidence packs")
    evidence_sub = evidence_parser.add_subparsers(dest="evidence_action")
    
    e_build = evidence_sub.add_parser("build")
    e_build.add_argument("--topic", required=True)
    e_build.add_argument("--projects", nargs="+")
    e_build.add_argument("--hybrid", action="store_true")
    e_build.add_argument("--graph", action="store_true")
    e_build.add_argument("--include-decisions", action="store_true")
    e_build.add_argument("--include-policy-radar", action="store_true")
    e_build.add_argument("--limit", type=int, default=20)
    e_build.add_argument("--no-redact", action="store_false", dest="redact", default=True)
    e_build.add_argument("--verbose", action="store_true")
    
    e_list = evidence_sub.add_parser("list")
    
    e_inspect = evidence_sub.add_parser("inspect")
    e_inspect.add_argument("pack_id")
    
    e_export = evidence_sub.add_parser("export")
    e_export.add_argument("pack_id")
    e_export.add_argument("--format", choices=["markdown", "json"], default="markdown")
    e_export.add_argument("--output-dir")
    
    e_redact = evidence_sub.add_parser("redact")
    e_redact.add_argument("pack_id")

    # zurvan report
    report_parser = subparsers.add_parser("report", help="Compose structured reports")
    report_sub = report_parser.add_subparsers(dest="report_action")
    
    r_compose = report_sub.add_parser("compose")
    r_compose.add_argument("--pack", required=True)
    r_compose.add_argument("--template", default="evidence_digest", 
                          choices=["executive_summary", "technical_audit", "research_brief", "decision_log", "risk_review", "evidence_digest"])
    r_compose.add_argument("--format", default="markdown")
    r_compose.add_argument("--allow-unsafe", action="store_true", help="Allow unredacted output")
    
    r_list = report_sub.add_parser("list")
    
    r_inspect = report_sub.add_parser("inspect")
    r_inspect.add_argument("report_id")
    
    r_export = report_sub.add_parser("export")
    r_export.add_argument("report_id")
    r_export.add_argument("--format", choices=["markdown", "json"], default="markdown")
    r_export.add_argument("--output-dir")
    r_export.add_argument("--allow-unsafe", action="store_true")
    
    r_validate = report_sub.add_parser("validate")
    r_validate.add_argument("report_id")
    
    # zurvan review
    review_parser = subparsers.add_parser("review", help="Local Report Review Workbench")
    review_sub = review_parser.add_subparsers(dest="review_action")
    
    rev_serve = review_sub.add_parser("serve")
    rev_serve.add_argument("--host", default="127.0.0.1")
    rev_serve.add_argument("--port", type=int, default=8765)
    rev_serve.add_argument("--allow-lan", action="store_true")
    rev_serve.add_argument("--open", action="store_true")
    
    rev_list = review_sub.add_parser("list")
    
    rev_open = review_sub.add_parser("open")
    rev_open.add_argument("report_id")
    
    rev_audit = review_sub.add_parser("audit")
    rev_audit.add_argument("report_id", nargs="?")
    
    rev_index = review_sub.add_parser("index")
    rev_index.add_argument("action", choices=["rebuild"])
    
    rev_checklist = review_sub.add_parser("checklist")
    rev_checklist.add_argument("report_id")
    
    # zurvan publish
    publish_parser = subparsers.add_parser("publish", help="Export and package reports")
    publish_sub = publish_parser.add_subparsers(dest="publish_action", required=True)
    
    pub_export = publish_sub.add_parser("export")
    pub_export.add_argument("report_id")
    pub_export.add_argument("--format", choices=["markdown", "json", "html", "pdf", "docx"], default="markdown")
    pub_export.add_argument("--output-dir")
    pub_export.add_argument("--force", action="store_true")
    pub_export.add_argument("--verbose", action="store_true")
    
    pub_bundle = publish_sub.add_parser("bundle")
    pub_bundle.add_argument("report_id")
    pub_bundle.add_argument("--format", choices=["directory", "zip"], default="directory")
    pub_bundle.add_argument("--output-dir")
    pub_bundle.add_argument("--force", action="store_true")
    pub_bundle.add_argument("--verbose", action="store_true")
    
    pub_cit = publish_sub.add_parser("citations")
    pub_cit.add_argument("report_id")
    
    pub_val = publish_sub.add_parser("validate")
    pub_val.add_argument("report_id")

    # zurvan trace
    trace_parser = subparsers.add_parser("trace", help="Inspect and replay audit traces")
    trace_sub = trace_parser.add_subparsers(dest="trace_action")

    trace_sub.add_parser("list", help="List saved traces")

    trace_inspect = trace_sub.add_parser("inspect", help="Print a trace JSON document")
    trace_inspect.add_argument("trace_id")

    trace_validate = trace_sub.add_parser("validate", help="Validate a trace JSON document")
    trace_validate.add_argument("trace_id")

    trace_replay = trace_sub.add_parser("replay", help="Render a deterministic trace replay")
    trace_replay.add_argument("trace_id")
    
    # zurvan remember
    remember_parser = subparsers.add_parser("remember", help="Remember a project note")
    remember_parser.add_argument("--type", choices=["note", "summary", "finding"], default="note")
    remember_parser.add_argument("--title", required=True, help="Title of the memory")
    remember_parser.add_argument("--body", required=True, help="Body text")
    remember_parser.add_argument("--tags", nargs="+", default=[], help="Tags")
    
    # zurvan decision add
    decision_parser = subparsers.add_parser("decision", help="Manage decisions")
    decision_sub = decision_parser.add_subparsers(dest="action")
    dec_add = decision_sub.add_parser("add")
    dec_add.add_argument("--title", required=True)
    dec_add.add_argument("--reason", required=True)
    dec_add.add_argument("--status", required=True)
    dec_add.add_argument("--tags", nargs="+", default=[])
    
    # zurvan claim add
    claim_parser = subparsers.add_parser("claim", help="Manage claims")
    claim_sub = claim_parser.add_subparsers(dest="action")
    clm_add = claim_sub.add_parser("add")
    clm_add.add_argument("--text", required=True)
    clm_add.add_argument("--source", required=True)
    clm_add.add_argument("--evidence", required=True)
    clm_add.add_argument("--confidence", required=True)
    clm_add.add_argument("--tags", nargs="+", default=[])
    
    # zurvan question add
    question_parser = subparsers.add_parser("question", help="Manage open questions")
    question_sub = question_parser.add_subparsers(dest="action")
    q_add = question_sub.add_parser("add")
    q_add.add_argument("--question", required=True)
    q_add.add_argument("--reason", required=True)
    q_add.add_argument("--tags", nargs="+", default=[])
    
    # zurvan search
    search_parser = subparsers.add_parser("search", help="Search memory")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--hybrid", action="store_true", help="Use hybrid search")
    search_parser.add_argument("--save", action="store_true", help="File results into wiki/syntheses/")
    search_parser.add_argument("--trace", action="store_true", help="Write an opt-in retrieval trace")
    search_parser.add_argument("--trace-id", help="Optional trace ID for deterministic audit runs")
    search_parser.add_argument("--json", action="store_true", dest="as_json",
                               help="Emit machine-parseable JSON (compact snippets) instead of text")

    # zurvan context
    context_parser = subparsers.add_parser("context", help="Export context bundle")
    context_parser.add_argument("--topic", required=True)
    context_parser.add_argument("--limit", type=int, default=10)
    context_parser.add_argument("--hybrid", action="store_true", help="Use hybrid search")
    context_parser.add_argument("--graph", action="store_true", help="Expand graph neighbours")
    context_parser.add_argument("--depth", type=int, default=1, help="Graph expansion depth")
    context_parser.add_argument("--save", action="store_true", help="File answer back into wiki/syntheses/")
    context_parser.add_argument("--trace", action="store_true", help="Write an opt-in retrieval trace")
    context_parser.add_argument("--trace-id", help="Optional trace ID for deterministic audit runs")
    context_parser.add_argument("--max-per-source", type=int, default=2,
                                help="Cap chunks per source file before budgeting (0 disables; default 2)")
    context_parser.add_argument(
        "--format",
        choices=["markdown", "table", "marp", "json"],
        default="markdown",
        dest="output_format",
        help="Output format for stdout. --save always writes canonical Markdown.",
    )

    # zurvan audit
    subparsers.add_parser("audit", help="Audit the wiki")
    
    # zurvan index
    index_parser = subparsers.add_parser("index", help="Manage index")
    index_sub = index_parser.add_subparsers(dest="action")
    index_sub.add_parser("rebuild")
    index_sub.add_parser("search")
    
    # zurvan eval
    eval_parser = subparsers.add_parser("eval", help="Evaluation commands")
    eval_sub = eval_parser.add_subparsers(dest="action")
    
    eval_search_parser = eval_sub.add_parser("search", help="Evaluate search retrieval")
    eval_search_parser.add_argument("--gold", default="eval/search_gold.jsonl")
    eval_search_parser.add_argument("--hybrid", action="store_true")
    eval_search_parser.add_argument("--min-top3", type=float, default=0.0)
    eval_search_parser.add_argument("--json", action="store_true", dest="as_json")

    eval_provenance_parser = eval_sub.add_parser("provenance", help="Evaluate retrieval trace provenance")
    eval_provenance_parser.add_argument("--gold", default="eval/provenance_gold.jsonl")
    eval_provenance_parser.add_argument("--validate", action="store_true")
    eval_provenance_parser.add_argument("--min-source-recall", type=float, default=0.0)
    eval_provenance_parser.add_argument("--min-provenance-completeness", type=float, default=0.0)
    eval_provenance_parser.add_argument("--min-graph-context-presence", type=float, default=0.0)
    eval_provenance_parser.add_argument("--json", action="store_true", dest="as_json")
    
    eval_validate_parser = eval_sub.add_parser("validate-gold", help="Validate gold dataset")
    eval_validate_parser.add_argument("--gold", default="eval/search_gold.jsonl")
    
    # zurvan graph
    graph_parser = subparsers.add_parser("graph", help="Knowledge graph commands")
    graph_sub = graph_parser.add_subparsers(dest="action")
    
    graph_sub.add_parser("rebuild", help="Rebuild graph from files")
    graph_sub.add_parser("stats", help="Show graph stats")
    
    graph_neighbours_parser = graph_sub.add_parser("neighbours", help="Show neighbours of a node")
    graph_neighbours_parser.add_argument("path", help="Path or ID of the node")
    
    graph_expand_parser = graph_sub.add_parser("expand", help="Expand graph neighbours for context")
    graph_expand_parser.add_argument("path", help="Path or ID of the node")
    graph_expand_parser.add_argument("--depth", type=int, default=2, help="Depth to expand")
    
    graph_trace_parser = graph_sub.add_parser("trace", help="Trace node paths")
    graph_trace_parser.add_argument("path", help="Path or ID of the node")
    
    graph_export_parser = graph_sub.add_parser("export", help="Export graph")
    graph_export_parser.add_argument("--format", choices=["markdown", "dot"], required=True)

    # zurvan session
    session_parser = subparsers.add_parser("session", help="Manage agent sessions")
    session_sub = session_parser.add_subparsers(dest="action")
    
    sess_start = session_sub.add_parser("start", help="Start a session")
    sess_start.add_argument("--topic", required=True)
    
    sess_close = session_sub.add_parser("close", help="Close a session")
    sess_close.add_argument("--topic", required=True)
    sess_close.add_argument("--summary", required=True)
    sess_close.add_argument("--checks", required=True)
    
    # zurvan agent
    agent_parser = subparsers.add_parser("agent", help="Agent workflows")
    agent_sub = agent_parser.add_subparsers(dest="action")
    
    agent_pref = agent_sub.add_parser("preflight", help="Agent preflight context")
    agent_pref.add_argument("--topic", required=True)
    agent_pref.add_argument("--hybrid", action="store_true")
    agent_pref.add_argument("--graph", action="store_true")
    agent_pref.add_argument("--limit", type=int, default=10)
    
    agent_sub.add_parser("prime", help="Compact session-start orientation card (~300 tokens)")

    agent_post = agent_sub.add_parser("postedit", help="Agent post-edit log")
    agent_post.add_argument("--summary", required=True)
    agent_post.add_argument("--files", nargs="+", required=True)
    agent_post.add_argument("--checks", required=True)

    # zurvan version
    subparsers.add_parser("version", help="Print version and environment info")
    
    # zurvan doctor
    subparsers.add_parser("doctor", help="Run system health checks")
    
    # zurvan snapshot
    snapshot_parser = subparsers.add_parser("snapshot", help="Manage release snapshots")
    snapshot_sub = snapshot_parser.add_subparsers(dest="action")
    
    snap_create = snapshot_sub.add_parser("create", help="Create a snapshot")
    snap_create.add_argument("--include-raw", action="store_true")
    
    snapshot_sub.add_parser("list", help="List snapshots")
    
    snap_restore = snapshot_sub.add_parser("restore", help="Restore a snapshot")
    snap_restore.add_argument("snapshot_name")
    snap_restore.add_argument("--force", action="store_true")

    args = parser.parse_args(args_list)
    
    if args.command == "project":
        if args.action == "register":
            from scripts.project_registry import register_project
            register_project(args.name, args.path, args.force)
            from scripts.workspace import shorten_path
            print(f"✅ Registered project '{args.name}' at {shorten_path(args.path)}")
        elif args.action == "list":
            from scripts.project_registry import load_registry
            from scripts.workspace import is_valid_zurvan_project, shorten_path
            registry = load_registry()
            print("Registered Projects:")
            for name, data in registry["projects"].items():
                marker = "*" if name == registry.get("current") else " "
                path = data["path"]
                status = "✅" if is_valid_zurvan_project(path) else "❌ (missing/invalid)"
                print(f" {marker} {name:20} {shorten_path(path)} {status}")
        elif args.action == "current":
            from scripts.project_registry import get_current_project
            from scripts.workspace import shorten_path
            name, path = get_current_project()
            if name:
                print(f"Current project: {name} at {shorten_path(path)}")
            else:
                print("No current project.")
                sys.exit(1)
        elif args.action == "use":
            from scripts.project_registry import set_current_project, load_registry
            from scripts.workspace import is_valid_zurvan_project, shorten_path
            registry = load_registry()
            if args.name not in registry["projects"]:
                print(f"❌ Project '{args.name}' not found.")
                sys.exit(1)
            path = registry["projects"][args.name]["path"]
            if not is_valid_zurvan_project(path):
                print(f"❌ Warning: Project path is invalid or missing: {shorten_path(path)}")
            set_current_project(args.name)
            print(f"✅ Switched to project '{args.name}'")
        elif args.action == "doctor":
            from scripts.workspace import resolve_project_root
            root = resolve_project_root(args.name)
            print(f"Running doctor for project: {args.name}")
            sys.exit(subprocess.run([sys.executable, "scripts/cli.py", "doctor"], cwd=str(root)).returncode)
        elif args.action == "snapshot":
            from scripts.workspace import resolve_project_root
            root = resolve_project_root(args.name)
            print(f"Running snapshot for project: {args.name}")
            sys.exit(subprocess.run([sys.executable, "scripts/cli.py", "snapshot", "create"], cwd=str(root)).returncode)
        elif args.action == "search-all":
            from scripts.cross_project_search import cross_project_search
            import json
            res = cross_project_search(args.query, args.hybrid, args.limit, args.projects, args.strict, args.verbose)
            print(json.dumps(res, indent=2))
        elif args.action == "context-all":
            from scripts.cross_project_context import build_federated_context
            res = build_federated_context(args.topic, args.hybrid, args.graph, args.limit, args.projects, args.strict, args.verbose)
            print(res)
        elif args.action == "federation":
            if args.fed_action == "stats":
                from scripts.federation import get_federation_stats
                import json
                stats = get_federation_stats(args.verbose)
                print(json.dumps(stats, indent=2))
            elif args.fed_action == "doctor":
                from scripts.federation import run_federation_doctor
                success = run_federation_doctor(args.strict, args.verbose)
                if not success and args.strict:
                    sys.exit(1)
        elif args.action == "decisions-all":
            from scripts.decision_federation import collect_federated_decisions, format_decisions_all
            ds = collect_federated_decisions(args.projects, args.strict, args.verbose)
            print(format_decisions_all(ds))
        elif args.action == "decisions-similar":
            from scripts.decision_federation import collect_federated_decisions, format_similar_decisions
            ds = collect_federated_decisions(args.projects, args.strict, args.verbose)
            print(format_similar_decisions(ds, args.query, args.limit))
        elif args.action == "decisions-conflicts":
            from scripts.decision_federation import collect_federated_decisions, format_decision_conflicts
            ds = collect_federated_decisions(args.projects, args.strict, args.verbose)
            print(format_decision_conflicts(ds))
        elif args.action == "decisions-stale":
            from scripts.decision_federation import collect_federated_decisions, format_stale_decisions
            ds = collect_federated_decisions(args.projects, args.strict, args.verbose)
            print(format_stale_decisions(ds, args.days))
        elif args.action == "decision-memory" and getattr(args, "dec_action", None) == "rebuild":
            from scripts.decision_federation import rebuild_decision_memory
            count = rebuild_decision_memory(args.projects, args.strict, args.verbose)
            print(f"Rebuilt cache with {count} decisions.")
        elif args.action == "radar":
            from scripts.claim_federation import collect_federated_claims_and_policies
            from scripts.policy_radar import format_policy_scan, format_policy_coverage, format_policy_drift, generate_full_report, save_report_locally, analyze_policies
            from scripts.contradiction_radar import detect_contradictions, format_contradictions
            
            items = collect_federated_claims_and_policies(args.projects, args.strict, args.verbose)
            
            if args.radar_action == "scan":
                print(format_policy_scan(items))
            elif args.radar_action == "contradictions":
                conflicts = detect_contradictions(items)
                print(format_contradictions(conflicts))
            elif args.radar_action == "policies":
                analysis = analyze_policies(items)
                print(format_policy_coverage(analysis))
            elif args.radar_action == "drift":
                analysis = analyze_policies(items)
                print(format_policy_drift(analysis))
            elif args.radar_action == "report":
                report = generate_full_report(items)
                print(report)
                path = save_report_locally(report)
                print(f"\nReport saved to: {path}")
    elif args.command == "evidence":
        from scripts.evidence_pack import build_evidence_pack, list_evidence_packs, inspect_evidence_pack
        from scripts.evidence_export import export_evidence_pack, redact_existing_pack
        
        if args.evidence_action == "build":
            res = build_evidence_pack(
                args.topic, args.projects, args.hybrid, args.graph, 
                args.include_decisions, args.include_policy_radar, 
                args.limit, args.redact
            )
            print(f"Evidence pack created: {res['pack_id']}")
            print(f"Items collected: {res['item_count']}")
            if args.verbose:
                print(f"Output directory: {res['path']}")
                
        elif args.evidence_action == "list":
            packs = list_evidence_packs()
            if not packs:
                print("No evidence packs found.")
            else:
                for p in packs:
                    print(f"- {p['pack_id']} | Topic: '{p.get('topic', '')}' | Items: {p.get('item_count', 0)} | Created: {p.get('created_at', '')}")
                    
        elif args.evidence_action == "inspect":
            data = inspect_evidence_pack(args.pack_id)
            if not data:
                print(f"Pack {args.pack_id} not found.")
                sys.exit(1)
            manifest = data["manifest"]
            print(f"Pack ID: {manifest['pack_id']}")
            print(f"Topic: {manifest.get('topic', '')}")
            print(f"Items: {manifest.get('item_count', 0)}")
            print(f"Redaction: {manifest.get('redaction_status', 'unknown')}")
            
        elif args.evidence_action == "export":
            try:
                path = export_evidence_pack(args.pack_id, args.format, args.output_dir)
                print(f"Exported to {path}")
            except Exception as e:
                print(f"Export failed: {e}")
                sys.exit(1)
                
        elif args.evidence_action == "redact":
            if redact_existing_pack(args.pack_id):
                print(f"Redacted pack {args.pack_id}")
            else:
                print(f"Pack {args.pack_id} not found or already redacted.")
                
    elif args.command == "report":
        from scripts.report_compose import compose_report, list_reports, inspect_report, validate_report
        from scripts.report_export import export_report
        
        if args.report_action == "compose":
            rep = compose_report(args.pack, args.template, args.allow_unsafe)
            print(f"Report composed: {rep['report_id']}")
            
            for f in args.format.split(","):
                path = export_report(rep["report_id"], f, allow_unsafe=args.allow_unsafe)
                print(f"Exported {f}: {path}")
                
        elif args.report_action == "list":
            reps = list_reports()
            if not reps:
                print("No reports found.")
            for r in reps:
                print(f"- {r['report_id']} | Topic: '{r.get('topic','')}' | Template: {r.get('template','')} | Created: {r.get('created_at','')}")
                
        elif args.report_action == "inspect":
            data = inspect_report(args.report_id)
            if not data:
                print(f"Report {args.report_id} not found.")
                sys.exit(1)
            print(f"Report ID: {data['report_id']}")
            print(f"Topic: {data.get('topic', '')}")
            print(f"Template: {data.get('template', '')}")
            print(f"Source Pack: {data.get('source_pack_id', '')}")
            print(f"Redaction: {data.get('redaction_status', '')}")
            if data.get("warnings"):
                print("Warnings:")
                for w in data["warnings"]:
                    print(f" - {w}")
                    
        elif args.report_action == "export":
            try:
                path = export_report(args.report_id, args.format, args.output_dir, args.allow_unsafe)
                print(f"Exported to {path}")
            except Exception as e:
                print(f"Export failed: {e}")
                sys.exit(1)
                
        elif args.report_action == "validate":
            res = validate_report(args.report_id)
            if res["valid"]:
                print(f"Report {args.report_id} is valid.")
            else:
                print(f"Report {args.report_id} has issues:")
                for iss in res["issues"]:
                    print(f" - {iss}")
                sys.exit(1)
            
            if res.get("warnings"):
                print("Validation Warnings:")
                for w in res["warnings"]:
                    print(f" - {w}")
                    
    elif args.command == "review":
        if args.review_action == "serve":
            from scripts.review_server import run_server
            run_server(host=args.host, port=args.port, allow_lan=args.allow_lan, open_browser=args.open)
        elif args.review_action == "list":
            from scripts.report_compose import list_reports
            reps = list_reports()
            for r in reps:
                print(f"{r['report_id']} - {r['topic']}")
        elif args.review_action == "open":
            import webbrowser
            url = f"http://127.0.0.1:8765/reports/{args.report_id}"
            webbrowser.open(url)
            print(f"Opened {url}")
        elif args.review_action == "audit":
            if args.report_id:
                from scripts.review_audit import audit_report
                audit = audit_report(args.report_id)
                print(f"Audit Status for {args.report_id}: {audit['status'].upper()}")
                for w in audit["warnings"]: print(f"WARN: {w}")
                for f in audit["failures"]: print(f"FAIL: {f}")
            else:
                from scripts.review_audit import audit_all_reports
                audits = audit_all_reports()
                for a in audits:
                    print(f"{a['report_id']} - {a['status'].upper()}")
        elif args.review_action == "index":
            from scripts.review_index import rebuild_index
            idx = rebuild_index()
            print(f"Index rebuilt. {len(idx['reports'])} reports, {len(idx['packs'])} packs.")
        elif args.review_action == "checklist":
            import webbrowser
            url = f"http://127.0.0.1:8765/reports/{args.report_id}/checklist"
            webbrowser.open(url)
            print(f"Opened checklist for {args.report_id}")
            
    elif args.command == "publish":
        if args.publish_action == "export":
            from scripts.publication_export import export_publication
            out_dir = Path(args.output_dir) if args.output_dir else None
            try:
                out = export_publication(args.report_id, args.format, args.force, out_dir)
                print(f"Exported {args.format} to {out}")
            except Exception as e:
                print(f"Export failed: {e}")
                sys.exit(1)
        elif args.publish_action == "bundle":
            from scripts.publication_bundle import create_bundle
            out_dir = Path(args.output_dir) if args.output_dir else None
            try:
                out = create_bundle(args.report_id, args.format, args.force, out_dir)
                print(f"Created bundle at {out}")
            except Exception as e:
                print(f"Bundle failed: {e}")
                sys.exit(1)
        elif args.publish_action == "citations":
            from scripts.report_compose import inspect_report
            from scripts.evidence_pack import inspect_evidence_pack
            from scripts.publication_citations import generate_citation_appendix
            rep = inspect_report(args.report_id)
            if not rep:
                print("Report not found.")
                sys.exit(1)
            pack_id = rep.get("source_pack_id")
            pack = inspect_evidence_pack(pack_id) if pack_id else {}
            app = generate_citation_appendix(rep, pack)
            for c in app:
                print(f"[{c['evidence_id']}] {c['project']} / {c['relative_path']}")
                print(f"  {c['excerpt']}")
        elif args.publish_action == "validate":
            from scripts.review_audit import audit_report
            audit = audit_report(args.report_id)
            print(f"Validation Status: {audit['status'].upper()}")
            for w in audit["warnings"]: print(f"WARN: {w}")
            for f in audit["failures"]: print(f"FAIL: {f}")
            if audit["status"] == "fail": sys.exit(1)

    elif args.command == "trace":
        from scripts.trace_replay import replay_trace_file
        from scripts.trace_validate import validate_trace_file
        from scripts.trace_writer import TraceStore
        import json

        store = TraceStore(project_root=project_root_override or Path(__file__).parent.parent)
        if args.trace_action == "list":
            traces = store.list()
            if not traces:
                print("No traces found.")
            for trace in traces:
                print(
                    f"- {trace['trace_id']} | {trace['title']} | "
                    f"events: {trace['event_count']} | created: {trace['created_at']}"
                )
        elif args.trace_action == "inspect":
            try:
                print(json.dumps(store.read(args.trace_id), indent=2, sort_keys=True))
            except FileNotFoundError as exc:
                print(str(exc))
                sys.exit(1)
            except ValueError as exc:
                print(str(exc))
                sys.exit(1)
        elif args.trace_action == "validate":
            try:
                result = validate_trace_file(store.trace_path(args.trace_id))
            except ValueError as exc:
                print(str(exc))
                sys.exit(1)
            if result.valid:
                print(f"Trace {args.trace_id} is valid.")
            else:
                print(f"Trace {args.trace_id} has issues:")
                for issue in result.issues:
                    print(f" - {issue}")
                sys.exit(1)
        elif args.trace_action == "replay":
            try:
                print(replay_trace_file(store.trace_path(args.trace_id)), end="")
            except (FileNotFoundError, ValueError) as exc:
                print(str(exc))
                sys.exit(1)
        else:
            trace_parser.print_help()

                
    elif args.command == "remember":
        if add_note(args.title, args.body, args.tags) is False:
            sys.exit(1)
    elif args.command == "decision" and args.action == "add":
        if add_decision(args.title, args.reason, args.status, args.tags) is False:
            sys.exit(1)
    elif args.command == "claim" and args.action == "add":
        if add_claim(args.text, args.source, args.evidence, args.confidence, args.tags) is False:
            sys.exit(1)
    elif args.command == "question" and args.action == "add":
        if add_question(args.question, args.reason, args.tags) is False:
            sys.exit(1)
    elif args.command == "search":
        try:
            search_memory(
                args.query,
                args.hybrid,
                save=getattr(args, "save", False),
                trace=getattr(args, "trace", False),
                trace_id=getattr(args, "trace_id", None),
                as_json=getattr(args, "as_json", False),
            )
        except ValueError as exc:
            print(str(exc))
            sys.exit(1)
    elif args.command == "context":
        from scripts.context_export import export_context
        try:
            bundle = export_context(
                args.topic, args.limit, args.hybrid, args.graph, args.depth,
                save=getattr(args, "save", False),
                fmt=getattr(args, "output_format", "markdown"),
                trace=getattr(args, "trace", False),
                trace_id=getattr(args, "trace_id", None),
                max_per_source=getattr(args, "max_per_source", 2),
            )
        except ValueError as exc:
            print(str(exc))
            sys.exit(1)
        print(bundle)
    elif args.command == "audit":
        _run_script("audit_wiki.py")
    elif args.command == "index" and args.action == "rebuild":
        _run_script("rebuild_index.py")
    elif args.command == "index" and args.action == "search":
        _run_script("rebuild_search_index.py")
    elif args.command == "eval" and args.action == "search":
        _run_script("eval_search.py", [
            "--gold", args.gold,
            "--min-top3", str(args.min_top3),
        ] + (["--hybrid"] if args.hybrid else [])
          + (["--json"] if getattr(args, "as_json", False) else []))
    elif args.command == "eval" and args.action == "validate-gold":
        _run_script("eval_search.py", ["--gold", args.gold, "--validate"])
    elif args.command == "eval" and args.action == "provenance":
        from scripts.eval_provenance import run_provenance_evaluation, validate_gold_dataset
        if args.validate:
            validate_gold_dataset(args.gold)
        else:
            run_provenance_evaluation(
                args.gold,
                min_source_recall=args.min_source_recall,
                min_provenance_completeness=args.min_provenance_completeness,
                min_graph_context_presence=args.min_graph_context_presence,
                as_json=getattr(args, "as_json", False),
            )
    elif args.command == "graph":
        if args.action == "rebuild":
            _run_script("graph_build.py")
        elif args.action == "stats":
            from scripts.graph_query import get_stats
            stats = get_stats()
            print(f"Graph stats: {stats['nodes']} nodes, {stats['edges']} edges")
        elif args.action == "neighbours":
            from scripts.graph_query import get_neighbours
            neighbours = get_neighbours(args.path)
            for n in neighbours:
                print(f"{n['from_title']} --[{n['edge_type']}]--> {n['to_title']}")
        elif args.action == "expand":
            from scripts.graph_context import expand_graph_context
            items = expand_graph_context([args.path], args.depth)
            for i in items:
                print(f"[{i['depth']}] {i['title']} ({i['node_type']}) - {i['relation']}")
        elif args.action == "trace":
            from scripts.graph_query import trace_node
            result = trace_node(args.path)
            print(f"Traced {result['nodes_visited']} nodes.")
        elif args.action == "export":
            _run_script("graph_export.py", ["--format", args.format])
        else:
            graph_parser.print_help()
    elif args.command == "session" and args.action == "start":
        from scripts.session import session_start
        print(session_start(args.topic))
    elif args.command == "session" and args.action == "close":
        from scripts.session import session_close
        print(session_close(args.topic, args.summary, args.checks))
    elif args.command == "agent" and args.action == "prime":
        from scripts.agent_workflow import agent_prime
        print(agent_prime())
    elif args.command == "agent" and args.action == "preflight":
        from scripts.agent_workflow import agent_preflight
        print(agent_preflight(args.topic, args.hybrid, args.graph, args.limit))
    elif args.command == "agent" and args.action == "postedit":
        from scripts.agent_workflow import agent_postedit
        print(agent_postedit(args.summary, args.files, args.checks))
    elif args.command == "version":
        from scripts.version import print_version
        print_version()
    elif args.command == "doctor":
        from scripts.doctor import run_doctor
        sys.exit(run_doctor())
    elif args.command == "snapshot":
        if args.action == "create":
            from scripts.snapshot import create_snapshot
            create_snapshot(args.include_raw)
        elif args.action == "list":
            from scripts.snapshot import list_snapshots
            list_snapshots()
        elif args.action == "restore":
            from scripts.restore_snapshot import restore_snapshot
            restore_snapshot(args.snapshot_name, args.force)
        else:
            snapshot_parser.print_help()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
