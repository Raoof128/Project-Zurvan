import argparse
import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import subprocess
from scripts.memory import add_decision, add_note, add_claim, add_question
from scripts.context_export import search_memory, export_context

def main():
    args_list = sys.argv[1:]
    project_name = None
    if "--project" in args_list:
        idx = args_list.index("--project")
        if idx + 1 < len(args_list):
            project_name = args_list[idx + 1]
            args_list.pop(idx)
            args_list.pop(idx)
    
    if project_name is not None:
        from scripts.workspace import resolve_project_root
        root = resolve_project_root(project_name)
        print(f"Running against project: {project_name}")
        cmd = [sys.executable, "scripts/cli.py"] + args_list
        sys.exit(subprocess.run(cmd, cwd=str(root)).returncode)

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
    
    # zurvan context
    context_parser = subparsers.add_parser("context", help="Export context bundle")
    context_parser.add_argument("--topic", required=True)
    context_parser.add_argument("--limit", type=int, default=10)
    context_parser.add_argument("--hybrid", action="store_true", help="Use hybrid search")
    context_parser.add_argument("--graph", action="store_true", help="Expand graph neighbours")
    context_parser.add_argument("--depth", type=int, default=1, help="Graph expansion depth")
    
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
        search_memory(args.query, args.hybrid)
    elif args.command == "context":
        from scripts.context_export import export_context
        bundle = export_context(args.topic, args.limit, args.hybrid, args.graph, args.depth)
        print(bundle)
    elif args.command == "audit":
        subprocess.run(["python", "scripts/audit_wiki.py"])
    elif args.command == "index" and args.action == "rebuild":
        subprocess.run(["python", "scripts/rebuild_index.py"])
    elif args.command == "index" and args.action == "search":
        subprocess.run(["python", "scripts/rebuild_search_index.py"])
    elif args.command == "eval" and args.action == "search":
        subprocess.run([
            "python", "scripts/eval_search.py",
            "--gold", args.gold,
            "--min-top3", str(args.min_top3)
        ] + (["--hybrid"] if args.hybrid else []))
    elif args.command == "eval" and args.action == "validate-gold":
        subprocess.run([
            "python", "scripts/eval_search.py",
            "--gold", args.gold,
            "--validate"
        ])
    elif args.command == "graph":
        if args.action == "rebuild":
            subprocess.run(["python", "scripts/graph_build.py"])
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
            subprocess.run(["python", "scripts/graph_export.py", "--format", args.format])
        else:
            graph_parser.print_help()
    elif args.command == "session" and args.action == "start":
        from scripts.session import session_start
        print(session_start(args.topic))
    elif args.command == "session" and args.action == "close":
        from scripts.session import session_close
        print(session_close(args.topic, args.summary, args.checks))
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
