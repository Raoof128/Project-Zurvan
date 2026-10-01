"""Line protocol between the broker and a sandboxed candidate (contract s3.8).

One JSON object per line in each direction. The broker side is deliberately strict:
the candidate is untrusted code, and its stdout is the only channel back. Anything
outside the closed grammar is a ValueError, which the broker maps to CANDIDATE_FAILED.
"""
from __future__ import annotations

import json
import math

MAX_LINE = 65536
MAX_PAGE_CHARS = 512
MAX_HEADING_CHARS = 1024


def _no_dupes(pairs):
    out = {}
    for key, val in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = val
    return out


def _no_constant(_name):
    raise ValueError("non-finite number")


def _plain_int(value) -> bool:
    return type(value) is int


def _clean_text(value: str) -> bool:
    return not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def encode_query(qid: int, query: str) -> bytes:
    """{"qid":n,"q":"..."}\\n  -- nothing else ever goes to the candidate."""
    if not _plain_int(qid) or qid < 0:
        raise ValueError("qid must be a non-negative int")
    if type(query) is not str:
        raise ValueError("query must be str")
    line = json.dumps({"qid": qid, "q": query}, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(line) > MAX_LINE:
        raise ValueError("query line too long")
    return line


def _check_page(page) -> str:
    if type(page) is not str or not page or len(page) > MAX_PAGE_CHARS:
        raise ValueError("bad page")
    if page.startswith("/") or "\\" in page or not _clean_text(page):
        raise ValueError("bad page")
    if ".." in page.split("/"):
        raise ValueError("bad page")
    return page


def _check_result(item, ) -> dict:
    if type(item) is not dict or set(item) != {"page", "heading", "score"}:
        raise ValueError("bad result")
    page = _check_page(item["page"])
    heading = item["heading"]
    if heading is not None:
        if type(heading) is not str or len(heading) > MAX_HEADING_CHARS or "\x00" in heading:
            raise ValueError("bad heading")
    score = item["score"]
    if type(score) is not float or not math.isfinite(score):
        raise ValueError("bad score")
    return {"page": page, "heading": heading, "score": score}


def decode_response(line: bytes, qid: int, max_results: int) -> dict:
    """Strict parse of one response line. Raises ValueError on anything unexpected."""
    try:
        if type(line) is not bytes or len(line) > MAX_LINE:
            raise ValueError("bad line")
        if not _plain_int(max_results) or max_results < 0:
            raise ValueError("bad max_results")
        body = line[:-1] if line.endswith(b"\n") else line
        if b"\n" in body or b"\r" in body or not body:
            raise ValueError("not exactly one line")
        obj = json.loads(body.decode("utf-8"), object_pairs_hook=_no_dupes, parse_constant=_no_constant)
        if type(obj) is not dict or set(obj) != {"qid", "results", "abstain"}:
            raise ValueError("bad shape")
        if not _plain_int(obj["qid"]) or obj["qid"] != qid:
            raise ValueError("wrong qid")
        if type(obj["abstain"]) is not bool:
            raise ValueError("bad abstain")
        results = obj["results"]
        if type(results) is not list or len(results) > max_results:
            raise ValueError("bad results")
        return {"qid": obj["qid"], "results": [_check_result(r) for r in results], "abstain": obj["abstain"]}
    except ValueError:
        raise
    except Exception as exc:  # RecursionError, UnicodeDecodeError subclasses ValueError; the rest end up here
        raise ValueError("malformed response") from exc
