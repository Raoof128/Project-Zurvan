from mcp.server.fastmcp import FastMCP
import scripts.mcp_tools as tools
import scripts.mcp_resources as resources
import scripts.mcp_prompts as prompts

mcp = FastMCP("Zurvan", dependencies=["mcp"])

# Tools - Read Only
@mcp.tool()
def zurvan_search(query: str, hybrid: bool = True, limit: int = 10) -> str:
    return tools.tool_zurvan_search(query, hybrid, limit)

@mcp.tool()
def zurvan_context(topic: str, hybrid: bool = True, graph: bool = True, limit: int = 10) -> str:
    return tools.tool_zurvan_context(topic, hybrid, graph, limit)

@mcp.tool()
def zurvan_graph_stats() -> str:
    return tools.tool_zurvan_graph_stats()

@mcp.tool()
def zurvan_graph_neighbours(path_or_node_id: str, depth: int = 1) -> str:
    return tools.tool_zurvan_graph_neighbours(path_or_node_id, depth)

@mcp.tool()
def zurvan_graph_expand(path_or_node_id: str, depth: int = 2) -> str:
    return tools.tool_zurvan_graph_expand(path_or_node_id, depth)

@mcp.tool()
def zurvan_eval_search(hybrid: bool = True, min_top3: float = 0.6) -> str:
    return tools.tool_zurvan_eval_search(hybrid, min_top3)

@mcp.tool()
def zurvan_validate_gold() -> str:
    return tools.tool_zurvan_validate_gold()

# Tools - Write
@mcp.tool()
def zurvan_remember(type: str, title: str, body: str, tags: list[str]) -> str:
    return tools.tool_zurvan_remember(type, title, body, tags)

@mcp.tool()
def zurvan_decision_add(title: str, reason: str, status: str, tags: list[str]) -> str:
    return tools.tool_zurvan_decision_add(title, reason, status, tags)

@mcp.tool()
def zurvan_claim_add(text: str, source: str, evidence: str, confidence: str, tags: list[str]) -> str:
    return tools.tool_zurvan_claim_add(text, source, evidence, confidence, tags)

@mcp.tool()
def zurvan_question_add(question: str, reason: str, tags: list[str]) -> str:
    return tools.tool_zurvan_question_add(question, reason, tags)

# Resources
@mcp.resource("zurvan://wiki/index")
def resource_wiki_index() -> str:
    return resources.resource_wiki_index()

@mcp.resource("zurvan://wiki/log")
def resource_wiki_log() -> str:
    return resources.resource_wiki_log()

@mcp.resource("zurvan://wiki/overview")
def resource_wiki_overview() -> str:
    return resources.resource_wiki_overview()

@mcp.resource("zurvan://wiki/open-questions")
def resource_wiki_open_questions() -> str:
    return resources.resource_wiki_open_questions()

@mcp.resource("zurvan://graph/stats")
def resource_graph_stats() -> str:
    return resources.resource_graph_stats()

@mcp.resource("zurvan://eval/baseline")
def resource_eval_baseline() -> str:
    return resources.resource_eval_baseline()

@mcp.resource("zurvan://file/{path}")
def resource_file(path: str) -> str:
    return resources.resource_file(path)

# Prompts
@mcp.prompt()
def zurvan_project_brief() -> str:
    return prompts.prompt_project_brief()

@mcp.prompt()
def zurvan_pre_edit_context() -> str:
    return prompts.prompt_pre_edit_context()

@mcp.prompt()
def zurvan_post_edit_memory() -> str:
    return prompts.prompt_post_edit_memory()

@mcp.prompt()
def zurvan_research_audit() -> str:
    return prompts.prompt_research_audit()

if __name__ == "__main__":
    mcp.run(transport="stdio")
