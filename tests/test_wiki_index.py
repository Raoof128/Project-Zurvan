import os

import pytest

from scripts import wiki_index


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki_index, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    return tmp_path


def test_toc_stays_small_and_readable_however_many_sources(wiki):
    # The whole point: wiki/index.md was an append-only log that reached 501 KB
    # over 3,713 lines and blew past the MCP 256 KB read cap, so the index was
    # the one page an agent could not open. The table of contents must not grow
    # with the corpus.
    sources = wiki / "wiki" / "sources"
    for i in range(2000):
        (sources / f"Doc_{i:05d}.md").write_text("x", encoding="utf-8")

    assert wiki_index.rebuild() == 2000

    toc = (wiki / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "Wiki Index" in toc
    assert len(toc.encode()) < 8192, "the table of contents must stay small"
    assert "2000 source pages" in toc
    for shard in os.listdir(wiki / "wiki" / "index"):
        size = (wiki / "wiki" / "index" / shard).stat().st_size
        assert size < 256 * 1024, f"{shard} exceeds the MCP read cap"


def test_add_entry_is_idempotent(wiki):
    # A re-ingest used to append the line again: 450 of the 3,713 lines in the
    # real index were duplicates.
    for _ in range(3):
        wiki_index.add_entry("Paper_One.md")

    shard = (wiki / "wiki" / "index" / "sources-P.md").read_text(encoding="utf-8")
    assert shard.count("- [sources/Paper_One.md]") == 1
    assert "1 source pages" in (wiki / "wiki" / "index.md").read_text(encoding="utf-8")


def test_entries_are_sharded_by_first_character(wiki):
    for name in ("Alpha.md", "9lives.md", "_hidden.md"):
        wiki_index.add_entry(name)

    idx = wiki / "wiki" / "index"
    assert "- [sources/Alpha.md]" in (idx / "sources-A.md").read_text(encoding="utf-8")
    assert "- [sources/9lives.md]" in (idx / "sources-0-9.md").read_text(encoding="utf-8")
    assert "- [sources/_hidden.md]" in (idx / "sources-other.md").read_text(encoding="utf-8")


def test_rebuild_drops_stale_shards(wiki):
    wiki_index.add_entry("Gone.md")            # shard written, but no page on disk
    assert (wiki / "wiki" / "index" / "sources-G.md").exists()

    (wiki / "wiki" / "sources" / "Kept.md").write_text("x", encoding="utf-8")
    wiki_index.rebuild()

    assert not (wiki / "wiki" / "index" / "sources-G.md").exists()
    assert (wiki / "wiki" / "index" / "sources-K.md").exists()
