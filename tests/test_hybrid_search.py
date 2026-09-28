import pytest
import sqlite3
import os
import json

from scripts import vec_storage as _vs
from scripts.hybrid_search import cosine_similarity, search_hybrid
from scripts.rebuild_search_index import rebuild_search_index


def _write_fixture_corpus(root):
    """A small, fixed wiki for index-building tests.

    These tests used to index the LIVE wiki. That was seconds at 861 pages and
    unusable at 3,823 (131 MB of ingested sources) — the suite stopped
    finishing. Runtime must depend on the fixture, never on how much has been
    ingested into the developer's store.
    """
    wiki = root / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "citation.md").write_text(
        "# Citation Rules\n\nEvery citation needs a source and a verbatim quote.\n",
        encoding="utf-8",
    )
    (wiki / "graph.md").write_text(
        "# Knowledge Graph\n\nNodes and edges are derived from wikilinks.\n",
        encoding="utf-8",
    )
    (wiki / "retrieval.md").write_text(
        "# Retrieval\n\nHybrid search fuses BM25 keyword scores with embeddings.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="module")
def tmp_index(tmp_path_factory):
    """A search index built into a temp DB so tests never touch (or downgrade)
    the real data/search.sqlite.

    The mock provider is pinned for the build regardless of the ambient
    environment: the repo's own `.claude/settings.json` exports
    ``ZURVAN_EMBED_PROVIDER=sentence_transformers`` for agent sessions, which
    would otherwise make this fixture build a real (slow, non-deterministic)
    index and break the provider-follows-index assertion."""
    base = tmp_path_factory.mktemp("index")
    db_path = str(base / "search.sqlite")
    corpus = _write_fixture_corpus(base / "corpus")
    saved = {k: os.environ.get(k) for k in ("ZURVAN_EMBED_PROVIDER", "ZURVAN_EMBED_MODEL")}
    os.environ["ZURVAN_EMBED_PROVIDER"] = "mock"
    os.environ["ZURVAN_EMBED_MODEL"] = "mock_model"
    try:
        rebuild_search_index(db_path, root=corpus)
        # `_insert_chunk` uses this index as scratch: not a published store.
        _unlock_published_for_fixture(db_path)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return db_path


def _unlock_published_for_fixture(db) -> None:
    """Deliberately undo publication's read-only bit. These tests simulate the
    forbidden in-place write (or use a published store as scratch); saying so
    here keeps it from looking like an accident."""
    os.chmod(db, 0o644)


def _insert_chunk(db_path, chunk_id, heading, text):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (chunk_id, "wiki/test.md", heading, text, "hash", "now"),
    )
    cursor.execute(
        "INSERT INTO chunks_fts (rowid, chunk_id, heading, text) VALUES (last_insert_rowid(), ?, ?, ?)",
        (chunk_id, heading, text),
    )
    from scripts.embed import get_embedding
    # Pin the provider, exactly as the fixture does. Without this the vector is
    # built with whatever the ambient environment sets (the repo exports
    # sentence_transformers) while still being LABELLED mock, so the query —
    # which follows the index config and is embedded with mock — is compared
    # against a vector from a different space and scores ~0. The old
    # keyword-heavy fusion masked it; it surfaced once semantic carried the
    # majority of the score.
    vec = get_embedding(text, provider="mock", model="deterministic_hash")["vector"]
    cursor.execute(
        "INSERT INTO embeddings (chunk_id, provider, model, dimension, vector) VALUES (?, ?, ?, ?, ?)",
        # Phase 1: vectors are float32 BLOBs, not JSON text.
        (chunk_id, "mock", "deterministic_hash", len(vec), _vs.encode(vec)),
    )
    conn.commit()
    conn.close()


def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([1, 0], [-1, 0]) == -1.0
    assert cosine_similarity([0, 0], [0, 0]) == 0.0


def test_search_hybrid(tmp_index):
    chunk_id = "test_chunk_unique_999"
    _insert_chunk(tmp_index, chunk_id, "Test", "vector reliability test data XYZ999")

    results = search_hybrid("XYZ999", db_path=tmp_index)

    assert len(results) >= 1
    assert any(r['chunk_id'] == chunk_id for r in results)


def test_search_hybrid_survives_fts5_keywords(tmp_index):
    # Regression: unquoted terms were joined into the MATCH expression, so a
    # query containing a bareword FTS5 keyword (AND/OR/NOT/NEAR) raised
    # sqlite3.OperationalError: fts5 syntax error.
    for query in ("search AND rescue", "NOT this", "what is NEAR the graph", "OR"):
        results = search_hybrid(query, limit=3, db_path=tmp_index)
        assert isinstance(results, list)


def test_search_hybrid_handles_symbol_only_query(tmp_index):
    # No word characters at all — must not crash on an empty term list.
    assert isinstance(search_hybrid("!!! ???", limit=3, db_path=tmp_index), list)


