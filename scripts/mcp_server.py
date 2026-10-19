from typing import Annotated, Literal, TypedDict

from pydantic import Field

from mcp.server.mcpserver import MCPServer, ResourceSecurity
from mcp.server.caching import CacheHint
from mcp.types import ToolAnnotations

import scripts.mcp_tools as tools
import scripts.mcp_resources as resources
import scripts.mcp_prompts as prompts

ZURVAN_MCP_VERSION = "2.0.0"

# MCP Python SDK v2 (protocol revision 2026-07-28). v1.x is maintenance-only,
# and the resource-template fix below is not expressible on v1 at all.
#
# `cache_hints` implements the spec's new CacheableResult (`ttlMs` +
# `cacheScope`): Zurvan's tool/prompt/resource lists change only when the repo
# is edited, so a client re-listing them on every turn is pure waste. The scope
# is PRIVATE throughout — these listings describe a personal knowledge base and
# must never be cached by a shared intermediary.
_PRIVATE_5_MIN = CacheHint(ttl_ms=300_000, scope="private")

# Sent on `initialize` so an agent without the Zurvan skill still learns the
# workflow (design 2026-10-19 §7A A1). One file, shared with the Rust server,
# so the reference implementation and its replacement say the same thing.
_INSTRUCTIONS = (
    __import__("pathlib").Path(__file__).resolve().parent.parent / "rust" / "zurvan-mcp" / "instructions.md"
).read_text(encoding="utf-8")

mcp = MCPServer(
    "Zurvan",
    title="Zurvan knowledge engine",
    description="Local-first knowledge base: hybrid search, a knowledge graph, and durable project memory.",
    instructions=_INSTRUCTIONS,
    version=ZURVAN_MCP_VERSION,
    dependencies=["mcp"],
    cache_hints={
        "tools/list": _PRIVATE_5_MIN,
        "prompts/list": _PRIVATE_5_MIN,
        "resources/list": _PRIVATE_5_MIN,
        "resources/templates/list": _PRIVATE_5_MIN,
    },
)

# Annotations are how a client decides whether a call needs confirmation.
# None of these tools declared any, so a client had to treat a pure search the
# same as a write. They are hints, not enforcement — the real write gate is
# `enforce_read_only`, which fails closed on ZURVAN_MCP_READONLY.
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
# Additive writes: they append a new page, never overwrite or delete one.
ADDITIVE_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

# ---------------------------------------------------------------------------
# Tools — Read Only
#
# MCPServer exposes each tool's *docstring* and type hints to the calling LLM as
# the tool description and JSON schema. These docstrings are therefore written
# for the model: what the tool does, when to reach for it, what the arguments
# mean, and what comes back.
# ---------------------------------------------------------------------------

@mcp.tool(title="Search Zurvan memory", annotations=READ_ONLY)
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

@mcp.tool(title="Orient on a topic", annotations=READ_ONLY)
def zurvan_orient(
    topic: Annotated[str, Field(description="The subject to orient on.")],
    limit: Annotated[int, Field(description="Maximum number of search results.", ge=1, le=50)] = 10,
) -> str:
    """One call instead of three: index freshness, hybrid search, and the opening of the top pages.

    Returns the "Search index:" freshness line, the same numbered results as
    zurvan_search, then the first 2,000 characters of each of the top three
    distinct result pages. Use it to start on a topic; continue a page with
    zurvan_read_page and the offset its header names.
    """
    return tools.tool_zurvan_orient(topic, limit)

@mcp.tool(title="Build a context bundle", annotations=READ_ONLY)
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

