def prompt_project_brief() -> str:
    return "You are an agent interacting with the Zurvan Local-first AI knowledge engine. Zurvan holds project knowledge securely without internal LLMs. Read AGENTS.md for constraints and use zurvan_search to orient yourself."

def prompt_pre_edit_context() -> str:
    return "Before editing files, run zurvan_context or zurvan_search to understand the decisions, claims, and contradictions surrounding the topic. Ensure you respect past decisions recorded in Zurvan."

def prompt_post_edit_memory() -> str:
    return "If your edit made a new decision, claim, or note, use the write tools (if ZURVAN_MCP_READONLY=0) to record it into Zurvan so future agents will know."

def prompt_research_audit() -> str:
    return "Run a search on the topic you are researching. Then use zurvan_graph_expand to find nearby concepts and notes to build a comprehensive picture before answering."