def test_rebuild_reuses_embeddings_for_unchanged_chunks(tmp_path, monkeypatch):
    # Incremental rebuild: embeddings for unchanged chunk_ids come from the
    # previous index; only new/changed chunks (plus one provider probe) are
    # embedded. With a real provider this is the difference between seconds
    # and a full re-embed of the corpus.
    import scripts.rebuild_search_index as rsi
    from scripts.embed import get_embedding as real_get_embedding

    db_path = str(tmp_path / "search.sqlite")
    corpus = _write_fixture_corpus(tmp_path / "corpus")
    rebuild_search_index(db_path, root=corpus)  # warm index

    calls = {"n": 0}
    def counting_get_embedding(text):
        calls["n"] += 1
        return real_get_embedding(text)

    monkeypatch.setattr(rsi, "get_embedding", counting_get_embedding)
    rebuild_search_index(db_path, root=corpus)

    assert calls["n"] == 1  # the provider probe only — zero chunk re-embeds


def test_fts_porter_stemming_matches_inflected_terms(tmp_index):
    # R4a: the porter tokenizer stems query and index terms, so a plural
    # query matches singular content (the one genuine lexical miss in the
    # R1B analysis: "citations" vs heading "Citation").
    chunk_id = "stem_test_chunk_777"
    _insert_chunk(
        tmp_index, chunk_id, "Citation Rules ZQSTEM777",
        "## Citation Rules ZQSTEM777\nEvery citation needs a source.",
    )

    # Asserted at the FTS layer, which is where stemming actually happens.
    # Going through search_hybrid coupled this to the fusion ranking: semantic
    # now carries most of the score, and a synthetic chunk cannot out-rank
    # 26k real ones on a mock-space vector however well it matches lexically.
    # The claim under test is unchanged — a plural query matches singular text.
    conn = sqlite3.connect(tmp_index)
    try:
        matched = conn.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?", ('"citations"',)
        ).fetchall()
    finally:
        conn.close()

    assert (chunk_id,) in matched, "porter stemming should match 'citations' to 'Citation'"


def test_query_embedding_follows_index_provider(tmp_index, monkeypatch):
    # The index is the source of truth: even if the env names a different
    # provider, queries embed with the provider/model stored in the index —
    # mixing providers would make cosine scores meaningless.
    import scripts.hybrid_search as hs
    from scripts.embed import get_embedding as real_get_embedding

    captured = {}
    def spying_get_embedding(text, provider=None, model=None):
        captured["provider"] = provider
        captured["model"] = model
        return real_get_embedding(text, provider="mock", model="deterministic_hash")

    monkeypatch.setattr(hs, "get_embedding", spying_get_embedding)
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "sentence_transformers")

    search_hybrid("anything", limit=1, db_path=tmp_index)

    assert captured["provider"] == "mock"              # from the index,
    assert captured["model"] == "deterministic_hash"   # not from the env


def test_vectorised_scoring_matches_the_reference_cosine_fusion(tmp_path_factory):
    # The semantic path was rewritten from "pull every row, cosine in Python"
    # to a batched numpy matrix product reduced through a bounded heap, because
    # the old shape marshalled the whole corpus (text included) on every query
    # — ~2 GB at 199k chunks. The rewrite must be a pure performance change, so
    # assert it against the reference implementation it replaced.
    #
    # Own index, not the module fixture: the reference below models the
    # semantic half only, so the query must match nothing lexically for both
    # sides to see keyword_score 0. A shared index carries other tests' chunks
    # and cannot guarantee that.
    import scripts.hybrid_search as hs

    base = tmp_path_factory.mktemp("vec_equiv")
    db_path = str(base / "search.sqlite")
    corpus = _write_fixture_corpus(base / "corpus")
    saved = {k: os.environ.get(k) for k in ("ZURVAN_EMBED_PROVIDER", "ZURVAN_EMBED_MODEL")}
    os.environ["ZURVAN_EMBED_PROVIDER"] = "mock"
    os.environ["ZURVAN_EMBED_MODEL"] = "mock_model"
    try:
        rebuild_search_index(db_path, root=corpus)
        # `_insert_chunk` uses this index as scratch: not a published store.
        _unlock_published_for_fixture(db_path)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    for i in range(25):
        _insert_chunk(db_path, f"vec_equiv_{i}", f"Heading {i}",
                      f"retrieval fusion sample text number {i} about citations and graphs")

    # No word in the corpus, so FTS matches nothing and keyword_score is 0 for
    # every chunk on both paths — isolating the semantic arithmetic.
    query = "zqxwv unmatchable token"

    conn = sqlite3.connect(db_path)
    try:
        provider, model = hs._index_embedding_config(conn.cursor())
        assert conn.execute(
            "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", ('"zqxwv"',)
        ).fetchone()[0] == 0
        query_emb = hs.get_embedding(query, provider=provider, model=model)["vector"]
        rows = conn.execute(
            "SELECT c.chunk_id, c.source_path, c.heading, c.text, e.vector "
            "FROM chunks c JOIN embeddings e ON c.chunk_id = e.chunk_id"
        ).fetchall()
    finally:
        conn.close()

    # The reference: exactly the code that was replaced.
    reference = []
    for chunk_id, source_path, heading, text, vector_json in rows:
        sem = hs.cosine_similarity(query_emb, _vs.decode(vector_json))
        sem = max(0.0, (sem + 1.0) / 2.0)
        reference.append({
            "chunk_id": chunk_id, "source_path": source_path, "heading": heading,
            "text": text, "keyword_score": 0.0, "semantic_score": sem,
        })
    expected = hs._fuse(reference)[:10]
    assert expected, "reference should rank something"

    actual = hs.search_hybrid(query, limit=10, db_path=db_path)

    # Order is what ranking means, and it must be identical.
    assert [r["chunk_id"] for r in actual] == [r["chunk_id"] for r in expected]

    # Scores agree to float32 resolution, not to the bit: the sidecar stores
    # vectors as float32 (halving its size and the per-query conversion) while
    # the reference parses the JSON into float64, so scores differ around the
    # 1e-9 mark. That is ~2 orders below the closest score gap seen on the real
    # gold set; a pair separated by less than ~1e-7 could in principle order
    # differently, which is why the ordering assertion above is the contract
    # and this one is a sanity bound.
    for a, e in zip(actual, expected):
        assert a["semantic_score"] == pytest.approx(e["semantic_score"], abs=1e-6)
        assert a["hybrid_score"] == pytest.approx(e["hybrid_score"], abs=1e-6)


