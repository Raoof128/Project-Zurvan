import argparse
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import subprocess
import subprocess
from scripts.memory import add_decision, add_note, add_claim, add_question
from scripts.context_export import search_memory, export_context

def main():
    parser = argparse.ArgumentParser(description="Zurvan - Local-first CLI Memory Interface")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
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

    args = parser.parse_args()
    
    if args.command == "remember":
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
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