@mcp.tool(title="Read a knowledge-base page", annotations=READ_ONLY)
def zurvan_read_page(
    path: Annotated[str, Field(description='Repo-relative file path (e.g. "wiki/decisions/foo.md" or "docs/API.md"). No absolute paths, no "../"; raw/ is blocked unless ZURVAN_MCP_ALLOW_RAW_READ=1.')],
    offset: Annotated[int, Field(description="Character offset to start reading from. Use the offset the previous call reported to continue a large file.", ge=0)] = 0,
    limit: Annotated[int, Field(description="Maximum characters to return in this slice.", ge=1, le=24000)] = 24000,
) -> str:
    """Read one knowledge-base file, in slices when it is large.

    Use after zurvan_search to open only the page(s) you actually need instead
    of pulling a large context bundle. Returns the raw Markdown (including
    frontmatter). A file bigger than one slice is NOT refused: the result names
    the offset to pass back to read the next part. On unsafe or missing paths it
    returns an actionable error string rather than raising.
    """
    return tools.tool_zurvan_read_page(path, offset, limit)

class GraphStats(TypedDict):
    """Structured node/edge counts for the knowledge graph."""
    nodes: int
    edges: int

@mcp.tool(title="Graph size", annotations=READ_ONLY)
def zurvan_graph_stats() -> GraphStats:
    """Return the knowledge graph size as structured {nodes, edges} counts.

    Use as a quick health/size check of the graph before relying on graph tools.
    Emits machine-readable structured output (plus a JSON text fallback).
    """
    return tools.tool_zurvan_graph_stats_struct()

@mcp.tool(title="Graph neighbours", annotations=READ_ONLY)
def zurvan_graph_neighbours(
    path_or_node_id: Annotated[str, Field(description='A wiki path (e.g. "wiki/decisions/foo.md") or a node id.')],
    limit: Annotated[int, Field(description="Maximum neighbours to return in this page.", ge=1, le=200)] = 25,
    cursor: Annotated[int, Field(description="Offset to resume from; the previous result names the next cursor when more remain.", ge=0)] = 0,
) -> str:
    """List one page of a node's direct (one-hop) graph neighbours.

    Use after a search to see what a specific page links to or is linked from
    (decisions, claims, concepts, sources). For multi-hop expansion use
    zurvan_graph_expand instead.

    Results are PAGED: hub nodes in this graph have thousands of edges, and
    returning them all is a single result larger than most context windows. The
    first line reports the range and total, and names the cursor for the rest.
    """
    return tools.tool_zurvan_graph_neighbours(path_or_node_id, 1, limit, cursor)

@mcp.tool(title="Expand graph neighbourhood", annotations=READ_ONLY)
def zurvan_graph_expand(
    path_or_node_id: Annotated[str, Field(description="A wiki path or node id to expand from.")],
    depth: Annotated[int, Field(description="Number of hops to traverse outward.", ge=1, le=5)] = 2,
    limit: Annotated[int, Field(description="Maximum entries to return in this page.", ge=1, le=200)] = 25,
    cursor: Annotated[int, Field(description="Offset to resume from; the previous result names the next cursor when more remain.", ge=0)] = 0,
) -> str:
    """Expand the graph around a node up to `depth` hops, one page at a time.

    Use to discover nearby concepts and notes when building a comprehensive
    picture. Each line is annotated with its hop distance, node type, and the
    relation that connected it.

    Results are ordered NEAREST-FIRST and PAGED, so a first page is the most
    relevant part of the neighbourhood rather than an arbitrary slice.
    """
    return tools.tool_zurvan_graph_expand(path_or_node_id, depth, limit, cursor)

@mcp.tool(title="Zurvan status", annotations=READ_ONLY)
def zurvan_status() -> str:
    """Report corpus size, search-index freshness, and graph size.

    Call this FIRST in a session. It answers the question every other tool
    depends on: is the index current? A STALE index means search is answering
    from out-of-date content, and a `mock` embedding provider means semantic
    search is meaningless. Also reports whether write tools are enabled.
    """
    return tools.tool_zurvan_status()