# --- canonical ordering policy (Phase 1 Gate A contract) --------------------
# Gate A originally demanded "ordering identical to the reference". That is
# unachievable for tied chunks, because the reference had no tie rule: equal
# scores were resolved by whichever row the scan reached first. The contract is
# therefore: unequal scores must match the reference exactly, and exact ties
# must obey ONE canonical rule in BOTH implementations — score DESC, chunk_id
# ASC. These tests are that rule's only guarantee.

def test_canonical_order_is_score_desc_then_chunk_id_asc():
    from scripts.hybrid_search import _Ranked

    assert _Ranked(0.9, "b").outranks(_Ranked(0.8, "a"))       # score wins
    assert _Ranked(0.5, "a").outranks(_Ranked(0.5, "b"))       # tie -> lower id
    assert not _Ranked(0.5, "b").outranks(_Ranked(0.5, "a"))
    assert not _Ranked(0.5, "a").outranks(_Ranked(0.5, "a"))   # irreflexive


def test_chunk_id_is_compared_as_an_opaque_string():
    # chunk_ids are sha256 hex today, but retrieval must not encode that: an
    # int(cid, 16) tie-break would raise on any other id format.
    from scripts.hybrid_search import _Ranked

    assert _Ranked(1.0, "alpha").outranks(_Ranked(1.0, "beta"))
    # Lexical, NOT numeric: "chunk-10" sorts before "chunk-2". Asserted in the
    # surprising direction on purpose — the rule is only required to be total
    # and stable, and pretending it is numeric would be the start of a
    # format assumption.
    assert _Ranked(1.0, "chunk-10").outranks(_Ranked(1.0, "chunk-2"))


def test_bounded_heap_resolves_exact_ties_independently_of_scan_order():
    # THE regression this suite exists for. Two byte-identical chunks from
    # duplicate wiki pages score exactly equal; only one fits in the result
    # budget. Which one must not depend on the order the scan delivers them in
    # — that is what changed when vectors moved to sqlite-vec.
    import heapq
    from scripts.hybrid_search import _Ranked

    def keep_best(ids, limit=1):
        heap = []
        for cid in ids:
            e = _Ranked(0.75, cid)          # exactly equal scores
            if len(heap) < limit:
                heapq.heappush(heap, e)
            elif e.outranks(heap[0]):
                heapq.heapreplace(heap, e)
        # heapq guarantees only that heap[0] is the root; sort for comparison.
        return sorted(x.chunk_id for x in heap)

    assert keep_best(["bbb", "aaa"]) == ["aaa"]
    assert keep_best(["aaa", "bbb"]) == ["aaa"]
    assert keep_best(["ccc", "aaa", "bbb"], limit=2) == ["aaa", "bbb"]
    assert keep_best(["aaa", "bbb", "ccc"], limit=2) == ["aaa", "bbb"]


def test_fuse_breaks_tied_scores_on_chunk_id_not_on_input_order():
    # The heap is not the only place ties were unspecified: _fuse's stable sort
    # inherited _hydrate's SQLite fetch order for equal hybrid scores.
    from scripts.hybrid_search import _fuse

    def chunk(cid):
        return {"chunk_id": cid, "source_path": f"{cid}.md", "heading": "h",
                "text": "t", "keyword_score": 0.0, "semantic_score": 0.9}

    forward = [r["chunk_id"] for r in _fuse([chunk("a"), chunk("b"), chunk("c")])]
    reverse = [r["chunk_id"] for r in _fuse([chunk("c"), chunk("b"), chunk("a")])]
    assert forward == reverse == ["a", "b", "c"]
