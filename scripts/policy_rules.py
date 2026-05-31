POLICY_CATEGORIES = {
    "raw_protection": {
        "positive": ["raw is immutable", "never modify raw", "do not write to raw", "raw sources as untrusted", "no raw writes"],
        "negative": ["write to raw", "store generated output in raw", "modify raw", "update raw"],
        "description": "Rules protecting the raw/ directory from mutation."
    },
    "no_cloud": {
        "positive": ["no cloud", "no cloud apis", "local first", "no remote sync"],
        "negative": ["use cloud", "remote sync", "sync to cloud", "cloud api"],
        "description": "Rules enforcing local-first, cloud-free architecture."
    },
    "no_llm": {
        "positive": ["no llm", "no llm calls", "without llm", "heuristic only", "no llm in infrastructure"],
        "negative": ["use llm", "llm call", "llm provider required"],
        "description": "Rules restricting LLM usage in core logic."
    },
    "mcp_readonly_default": {
        "positive": ["mcp readonly default", "mcp write mode disabled by default", "read-only mcp default", "read only mcp"],
        "negative": ["mcp write mode enabled by default", "mcp readonly false by default"],
        "description": "Rules enforcing MCP safety defaults."
    },
    "public_repo_safety": {
        "positive": ["public repo safety", "do not commit private data", "do not commit registry data", "hide absolute paths"],
        "negative": ["commit registry", "show absolute paths", "leak absolute paths"],
        "description": "Rules protecting against leaks in public repositories."
    },
    "snapshot_excludes_raw": {
        "positive": ["snapshot excludes raw", "do not snapshot raw", "raw excluded from snapshot"],
        "negative": ["snapshot includes raw", "snapshot raw"],
        "description": "Rules ensuring snapshots do not pack private raw/ sources."
    },
    "no_path_traversal": {
        "positive": ["no path traversal", "prevent path traversal", "safe paths only"],
        "negative": ["allow path traversal", "relative paths permitted in read"],
        "description": "Rules protecting against directory traversal."
    },
    "no_shell_execution": {
        "positive": ["no shell execution", "do not execute shell", "never execute code"],
        "negative": ["execute shell", "run shell command", "execute source code"],
        "description": "Rules blocking arbitrary code execution."
    },
    "obsidian_optional": {
        "positive": ["obsidian optional", "no obsidian dependency", "obsidian friendly"],
        "negative": ["obsidian required", "obsidian plugin required"],
        "description": "Rules ensuring Obsidian remains an optional frontend."
    },
    "graph_no_remote_db": {
        "positive": ["graph no remote db", "local sqlite graph", "local sqlite only"],
        "negative": ["neo4j required", "remote graph db"],
        "description": "Rules keeping the knowledge graph local."
    },
    "registry_outside_repo": {
        "positive": ["registry outside repo", "local registry", "~/.zurvan/projects.json"],
        "negative": ["registry inside repo", "commit projects.json"],
        "description": "Rules forcing the project registry to live in user home directory."
    }
}

def identify_policies(text: str) -> dict:
    if not text:
        return {}
    
    text_lower = " " + text.lower() + " "
    matches = {}
    
    for category, rules in POLICY_CATEGORIES.items():
        # Check positive keywords ensuring they aren't part of a larger negative context
        # A simple way to avoid substring matches is checking with spaces if possible, or using \b
        import re
        
        matched_pos = []
        for p in rules["positive"]:
            if re.search(r'\b' + re.escape(p) + r'\b', text_lower):
                matched_pos.append(p)
                
        matched_neg = []
        for n in rules["negative"]:
            if re.search(r'\b' + re.escape(n) + r'\b', text_lower):
                # Only add if it's not a substring of a matched positive
                is_sub = False
                for p in matched_pos:
                    if n in p:
                        is_sub = True
                        break
                if not is_sub:
                    matched_neg.append(n)
        
        if matched_pos or matched_neg:
            matches[category] = {
                "positive_hits": matched_pos,
                "negative_hits": matched_neg,
                "status": "conflict" if (matched_pos and matched_neg) else ("positive" if matched_pos else "negative")
            }
            
    return matches
