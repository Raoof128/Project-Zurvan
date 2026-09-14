import os
import sqlite3

import pytest

from scripts import prune_registry as pr
from scripts.prune_registry import HEALTHY, HELD, ORPHAN


def _page(sources, name, digest):
    (sources / name).write_text(f"---\ntitle: {name}\nhash: {digest}\n---\n\nbody\n", encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    """A registry plus a wiki/sources dir containing one row of each state."""
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    live = tmp_path / "live.md"
    live.write_text("x", encoding="utf-8")

    db = tmp_path / "registry.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE, hash TEXT, ingested_at TEXT)")
    rows = [
        (str(live), "hash-healthy"),                    # file + page
        (str(tmp_path / "deleted.md"), "hash-held"),    # file gone, page kept
        (str(tmp_path / "vanished.md"), "hash-orphan"), # nothing anywhere
    ]
    conn.executemany("INSERT INTO sources (path, hash, ingested_at) VALUES (?, ?, 'now')", rows)
    conn.commit()
    conn.close()

    _page(sources, "healthy.md", "hash-healthy")
    _page(sources, "held.md", "hash-held")
    return db, sources


def test_classify_separates_held_content_from_true_orphans(store):
    db, sources = store
    b = pr.classify(registry_path=db, sources_dir=sources)

    assert [p for p, _ in b[HEALTHY]] == [p for p, _ in b[HEALTHY] if os.path.exists(p)]
    assert len(b[HEALTHY]) == 1
    assert len(b[HELD]) == 1 and b[HELD][0][1] == "hash-held"
    assert len(b[ORPHAN]) == 1 and b[ORPHAN][0][1] == "hash-orphan"


def test_a_deleted_source_whose_page_survives_is_never_pruned(store, monkeypatch):
    # The content IS still in Zurvan; only the original path died. Releasing
    # the row would let the same bytes ingest again as a duplicate page.
    db, sources = store
    b = pr.classify(registry_path=db, sources_dir=sources)

    released = []
    monkeypatch.setattr(pr, "unregister_source", lambda p: released.append(p))
    pr.prune(b)

    assert len(released) == 1
    assert "vanished.md" in released[0]
    assert not any("deleted.md" in r for r in released), "held content must be kept"


def test_prune_releases_every_orphan(store, monkeypatch):
    db, sources = store
    b = pr.classify(registry_path=db, sources_dir=sources)

    released = []
    monkeypatch.setattr(pr, "unregister_source", lambda p: released.append(p))

    assert pr.prune(b) == len(b[ORPHAN]) == len(released)


def test_a_row_with_a_live_file_but_no_page_is_an_orphan(tmp_path):
    # This is the recoverable case: the document exists, the registry claims it
    # is ingested, and no page holds it — so a re-ingest is refused and the
    # content can never come back until the row is released.
    sources = tmp_path / "wiki" / "sources"
    sources.mkdir(parents=True)
    live = tmp_path / "present.md"
    live.write_text("x", encoding="utf-8")

    db = tmp_path / "registry.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE, hash TEXT, ingested_at TEXT)")
    conn.execute("INSERT INTO sources (path, hash, ingested_at) VALUES (?, 'no-page', 'now')", (str(live),))
    conn.commit()
    conn.close()

    b = pr.classify(registry_path=db, sources_dir=sources)
    assert len(b[ORPHAN]) == 1
    assert b[HEALTHY] == [] and b[HELD] == []


def test_reporting_is_the_default_and_changes_nothing(store, monkeypatch, capsys):
    db, sources = store
    real_classify = pr.classify  # bind before patching, or the lambda calls itself
    monkeypatch.setattr(pr, "classify", lambda **k: real_classify(registry_path=db, sources_dir=sources))
    monkeypatch.setattr("sys.argv", ["prune_registry.py"])
    called = []
    monkeypatch.setattr(pr, "prune", lambda b: called.append(b) or 0)

    assert pr.main() == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert called == [], "the default run must never modify the registry"
