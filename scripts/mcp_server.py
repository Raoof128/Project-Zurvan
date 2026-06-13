from typing import Literal

from mcp.server.fastmcp import FastMCP
import scripts.mcp_tools as tools
import scripts.mcp_resources as resources
import scripts.mcp_prompts as prompts

mcp = FastMCP("Zurvan", dependencies=["mcp"])

# ---------------------------------------------------------------------------
# Tools — Read Only
#
# FastMCP exposes each tool's *docstring* and type hints to the calling LLM as
# the tool description and JSON schema. These docstrings are therefore written
# for the model: what the tool does, when to reach for it, what the arguments
# mean, and what comes back.
# ---------------------------------------------------------------------------

@mcp.tool()
def zurvan_search(query: str, hybrid: bool = True, limit: int = 10) -> str:
    """Search the Zurvan knowledge base and return ranked matches.

    Use this first to orient yourself on any topic before answering or editing.
    Returns a numbered list of matching wiki pages, each with a relevance score
    and (in hybrid mode) the matched heading plus a short text snippet, so you
    can judge relevance without opening every file.

    Args:
        query: Natural-language or keyword query.
        hybrid: True (default) blends FTS5 keyword scoring with local embeddings;
            False uses a pure keyword scan. Prefer hybrid for conceptual queries.
        limit: Maximum number of results to return (default 10).
    """
    return tools.tool_zurvan_search(query, hybrid, limit)

@mcp.tool()
def zurvan_context(topic: str, hybrid: bool = True, graph: bool = True, limit: int = 10) -> str:
    """Build a ready-to-read Markdown context bundle for a topic.

    Higher-level than zurvan_search: it gathers the top matches AND (when
    graph=True) their graph neighbours into a single Markdown digest you can
    read in one shot. Use this when you need to understand the decisions,
    claims, and related concepts around a topic before making a change.

    Args:
        topic: The subject to assemble context for.
        hybrid: Use hybrid retrieval (default True).
        graph: Also pull in linked graph neighbours of the top hits (default True).
        limit: Maximum number of seed matches (default 10).
    """
    return tools.tool_zurvan_context(topic, hybrid, graph, limit)

@mcp.tool()
def zurvan_graph_stats() -> str:
    """Return a one-line summary of the knowledge graph (node and edge counts).

    Use as a quick health/size check of the graph before relying on graph tools.
    """
    return tools.tool_zurvan_graph_stats()

@mcp.tool()
def zurvan_graph_neighbours(path_or_node_id: str) -> str:
    """List the direct (one-hop) graph neighbours of a node.

    Use after a search to see what a specific page links to or is linked from
    (decisions, claims, concepts, sources). For multi-hop expansion use
    zurvan_graph_expand instead.

    Args:
        path_or_node_id: A wiki path (e.g. "wiki/decisions/foo.md") or a node id.
    """
    return tools.tool_zurvan_graph_neighbours(path_or_node_id)

@mcp.tool()
def zurvan_graph_expand(path_or_node_id: str, depth: int = 2) -> str:
    """Expand the graph around a node up to `depth` hops for broad context.

    Use to discover nearby concepts and notes when building a comprehensive
    picture. Each line is annotated with its hop distance, node type, and the
    relation that connected it.

    Args:
        path_or_node_id: A wiki path or node id to expand from.
        depth: Number of hops to traverse (default 2).
    """
    return tools.tool_zurvan_graph_expand(path_or_node_id, depth)

@mcp.tool()
def zurvan_eval_search(hybrid: bool = True, min_top3: float = 0.6) -> str:
    """Run the retrieval-quality evaluation against the gold question set.

    Use to confirm search is healthy after reindexing or large wiki changes.
    Reports top-1/top-3 accuracy and mean reciprocal rank. Read-only and safe.

    Args:
        hybrid: Evaluate the hybrid retriever (default True).
        min_top3: Minimum acceptable top-3 accuracy; the report flags a failure
            below this threshold (default 0.6).
    """
    return tools.tool_zurvan_eval_search(hybrid, min_top3)

@mcp.tool()
def zurvan_validate_gold() -> str:
    """Validate that every path referenced by the gold question set still exists.

    Use to catch a stale evaluation set after files are renamed or removed.
    Read-only and safe.
    """
    return tools.tool_zurvan_validate_gold()

# ---------------------------------------------------------------------------
# Tools — Write
#
# All write tools are blocked unless the server is started with
# ZURVAN_MCP_READONLY=0. When blocked they return a clear, non-fatal message.
# ---------------------------------------------------------------------------

