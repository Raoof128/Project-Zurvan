#!/usr/bin/env python3
"""What "the search index is healthy" means, in one place.

Until 2026-09-17 the answer was a row count, and a row count cannot see either
of the two ways this index has actually broken:

* **A poisoned index agrees with itself.** 184,231 chunks, 184,231 embeddings,
  every table consistent — and every vector a SHA-256 hash, because a rebuild
  ran where `ZURVAN_EMBED_PROVIDER` was unset. Semantic recall was 0%. Counts
  cannot tell you that; the `provider` column can.
* **A truncated index looks like a small corpus.** 2,000 chunks from 59 of
  4,418 sources, internally perfect, answering queries exactly like a complete
  index would. Coverage, not consistency, is what catches that.

So health here is: structural integrity, agreement between chunks/FTS/embeddings,
a SINGLE embedding space, that space being the one expected, and enough of the
corpus present. Anything that opens the index for a real answer should be able
to ask this module whether the answer means anything.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# The production embedding space. These are constants rather than environment
# reads on purpose: the environment is precisely what went wrong. A canonical
# rebuild must produce THIS space or fail, so that a missing variable or a
# SentenceTransformers import error can never be absorbed as a valid index.
CANONICAL_PROVIDER = "sentence_transformers"
CANONICAL_MODEL = "all-MiniLM-L6-v2"
CANONICAL_DIMENSION = 384


@dataclass
class HealthReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        head = "VERDICT: healthy" if self.ok else "VERDICT: UNHEALTHY"
        lines = [head]
        for key in ("chunks", "embeddings", "fts_rows", "sources", "spaces"):
            if key in self.stats:
                lines.append(f"  {key}: {self.stats[key]}")
        for problem in self.problems:
            lines.append(f"  ✗ {problem}")
        return "\n".join(lines)


def canonical_db_path() -> Path:
    """The production index path, ignoring any test redirect."""
    from scripts.config import PROJECT_ROOT
    return (PROJECT_ROOT / "data" / "search.sqlite").resolve()


def is_canonical_index(db_path) -> bool:
    """True only for the real production corpus.

    `ZURVAN_SEARCH_DB` marks a run as disposable, so a redirected path is never
    canonical even if it somehow resolves to the same place — the override is a
    declaration of intent by the caller, and honouring it is what lets the test
    suite build indexes at all.
    """
    if os.environ.get("ZURVAN_SEARCH_DB"):
        return False
    try:
        return Path(db_path).resolve() == canonical_db_path()
    except OSError:
        return False


def inspect_index(db_path, deep: bool = False) -> dict:
    """Raw facts about an index file. Raises sqlite3.Error if unreadable."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        stats: dict = {}
        stats["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        stats["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        stats["embeddings"] = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        stats["sources"] = conn.execute(
            "SELECT COUNT(DISTINCT source_path) FROM chunks"
        ).fetchone()[0]
        try:
            stats["fts_rows"] = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        except sqlite3.Error:
            stats["fts_rows"] = None
        # One row per distinct embedding space. More than one means cosine
        # scores are being compared across incompatible geometries.
        stats["spaces"] = [
            {"provider": p, "model": m, "dimension": d, "rows": n}
            for p, m, d, n in conn.execute(
                "SELECT provider, model, dimension, COUNT(*) FROM embeddings "
                "GROUP BY provider, model, dimension ORDER BY COUNT(*) DESC"
            )
        ]
        stats["indexed_at"] = conn.execute("SELECT MAX(indexed_at) FROM chunks").fetchone()[0]
        if deep:
            # A blob narrower or wider than its declared dimension makes
            # vec_distance_cosine return garbage for that row — a ranking bug
            # with no visible cause. Reads every vector, so it is opt-in.
            stats["wrong_width_vectors"] = conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE LENGTH(vector) != dimension * 4"
            ).fetchone()[0]
        return stats
    finally:
        conn.close()