@mcp.tool(title="Evaluate retrieval", annotations=READ_ONLY)
def zurvan_eval_search(
    hybrid: Annotated[bool, Field(description="Evaluate the hybrid retriever (vs. pure keyword).")] = True,
    min_top3: Annotated[float, Field(description="Minimum acceptable top-3 accuracy; the report flags a failure below this.", ge=0.0, le=1.0)] = 0.6,
    max_queries: Annotated[int | None, Field(description="Run only the first N gold queries. For a LIVENESS check — the full set is the only valid quality measurement.", ge=1, le=50)] = None,
) -> str:
    """Run the retrieval-quality evaluation against the gold question set.

    Use to confirm search is healthy after reindexing or large wiki changes.
    Reports top-1/top-3 accuracy and mean reciprocal rank. Read-only and safe.
    """
    return tools.tool_zurvan_eval_search(hybrid, min_top3, max_queries)

@mcp.tool(title="Validate gold set", annotations=READ_ONLY)
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

@mcp.tool(title="Record a note", annotations=ADDITIVE_WRITE)
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

@mcp.tool(title="Record a decision", annotations=ADDITIVE_WRITE)
def zurvan_decision_add(
    title: Annotated[str, Field(description='The decision, stated as an outcome (e.g. "Use SQLite FTS5 for search").')],
    reason: Annotated[str, Field(description="The rationale and any considered alternatives.")],
    status: Annotated[Literal["proposed", "accepted", "rejected", "superseded", "deprecated"], Field(description="Lifecycle state of the decision.")],
    tags: Annotated[list[str], Field(description="Topical tags.")],
    project: Annotated[str | None, Field(description=(
        "The decision's SUBJECT project slug (lowercase letters, digits, '-', '_'), "
        "e.g. \"acme\". Written to the page's `project:` field, which gates "
        "publication. Omit only when the server runs inside that project's "
        "registered directory; otherwise it is stamped \"unknown\"."))] = None,
) -> str:
    """Record an architectural/engineering decision (ADR-style) in Zurvan.

    Use whenever a non-trivial choice is made so future agents respect it.
    Name the subject with `project`. Requires write mode (ZURVAN_MCP_READONLY=0).
    """
    return tools.tool_zurvan_decision_add(title, reason, status, tags, project=project)


@mcp.tool(title="Set a decision's project", annotations=ADDITIVE_WRITE)
def zurvan_decision_set_project(
    pages: Annotated[list[str], Field(description="Paths of decision pages under wiki/decisions.")],
    project: Annotated[str, Field(description="The subject project slug to set, e.g. \"acme\".")],
) -> str:
    """Correct the `project:` subject of existing decisions INSIDE the ledger.

    Use this, never a direct file edit, when a decision was stamped "unknown" or
    with the wrong project. The slug is validated; only the frontmatter line
    changes; one call is one ledger revision. Requires write mode.
    """
    return tools.tool_zurvan_decision_set_project(pages, project)

@mcp.tool(title="Record an evidenced claim", annotations=ADDITIVE_WRITE)
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

@mcp.tool(title="Record an open question", annotations=ADDITIVE_WRITE)
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

# `{+path}` is RFC 6570 *reserved* expansion: it matches "/" so nested paths
# work. The old `{path}` used simple expansion, which does NOT match "/", so
# zurvan://file/wiki/decisions/foo.md was an unknown-resource error — and every
# path in this repo is nested, which made the template useful only for
# root-level files. Reserved expansion cannot be registered on SDK v1 at all.
#
# exempt_params turns off the SDK's own traversal rejection for `path` (it
# would otherwise reject the "/" we just enabled). The real gate is unchanged
# and stricter: is_safe_path resolves against the repo root and rejects
# absolute paths, "..", and raw/ (case-insensitively, for macOS).
@mcp.resource(
    "zurvan://file/{+path}",
    description="Read a single safe, repo-relative wiki/docs file (raw/ blocked by default; large files are sliced).",
    security=ResourceSecurity(exempt_params={"path"}),
)
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