@mcp.tool()
def zurvan_remember(
    type: Literal["note", "insight", "reference", "todo", "risk"],
    title: str,
    body: str,
    tags: list[str],
) -> str:
    """Store a free-form project note in Zurvan memory for future agents.

    Use for general observations that do not fit the structured node kinds. For
    those, prefer the dedicated tools: zurvan_decision_add (architectural
    choices), zurvan_claim_add (evidence-backed facts), zurvan_question_add
    (open questions). Requires write mode (ZURVAN_MCP_READONLY=0).

    Args:
        type: Category label for the note; preserved as its first tag.
        title: Short, descriptive title.
        body: The note content (Markdown allowed).
        tags: Topical tags to aid later retrieval.
    """
    return tools.tool_zurvan_remember(type, title, body, tags)

@mcp.tool()
def zurvan_decision_add(
    title: str,
    reason: str,
    status: Literal["proposed", "accepted", "rejected", "superseded", "deprecated"],
    tags: list[str],
) -> str:
    """Record an architectural/engineering decision (ADR-style) in Zurvan.

    Use whenever a non-trivial choice is made so future agents respect it.
    Requires write mode (ZURVAN_MCP_READONLY=0).

    Args:
        title: The decision, stated as an outcome (e.g. "Use SQLite FTS5 for search").
        reason: The rationale and any considered alternatives.
        status: Lifecycle state of the decision.
        tags: Topical tags.
    """
    return tools.tool_zurvan_decision_add(title, reason, status, tags)

@mcp.tool()
def zurvan_claim_add(
    text: str,
    source: str,
    evidence: str,
    confidence: Literal["low", "medium", "high"],
    tags: list[str],
) -> str:
    """Record an evidence-backed claim, anchored to a source file.

    The `evidence` text MUST appear verbatim in the `source` file or the claim
    is rejected (no fabricated citations). `source` must be a safe repo-relative
    path. Requires write mode (ZURVAN_MCP_READONLY=0).

    Args:
        text: The claim being asserted.
        source: Repo-relative path to the supporting file (no absolute paths,
            no "../", no raw/ unless ZURVAN_MCP_ALLOW_RAW_READ=1).
        evidence: An exact quote from the source file that supports the claim.
        confidence: How strongly the evidence supports the claim.
        tags: Topical tags.
    """
    return tools.tool_zurvan_claim_add(text, source, evidence, confidence, tags)

@mcp.tool()
def zurvan_question_add(question: str, reason: str, tags: list[str]) -> str:
    """Record an open question / unknown for later investigation.

    Use to capture gaps surfaced during research or editing. Requires write
    mode (ZURVAN_MCP_READONLY=0).

    Args:
        question: The open question.
        reason: Why it matters / what is blocked on it.
        tags: Topical tags.
    """
    return tools.tool_zurvan_question_add(question, reason, tags)

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("zurvan://wiki/index", description="The wiki index — the table of contents for the knowledge base.")
def resource_wiki_index() -> str:
    return resources.resource_wiki_index()

@mcp.resource("zurvan://wiki/log", description="Chronological activity log of ingestions and knowledge updates.")
def resource_wiki_log() -> str:
    return resources.resource_wiki_log()

@mcp.resource("zurvan://wiki/overview", description="High-level overview of the project's knowledge base.")
def resource_wiki_overview() -> str:
    return resources.resource_wiki_overview()

@mcp.resource("zurvan://wiki/open-questions", description="Currently tracked open questions and unknowns.")
def resource_wiki_open_questions() -> str:
    return resources.resource_wiki_open_questions()

@mcp.resource("zurvan://graph/stats", description="Knowledge graph size (node and edge counts).")
def resource_graph_stats() -> str:
    return resources.resource_graph_stats()

@mcp.resource("zurvan://eval/baseline", description="The retrieval evaluation baseline (eval/README.md).")
def resource_eval_baseline() -> str:
    return resources.resource_eval_baseline()

@mcp.resource("zurvan://file/{path}", description="Read a single safe, repo-relative wiki/docs file (<=256 KB; raw/ blocked by default).")
def resource_file(path: str) -> str:
    return resources.resource_file(path)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def zurvan_project_brief() -> str:
    """Orientation brief: how to interact with the Zurvan knowledge engine."""
    return prompts.prompt_project_brief()

@mcp.prompt()
def zurvan_pre_edit_context() -> str:
    """Checklist to gather relevant context before editing files."""
    return prompts.prompt_pre_edit_context()

@mcp.prompt()
def zurvan_post_edit_memory() -> str:
    """Reminder to persist new decisions/claims/notes after an edit."""
    return prompts.prompt_post_edit_memory()

@mcp.prompt()
def zurvan_research_audit() -> str:
    """Workflow for researching a topic thoroughly using search + graph expand."""
    return prompts.prompt_research_audit()

if __name__ == "__main__":
    mcp.run(transport="stdio")