def check_index_health(
    db_path,
    *,
    expect_provider: str | None = None,
    expect_model: str | None = None,
    expect_dimension: int | None = None,
    min_chunks: int = 1,
    min_sources: int = 0,
    deep: bool = False,
) -> HealthReport:
    """Judge an index. Unstated expectations default to canonical for the
    production path and to internal consistency alone for a throwaway."""
    path = Path(db_path)
    if not path.exists():
        return HealthReport(ok=False, problems=[f"index missing at {path}"])

    if is_canonical_index(path):
        expect_provider = expect_provider or CANONICAL_PROVIDER
        expect_model = expect_model or CANONICAL_MODEL
        expect_dimension = expect_dimension or CANONICAL_DIMENSION

    try:
        stats = inspect_index(path, deep=deep)
    except sqlite3.Error as exc:
        return HealthReport(ok=False, problems=[f"index unreadable: {exc}"])

    problems: list[str] = []

    if stats["integrity"] != "ok":
        problems.append(f"sqlite integrity_check: {stats['integrity']}")

    if stats["chunks"] < min_chunks:
        problems.append(f"{stats['chunks']} chunks, expected at least {min_chunks}")

    if stats["chunks"] != stats["embeddings"]:
        problems.append(
            f"{stats['chunks']} chunks but {stats['embeddings']} embeddings — "
            "a partial or interrupted rebuild"
        )

    if stats["fts_rows"] is None:
        problems.append("no chunks_fts table — keyword search cannot work")
    elif stats["fts_rows"] != stats["chunks"]:
        problems.append(
            f"{stats['fts_rows']} FTS rows against {stats['chunks']} chunks"
        )

    if min_sources and stats["sources"] < min_sources:
        problems.append(
            f"{stats['sources']} distinct sources, expected at least {min_sources} — "
            "the corpus is truncated"
        )

    spaces = stats["spaces"]
    if not spaces:
        problems.append("no embeddings at all")
    elif len(spaces) > 1:
        detail = ", ".join(
            f"{s['provider']}/{s['model']}/{s['dimension']}×{s['rows']}" for s in spaces
        )
        problems.append(f"mixed embedding spaces ({detail}) — cosine scores are meaningless")
    else:
        space = spaces[0]
        if not expect_provider and space["provider"] == "mock":
            # Asked "is this index healthy?" with no expectation stated, about an
            # index of SHA-256 hashes, the honest answer is no. Mock vectors are
            # a test fixture; a caller that genuinely wants one says so with
            # expect_provider="mock". Without this clause the 184,231-row
            # poisoned index reported VERDICT: healthy, which is exactly the
            # sentence that let it sit in production overnight.
            problems.append(
                "provider 'mock' — deterministic hashes, not semantics; "
                "this index measures 0% semantic recall"
            )
        if expect_provider and space["provider"] != expect_provider:
            problems.append(
                f"provider {space['provider']!r}, expected {expect_provider!r}"
                + (" — these are deterministic hashes, not semantics"
                   if space["provider"] == "mock" else "")
            )
        if expect_model and space["model"] != expect_model:
            problems.append(f"model {space['model']!r}, expected {expect_model!r}")
        if expect_dimension and space["dimension"] != expect_dimension:
            problems.append(
                f"dimension {space['dimension']}, expected {expect_dimension}"
            )

    if deep and stats.get("wrong_width_vectors"):
        problems.append(
            f"{stats['wrong_width_vectors']} vectors whose byte width does not "
            "match their declared dimension"
        )

    return HealthReport(ok=not problems, problems=problems, stats=stats)


def main(argv=None) -> int:
    import argparse
    import json

    from scripts.config import get_search_db_path

    parser = argparse.ArgumentParser(description="Report search-index health.")
    parser.add_argument("--db", default=None, help="index path (default: the configured one)")
    parser.add_argument("--min-sources", type=int, default=0)
    parser.add_argument("--min-chunks", type=int, default=1)
    parser.add_argument("--deep", action="store_true",
                        help="also verify every vector's byte width (reads all vectors)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    db = args.db or str(get_search_db_path())
    report = check_index_health(
        db, min_sources=args.min_sources, min_chunks=args.min_chunks, deep=args.deep
    )
    if args.as_json:
        print(json.dumps({"ok": report.ok, "problems": report.problems,
                          "stats": report.stats}, indent=2, default=str))
    else:
        print(f"Index: {db}")
        print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
