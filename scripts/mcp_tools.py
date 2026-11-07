import os
import io
import sys
import contextlib
import functools
from typing import List, Optional

from scripts.mcp_security import enforce_read_only, is_readable_path
from scripts.context_export import search_memory, export_context
from scripts.graph_query import get_stats, get_neighbours
from scripts.graph_context import expand_graph_context
from scripts.memory import add_decision, add_note, add_claim, add_question


def mcp_operation(func):
    """An MCP tool call is one logical operation, exactly like a CLI invocation.

    Found live in the production ledger on 2026-09-19 at revision 8: four
    mutations for two decisions, every one of them `implicit`, alternating
    `scripts.memory:add_decision` and `scripts.memory:append_to_log`. These
    tools called `memory` directly, so the page and the log append each
    activated their own mutation and a revision was minted BETWEEN them —
    naming a state in which a decision page exists that `wiki/log.md` does not
    mention. That is the half-operation the mutation barrier exists to forbid.

    The boundary belongs HERE and not inside `memory.add_decision`: making a
    library function its own operation would give every caller an independent
    boundary and simply move the split one level down. The hierarchy is

        CLI command / MCP tool / agent operation   <- operation boundary
                        |
                  memory functions
                        |
            safe_write two-phase participant

    and a memory function called with no boundary above it is refused
    (`KnowledgeOperationRequired`, 2026-09-28) rather than recorded as an
    implicit mutation.

    Inside `enforce_read_only`, never outside it: a refused write must not open
    an operation. It plans nothing so no row would be created either way, but
    relying on that would make the ordering load-bearing by accident.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global LAST_MINTED_REVISION
        from scripts import knowledge_revision as kr
        LAST_MINTED_REVISION = None
        with kr.operation(writer="mcp", caller=f"mcp:{func.__name__}") as op:
            result = func(*args, **kwargs)
        # Set by the operation's commit, which runs as the block exits.
        LAST_MINTED_REVISION = getattr(op, "minted_revision", None)
        return result
    return wrapper


#: The revision the last `@mcp_operation` call committed (None if it minted
#: nothing). The write bridge reports this rather than `current()`, which can
#: already include another session's write.
LAST_MINTED_REVISION = None


# Every tool result lands in an agent's context window, so results are bounded
# in two ways: by ITEM COUNT (pagination, with a cursor so nothing is
# unreachable) and by CHARACTERS (a final budget, so one pathological item
# cannot blow the window either).
#
# Both were missing and the research-archive ingest made that dangerous rather
# than theoretical: on the 12,050-node graph, `graph_neighbours` on the hub node
# returned 300,055 characters (~75k tokens) and `graph_expand` at depth 2
# returned 906,501 (~227k tokens) — a single call that exceeds most context
# windows outright.
DEFAULT_PAGE = 25
MAX_PAGE = 200
RESULT_CHAR_BUDGET = 24000  # ~6k tokens


def _protocol_safe(func):
    """Keep a tool's stdout off the wire.

    Under the stdio transport, stdout IS the JSON-RPC channel: one stray
    print() corrupts the protocol stream. The library code behind these tools
    prints freely and legitimately for CLI use — `memory.py` alone has nine
    prints ("Created <path>", "Failed to securely write...") and
    `graph_context` prints "Graph index missing" — so the fix belongs at this
    boundary rather than in every call site.

    The failure was silent, which is what makes it worth a guard rather than a
    patch: the e2e smoke still reported success while the client logged
    "Failed to parse JSONRPC message from server" and skipped the line. Output
    goes to stderr, where stdio clients treat it as server logging.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(sys.stderr):
            return func(*args, **kwargs)
    return wrapper


