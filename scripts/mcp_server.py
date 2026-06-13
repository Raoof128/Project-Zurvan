from typing import Annotated, Literal, TypedDict

from pydantic import Field

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
def zurvan_search(
    query: Annotated[str, Field(description="Natural-language or keyword query.")],
    hybrid: Annotated[bool, Field(description="Blend FTS5 keyword scoring with local embeddings (best for conceptual queries). False = pure keyword scan.")] = True,
    limit: Annotated[int, Field(description="Maximum number of results to return.", ge=1, le=50)] = 10,
) -> str:
    """Search the Zurvan knowledge base and return ranked matches.

    Use this first to orient yourself on any topic before answering or editing.
    Returns a numbered list of matching wiki pages, each with a relevance score
    and (in hybrid mode) the matched heading plus a short text snippet, so you
    can judge relevance without opening every file.
    """
    return tools.tool_zurvan_search(query, hybrid, limit)

@mcp.tool()
def zurvan_context(
    topic: Annotated[str, Field(description="The subject to assemble context for.")],
    hybrid: Annotated[bool, Field(description="Use hybrid (keyword + embedding) retrieval.")] = True,
    graph: Annotated[bool, Field(description="Also pull in linked graph neighbours of the top hits.")] = True,
    limit: Annotated[int, Field(description="Maximum number of seed matches.", ge=1, le=50)] = 10,
) -> str:
    """Build a ready-to-read Markdown context bundle for a topic.

    Higher-level than zurvan_search: it gathers the top matches AND (when
    graph=True) their graph neighbours into a single Markdown digest you can
    read in one shot. Use this when you need to understand the decisions,
    claims, and related concepts around a topic before making a change.
    """
    return tools.tool_zurvan_context(topic, hybrid, graph, limit)

class GraphStats(TypedDict):
    """Structured node/edge counts for the knowledge graph."""
    nodes: int
    edges: int

@mcp.tool()
def zurvan_graph_stats() -> GraphStats:
    """Return the knowledge graph size as structured {nodes, edges} counts.

    Use as a quick health/size check of the graph before relying on graph tools.
    Emits machine-readable structured output (plus a JSON text fallback).
    """
    return tools.tool_zurvan_graph_stats_struct()

@mcp.tool()
def zurvan_graph_neighbours(
    path_or_node_id: Annotated[str, Field(description='A wiki path (e.g. "wiki/decisions/foo.md") or a node id.')],
) -> str:
    """List the direct (one-hop) graph neighbours of a node.

    Use after a search to see what a specific page links to or is linked from
    (decisions, claims, concepts, sources). For multi-hop expansion use
    zurvan_graph_expand instead.
    """
    return tools.tool_zurvan_graph_neighbours(path_or_node_id)

@mcp.tool()
def zurvan_graph_expand(
    path_or_node_id: Annotated[str, Field(description="A wiki path or node id to expand from.")],
    depth: Annotated[int, Field(description="Number of hops to traverse outward.", ge=1, le=5)] = 2,
) -> str:
    """Expand the graph around a node up to `depth` hops for broad context.

    Use to discover nearby concepts and notes when building a comprehensive
    picture. Each line is annotated with its hop distance, node type, and the
    relation that connected it.
    """
    return tools.tool_zurvan_graph_expand(path_or_node_id, depth)

@mcp.tool()
def zurvan_eval_search(
    hybrid: Annotated[bool, Field(description="Evaluate the hybrid retriever (vs. pure keyword).")] = True,
    min_top3: Annotated[float, Field(description="Minimum acceptable top-3 accuracy; the report flags a failure below this.", ge=0.0, le=1.0)] = 0.6,
) -> str:
    """Run the retrieval-quality evaluation against the gold question set.

    Use to confirm search is healthy after reindexing or large wiki changes.
    Reports top-1/top-3 accuracy and mean reciprocal rank. Read-only and safe.
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
    type: Annotated[Literal["note", "insight", "reference", "todo", "risk"], Field(description="Category label for the note; preserved as its first tag.")],
    title: Annotated[str, Field(description="Short, descriptive title.")],
    body: Annotated[str, Field(description="The note content (Markdown allowed).")],
    tags: Annotated[list[str], Field(description="Topical tags to aid later retrieval.")],
) -> str:
    """Store a free-form project note in Zurvan memory for future agents.

    Use for general observations that do not fit the structured node kinds. For
    those, prefer the dedicated tools: zurvan_decision_add (architectural
    choices), zurvan_claim_add (evidence-backed facts), zurvan_question_add
    (open questions). Requires write mode (ZURVAN_MCP_READONLY=0).
    """
    return tools.tool_zurvan_remember(type, title, body, tags)

@mcp.tool()
def zurvan_decision_add(
    title: Annotated[str, Field(description='The decision, stated as an outcome (e.g. "Use SQLite FTS5 for search").')],
    reason: Annotated[str, Field(description="The rationale and any considered alternatives.")],
    status: Annotated[Literal["proposed", "accepted", "rejected", "superseded", "deprecated"], Field(description="Lifecycle state of the decision.")],
    tags: Annotated[list[str], Field(description="Topical tags.")],
) -> str:
    """Record an architectural/engineering decision (ADR-style) in Zurvan.

    Use whenever a non-trivial choice is made so future agents respect it.
    Requires write mode (ZURVAN_MCP_READONLY=0).
    """
    return tools.tool_zurvan_decision_add(title, reason, status, tags)

@mcp.tool()
def zurvan_claim_add(
    text: Annotated[str, Field(description="The claim being asserted.")],
    source: Annotated[str, Field(description='Repo-relative path to the supporting file (no absolute paths, no "../", no raw/ unless ZURVAN_MCP_ALLOW_RAW_READ=1).')],
    evidence: Annotated[str, Field(description="An exact quote from the source file that supports the claim (must appear verbatim).")],
    confidence: Annotated[Literal["low", "medium", "high"], Field(description="How strongly the evidence supports the claim.")],
    tags: Annotated[list[str], Field(description="Topical tags.")],
) -> str:
    """Record an evidence-backed claim, anchored to a source file.

    The `evidence` text MUST appear verbatim in the `source` file or the claim
    is rejected (no fabricated citations). `source` must be a safe repo-relative
    path. Requires write mode (ZURVAN_MCP_READONLY=0).
    """
    return tools.tool_zurvan_claim_add(text, source, evidence, confidence, tags)

@mcp.tool()
def zurvan_question_add(
    question: Annotated[str, Field(description="The open question.")],
    reason: Annotated[str, Field(description="Why it matters / what is blocked on it.")],
    tags: Annotated[list[str], Field(description="Topical tags.")],
) -> str:
    """Record an open question / unknown for later investigation.

    Use to capture gaps surfaced during research or editing. Requires write
    mode (ZURVAN_MCP_READONLY=0).
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
