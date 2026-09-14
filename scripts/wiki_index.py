"""The wiki's source index: a small table of contents plus alphabetical shards.

``ingest.update_index`` appended one line to ``wiki/index.md`` per ingest, with
no de-duplication and no bound. After the research archive went in, the file was
**501 KB over 3,713 lines with 450 duplicates** — past the MCP read cap, so
``zurvan_read_page wiki/index.md`` returned "File too large (> 256 KB)" and the
index became the one page an agent could not read.

An index that cannot be opened is not an index. So:

* ``wiki/index.md`` keeps the navigation and a per-shard summary — always small;
* entries live in ``wiki/index/sources-<shard>.md``, split on the first
  character of the page name;
* adding an entry is idempotent, so a re-ingest no longer duplicates a line.
"""
import os
import re

from scripts.config import PROJECT_ROOT

NAV = """# Wiki Index

Welcome to the local LLM Wiki.

## Navigation
- [Overview](overview.md)
- [Open Questions](open-questions.md)
- [Log](log.md)
"""


def _index_dir():
    return PROJECT_ROOT / "wiki" / "index"


def shard_for(filename: str) -> str:
    """Shard key for a page name: '0-9', 'A'-'Z', or 'other'."""
    first = os.path.basename(filename)[:1].upper()
    if first.isdigit():
        return "0-9"
    if "A" <= first <= "Z":
        return first
    return "other"


def _shard_path(shard: str):
    return _index_dir() / f"sources-{shard}.md"


def add_entry(filename: str) -> None:
    """Record one source page in its shard. Idempotent."""
    shard = shard_for(filename)
    path = _shard_path(shard)
    os.makedirs(_index_dir(), exist_ok=True)
    line = f"- [sources/{filename}](../sources/{filename})\n"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            if line in fh.read():
                return
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# Sources — {shard}\n\n")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
    write_toc()


def write_toc() -> None:
    """Regenerate wiki/index.md from whatever shards exist."""
    shards = []
    if _index_dir().exists():
        for name in sorted(os.listdir(_index_dir())):
            m = re.fullmatch(r"sources-(.+)\.md", name)
            if not m:
                continue
            with open(_index_dir() / name, encoding="utf-8") as fh:
                count = sum(1 for ln in fh if ln.startswith("- ["))
            shards.append((m.group(1), name, count))
    total = sum(c for _, _, c in shards)
    lines = [NAV, "\n## Sources\n\n", f"{total} source pages across {len(shards)} shards.\n\n"]
    for key, name, count in shards:
        lines.append(f"- [{key}](index/{name}) — {count}\n")
    with open(PROJECT_ROOT / "wiki" / "index.md", "w", encoding="utf-8") as fh:
        fh.write("".join(lines))


def rebuild() -> int:
    """Rebuild every shard and the table of contents from wiki/sources/ on disk."""
    sources = PROJECT_ROOT / "wiki" / "sources"
    os.makedirs(_index_dir(), exist_ok=True)
    for name in os.listdir(_index_dir()):
        if re.fullmatch(r"sources-.+\.md", name):
            os.remove(_index_dir() / name)

    buckets = {}
    names = sorted(f for f in os.listdir(sources) if f.endswith(".md")) if sources.exists() else []
    for filename in names:
        buckets.setdefault(shard_for(filename), []).append(filename)

    for shard, filenames in buckets.items():
        with open(_shard_path(shard), "w", encoding="utf-8") as fh:
            fh.write(f"# Sources — {shard}\n\n")
            for filename in filenames:
                fh.write(f"- [sources/{filename}](../sources/{filename})\n")
    write_toc()
    return len(names)


if __name__ == "__main__":
    print(f"Rebuilt wiki index: {rebuild()} source pages.")