def _snippet(text: str, limit: int = 200) -> str:
    """Collapse whitespace and truncate to a short, single-line preview."""
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _page_bounds(limit, cursor):
    """Normalise (limit, cursor) into usable ints, clamping hostile values."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE
    limit = max(1, min(limit, MAX_PAGE))
    try:
        offset = max(0, int(cursor or 0))
    except (TypeError, ValueError):
        offset = 0
    return limit, offset


def _paginate(items, limit, cursor):
    """Return (page, header) for one slice, naming the cursor for the next."""
    limit, offset = _page_bounds(limit, cursor)
    total = len(items)
    page = items[offset:offset + limit]
    shown_to = offset + len(page)
    header = f"Showing {offset + 1}-{shown_to} of {total}."
    if shown_to < total:
        header += f" More available: call again with cursor={shown_to}."
    return page, header


def _budget(text: str, budget: int = RESULT_CHAR_BUDGET) -> str:
    """Hard character cap, stated plainly so the agent knows to narrow instead."""
    if len(text) <= budget:
        return text
    kept = text[:budget].rsplit("\n", 1)[0]
    dropped = len(text) - len(kept)
    return (
        f"{kept}\n\n[truncated: {dropped:,} more characters. "
        "Narrow the query or use a smaller limit / the next cursor.]"
    )


def _format_search_results(results) -> str:
    """The numbered result list `zurvan_search` shows (unbudgeted)."""
    if not results:
        return "No matches found."
    output = []
    for i, r in enumerate(results, 1):
        score = r.get("hybrid_score", "N/A")
        score_str = f"{score:.2f}" if isinstance(score, float) else str(score)
        heading = r.get("heading")
        snippet = _snippet(r.get("text", ""))
        line = f"{i}. {r['source_path']} (score {score_str})"
        if heading:
            line += f"\n   heading: {heading}"
        if snippet:
            line += f"\n   {snippet}"
        output.append(line)
    return "\n".join(output)


def _search_or_degrade(query: str, hybrid: bool, limit: int):
    """`_search_internal`, with the Rust server's explicit fallback (design
    2026-10-19 §7A): when semantic search cannot run, return keyword results
    AND the notice saying so, never a silent substitute (F4)."""
    from scripts.context_export import _search_internal
    from scripts.embed import EmbeddingSpaceMismatch, EmbeddingUnavailable
    if not hybrid:
        return _search_internal(query, False, limit), None
    try:
        return _search_internal(query, True, limit), None
    except (EmbeddingUnavailable, EmbeddingSpaceMismatch) as why:
        return (_search_internal(query, False, limit),
                f"Semantic search unavailable: {why} — keyword results only.")


@_protocol_safe
def tool_zurvan_search(query: str, hybrid: bool = True, limit: int = 10) -> str:
    """Searches Zurvan memory."""
    try:
        results, notice = _search_or_degrade(query, hybrid, limit)
        body = _format_search_results(results) if results else "No matches found."
        return _budget(f"{notice}\n{body}" if notice else body)
    except Exception as e:
        return f"Error searching Zurvan for '{query}': {e}"


ORIENT_PAGES = 3
ORIENT_PAGE_CHARS = 2000


def _top_pages_header() -> str:
    """Orient's `## Top pages` header (audit 2026-10-22 F6, plan Task 7).

    Those pages are read from the LIVE files, not from the index generation the
    hits came from, so the header labels them with their own version: the
    ledger revision at the read, `?` when there is no ledger. The Rust server
    (`status::top_pages_header`) emits the identical text.
    """
    try:
        from scripts import knowledge_revision as kr
        rev = str(kr.current().revision)
    except Exception:
        rev = "?"
    return f"## Top pages (live files, ledger rev {rev})"


@_protocol_safe
def tool_zurvan_orient(topic: str, limit: int = 10) -> str:
    """Status line + hybrid results + the opening of the top pages, in one call.

    Design 2026-10-19 §7A A12: the prescribed workflow was three round trips
    (status, search, read_page). Sections are separated by a blank line; each
    page is the first 2,000 characters of one of the top three DISTINCT result
    paths, in rank order, with read_page's slice header naming the next offset.
    """
    try:
        from scripts.agent_workflow import _index_staleness
        from scripts.mcp_resources import read_file_slice

        parts = [f"Search index: {_index_staleness()}"]
        results, notice = _search_or_degrade(topic, True, limit)
        if notice:
            parts[0] += f"\n{notice}"
        parts.append(_format_search_results(results))
        paths = []
        for r in results:
            if r["source_path"] not in paths:
                paths.append(r["source_path"])
            if len(paths) == ORIENT_PAGES:
                break
        if paths:
            parts.append(_top_pages_header())
            parts += [read_file_slice(p, 0, ORIENT_PAGE_CHARS) for p in paths]
        return _budget("\n\n".join(parts))
    except Exception as e:
        return f"Error orienting on '{topic}': {e}"

@_protocol_safe
def tool_zurvan_context(topic: str, hybrid: bool = True, graph: bool = True, limit: int = 10) -> str:
    """Exports a Markdown context bundle."""
    try:
        return _budget(export_context(topic, limit, hybrid, graph, 1))
    except Exception as e:
        return f"Error building context for '{topic}': {e}"

# A page larger than this is delivered in slices rather than refused.
PAGE_SLICE_CHARS = 24000


@_protocol_safe
def tool_zurvan_read_page(path: str, offset: int = 0, limit: int = PAGE_SLICE_CHARS) -> str:
    """Read one safe, repo-relative file, in slices for large ones.

    Goes through the resource_file safety gate (no absolute paths, no
    traversal, raw/ blocked by default). Exposed as a tool because many MCP
    clients never fetch resources, which forces search/context to dump full
    text up front; with this, search stays compact and the agent pulls only the
    pages it needs.

    Large files used to return "Error: File too large (> 256 KB)" and nothing
    else — wiki/index.md grew past the cap and became the one page an agent
    could not open at all. A size limit should bound what arrives per call, not
    make the content unreachable, so oversized files are now sliced with a
    byte offset the agent can advance.
    """
    from scripts.mcp_resources import resource_file, read_file_slice

    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(int(limit), PAGE_SLICE_CHARS))
    except (TypeError, ValueError):
        limit = PAGE_SLICE_CHARS

    if offset == 0:
        content = resource_file(path)
        if not content.startswith("Error: File too large"):
            return _budget(content, PAGE_SLICE_CHARS) if len(content) > PAGE_SLICE_CHARS else content
    return read_file_slice(path, offset, limit)


@_protocol_safe
def tool_zurvan_graph_stats() -> str:
    """Returns graph stats as a human-readable string."""
    try:
        stats = get_stats()
        return f"Graph stats: {stats['nodes']} nodes, {stats['edges']} edges"
    except Exception as e:
        return f"Error: {str(e)}"

@_protocol_safe
def tool_zurvan_graph_stats_struct() -> dict:
    """Returns graph stats as a structured {nodes, edges} dict for the MCP
    structured-output channel. Raises on failure so the MCP layer surfaces a
    proper tool error rather than an object that violates the output schema."""
    stats = get_stats()
    return {"nodes": int(stats["nodes"]), "edges": int(stats["edges"])}

@_protocol_safe
def tool_zurvan_graph_neighbours(path_or_node_id: str, depth: int = 1,
                                 limit: int = DEFAULT_PAGE, cursor: int = 0) -> str:
    """One page of a node's neighbours, newest-style paging via `cursor`."""
    try:
        neighbours = get_neighbours(path_or_node_id)
        if not neighbours:
            return (
                f"No neighbours found for '{path_or_node_id}'. If the node should exist, "
                "check the path with zurvan_search, or rebuild the graph "
                "(`zurvan graph rebuild`)."
            )
        page, header = _paginate(neighbours, limit, cursor)
        lines = [f"{n['from_title']} --[{n['edge_type']}]--> {n['to_title']}" for n in page]
        return _budget(header + "\n" + "\n".join(lines))
    except Exception as e:
        return f"Error reading graph neighbours for '{path_or_node_id}': {e}"

