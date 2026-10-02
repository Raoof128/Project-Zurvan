"""Default Z1b candidate adapter (DEV side, shipped inside the candidate commit).

Reads protocol lines on stdin, answers with hybrid_search over the corpus index, and
writes protocol lines on stdout. The whole loop runs inside trace_writer's
protected_eval_context, so any non-protected trace write raises ProtectedTraceError.

Environment from the sealed runner: ZURVAN_SEARCH_DB (required), ZURVAN_CORPUS, ZURVAN_ROOT,
HF_HUB_OFFLINE=1, HF_HOME. Nothing is guessed: a missing index is a hard exit.
"""
import json
import os
import posixpath
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.trace_writer import protected_eval_context  # noqa: E402

MAX_LINE = 65536
TOP_K = 10
MAX_PAGE_CHARS = 512


def chunk_to_page(source_path, wiki_root):
    """source_path relative to the corpus wiki root, or None when it cannot be made a safe relative page."""
    if type(source_path) is not str or not source_path or len(source_path) > MAX_PAGE_CHARS:
        return None
    if source_path.startswith("/") or "\x00" in source_path or "\\" in source_path:
        return None
    parts = source_path.split("/")
    if ".." in parts:
        return None
    prefix = (wiki_root or "").strip("/")
    if prefix and source_path.startswith(prefix + "/"):
        source_path = source_path[len(prefix) + 1:]
    page = posixpath.normpath(source_path)
    if page in ("", ".") or page.startswith("../") or page == "..":
        return None
    return page


def _adapter_parse_request(line):
    """Strict {"qid": int, "q": str}; returns (qid, q) or None."""
    try:
        def no_dupes(pairs):
            out = {}
            for key, val in pairs:
                if key in out:
                    raise ValueError("duplicate key")
                out[key] = val
            return out

        obj = json.loads(line.decode("utf-8"), object_pairs_hook=no_dupes)
    except (ValueError, RecursionError):
        return None
    if type(obj) is not dict or set(obj) != {"qid", "q"}:
        return None
    qid, query = obj["qid"], obj["q"]
    if type(qid) is not int or qid < 0 or type(query) is not str:
        return None
    return qid, query


def _adapter_answer(qid, chunks, wiki_root, limit):
    results = []
    for chunk in list(chunks or [])[:limit * 4]:
        page = chunk_to_page(chunk.get("source_path"), wiki_root)
        if page is None:
            continue
        heading = chunk.get("heading")
        results.append({"page": page, "heading": heading if isinstance(heading, str) else None,
                        "score": float(chunk.get("hybrid_score", 0.0))})
        if len(results) == limit:
            break
    return (json.dumps({"qid": qid, "results": results, "abstain": False},
                       ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def serve(stdin, stdout, search_fn, wiki_root="", limit=TOP_K):
    """Protocol loop. Returns 0 on clean EOF, 2 on a malformed request (nothing is answered for it).

    wiki_root defaults to "" (ruling R6): chunks.source_path is passed through UNCHANGED, because the
    sealed grader's path space is the store-relative path exactly as the index stores it."""
    with protected_eval_context():
        while True:
            line = stdin.readline(MAX_LINE + 1)
            if not line:
                return 0
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                return 2
            parsed = _adapter_parse_request(line)
            if parsed is None:
                return 2
            qid, query = parsed
            stdout.write(_adapter_answer(qid, search_fn(query, limit), wiki_root, limit))
            stdout.flush()


def adapter_main(argv=None):
    db = os.environ.get("ZURVAN_SEARCH_DB")
    if not db or not os.path.exists(db):
        sys.exit(2)
    # the protocol owns stdout; anything a library prints must not corrupt it
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    from scripts.hybrid_search import search_hybrid

    def search(query, limit):
        return search_hybrid(query, limit=limit, db_path=db)

    sys.exit(serve(sys.stdin.buffer, out, search))


if __name__ == "__main__":
    adapter_main()