@_protocol_safe
def tool_zurvan_graph_expand(path_or_node_id: str, depth: int = 2,
                             limit: int = DEFAULT_PAGE, cursor: int = 0) -> str:
    """One page of the expanded graph neighbourhood, ordered nearest-first."""
    try:
        items = expand_graph_context([path_or_node_id], depth)
        if not items:
            return (
                f"No graph neighbours found for '{path_or_node_id}'. Check the path with "
                "zurvan_search, or rebuild the graph (`zurvan graph rebuild`)."
            )
        # Nearest first, so a truncated page is still the most relevant part of
        # the neighbourhood rather than an arbitrary slice.
        items = sorted(items, key=lambda i: i.get("depth", 0))
        page, header = _paginate(items, limit, cursor)
        lines = [f"[{i['depth']}] {i['title']} ({i['node_type']}) - {i['relation']}" for i in page]
        return _budget(header + "\n" + "\n".join(lines))
    except Exception as e:
        return f"Error expanding graph around '{path_or_node_id}': {e}"

@_protocol_safe
def tool_zurvan_eval_search(hybrid: bool = True, min_top3: float = 0.6,
                            max_queries: int | None = None) -> str:
    """Evaluates search retrieval against gold set.

    Runs in-process (no subprocess / no `python` interpreter assumption) and
    captures stdout so the eval's print() output never corrupts the MCP stdio
    JSON-RPC stream. eval_search calls sys.exit() on a failing threshold, so
    SystemExit is caught and its captured output returned instead."""
    from scripts.eval_search import run_search_evaluation
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            run_search_evaluation(hybrid=hybrid, min_top3=min_top3, max_queries=max_queries)
    except SystemExit:
        pass  # threshold/validation failure — message already captured in buf
    except Exception as e:
        return f"Error: {str(e)}"
    return buf.getvalue().strip() or "No output."

@_protocol_safe
def tool_zurvan_validate_gold() -> str:
    """Validates gold dataset (in-process, stdout captured)."""
    from scripts.eval_search import validate_gold_dataset, DEFAULT_GOLD
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            validate_gold_dataset(DEFAULT_GOLD)
    except SystemExit:
        pass
    except Exception as e:
        return f"Error: {str(e)}"
    return buf.getvalue().strip() or "No output."

@_protocol_safe
@enforce_read_only
@mcp_operation
def tool_zurvan_remember(type: str, title: str, body: str, tags: List[str]) -> str:
    """Remembers a project note. The `type` label is preserved as the first tag
    so it is not silently discarded (use zurvan_decision_add / zurvan_claim_add /
    zurvan_question_add for those structured node kinds)."""
    tags = list(tags or [])
    if type and type not in tags:
        tags = [type] + tags
    if add_note(title, body, tags):
        return f"Note added successfully ({type}): {title}"
    return "Failed to add note."

@_protocol_safe
@enforce_read_only
@mcp_operation
def tool_zurvan_decision_add(title: str, reason: str, status: str, tags: List[str],
                             project: str | None = None) -> str:
    """Manages decisions.

    `project` names the decision's SUBJECT and lands in the `project:` field that
    `public_repo_guard` gates publication on. Left unset it is resolved from the
    working directory, never from whichever workspace is globally selected.
    """
    from scripts.memory import InvalidProjectError, resolve_project, write_decision
    try:
        path = write_decision(title, reason, status, tags, project=project)
    except InvalidProjectError as exc:
        return f"Error: {exc}"
    if path is None:
        return "Failed to add decision."
    subject = resolve_project(project)
    message = f"Decision added successfully (project: {subject}): {title}"
    if subject == "unknown":
        # Said in the result, where an MCP caller sees it (stderr does not reach
        # one). Silence here is what sent agents to `sed -i` on 2026-10-01.
        message += ("\nWarning: project stamped \"unknown\" (none was named and the "
                    "server's working directory is in no registered project). Fix it "
                    "inside the ledger: zurvan_decision_set_project(pages=["
                    f"{path!r}], project=\"<slug>\"), and pass project= next time. "
                    "Never edit wiki/decisions directly.")
    return message


@_protocol_safe
@enforce_read_only
@mcp_operation
def tool_zurvan_decision_set_project(pages: List[str], project: str) -> str:
    """Set the `project:` subject of existing decision pages inside the ledger."""
    from scripts.memory import InvalidProjectError, set_decision_project
    try:
        changed = set_decision_project(pages, project)
    except (InvalidProjectError, ValueError) as exc:
        return f"Error: {exc} Nothing was written."
    return "\n".join([f"Set project to {project} on {len(changed)} page(s):"]
                     + [f"- {p}: was {old}" for p, old in changed])

CLAIM_SOURCE_ERROR = "Error: Source path fails safety checks (e.g. no absolute paths, no ../, no raw/ without flag)."


@_protocol_safe
@enforce_read_only
@mcp_operation
def tool_zurvan_claim_add(text: str, source: str, evidence: str, confidence: str, tags: List[str]) -> str:
    """Manages claims."""
    # The claim reads `source` to verify the evidence, so the source is held
    # to the read allow-list: `is_safe_path` alone let a claim copy
    # `.git/config` or a `sources/` document into a readable claim page.
    allow_raw = os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0") == "1"
    if not is_readable_path(source, allow_raw=allow_raw):
        return CLAIM_SOURCE_ERROR
    
    if add_claim(text, source, evidence, confidence, tags):
        return f"Claim added successfully: {text}"
    # "Error" prefix: the A5 error flag (and the Rust server's isError) keys on
    # it, so a refused claim is reported as a refusal, not a success.
    return "Error: failed to add claim. Ensure evidence is non-empty and exists verbatim in the source file."

@_protocol_safe
@enforce_read_only
@mcp_operation
def tool_zurvan_question_add(question: str, reason: str, tags: List[str]) -> str:
    """Manages open questions."""
    if add_question(question, reason, tags):
        return f"Question added successfully: {question}"
    return "Failed to add question."


@_protocol_safe
def tool_zurvan_status() -> str:
    """One cheap orientation call: corpus size, index freshness, graph size.

    The skill tells agents to check index staleness before trusting search
    results, but that check only existed on the CLI (`zurvan agent prime`), so
    an MCP-only agent had no way to know it was querying a stale index. This
    surfaces it in the protocol the agent is actually speaking.
    """
    import sqlite3

    from scripts.config import PROJECT_ROOT, get_search_db_path

    lines = []
    try:
        from scripts.agent_workflow import _index_staleness
        lines.append(f"Search index: {_index_staleness()}")
    except Exception as e:
        lines.append(f"Search index: staleness unknown ({e})")

    db = str(get_search_db_path())
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            files = conn.execute("SELECT count(DISTINCT source_path) FROM chunks").fetchone()[0]
            row = conn.execute("SELECT provider, model FROM embeddings LIMIT 1").fetchone()
        finally:
            conn.close()
        lines.append(f"Indexed: {chunks:,} chunks across {files:,} files")
        if row:
            lines.append(f"Embeddings: {row[0]}/{row[1]}")
            if row[0] == "mock":
                lines.append(
                    "WARNING: the index holds MOCK embeddings — semantic search is "
                    "meaningless until it is rebuilt with a real provider."
                )
    except sqlite3.Error:
        lines.append(f"Indexed: no search index at {db} — run `zurvan index search`.")

    try:
        stats = get_stats()
        lines.append(f"Graph: {stats['nodes']:,} nodes / {stats['edges']:,} edges")
    except Exception as e:
        lines.append(f"Graph: unavailable ({e})")

    sources = PROJECT_ROOT / "wiki" / "sources"
    if sources.exists():
        lines.append(f"Source pages: {sum(1 for f in os.listdir(sources) if f.endswith('.md')):,}")

    readonly = os.environ.get("ZURVAN_MCP_READONLY", "1").strip() != "0"
    lines.append(f"Write tools: {'BLOCKED (read-only mode)' if readonly else 'enabled'}")
    return "\n".join(lines)
