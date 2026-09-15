"""Phase 1 — sqlite-vec storage swap. Gate A is RANKING INVARIANCE."""
import json
import sqlite3

import pytest

from scripts import vec_storage as vs


def test_cosine_distance_is_converted_to_similarity_explicitly():
    # THE trap this test exists for: sqlite-vec returns cosine DISTANCE, while
    # _fuse consumes a similarity mapped through (cos + 1) / 2. A silent scale
    # error here looks exactly like a ranking regression and would be debugged
    # for hours in the wrong place.
    assert vs.distance_to_similarity(0.0) == pytest.approx(1.0)    # identical
    assert vs.distance_to_similarity(1.0) == pytest.approx(0.0)    # orthogonal
    assert vs.distance_to_similarity(2.0) == pytest.approx(-1.0)   # opposite
    assert vs.distance_to_similarity(0.4) == pytest.approx(0.6)


def test_fusion_scale_matches_the_existing_mapping():
    # The value handed to _fuse must be max(0, (cos + 1) / 2), unchanged.
    assert vs.similarity_to_fusion_scale(1.0) == pytest.approx(1.0)
    assert vs.similarity_to_fusion_scale(0.0) == pytest.approx(0.5)
    assert vs.similarity_to_fusion_scale(-1.0) == pytest.approx(0.0)
    assert vs.similarity_to_fusion_scale(-2.0) == 0.0, "must clamp, as today"


def test_vectors_round_trip_through_the_blob_encoding():
    v = [0.25, -0.5, 0.75]
    assert vs.decode(vs.encode(v)) == pytest.approx(v, abs=1e-6)


def test_blob_encoding_is_float32_not_json():
    # The whole point: JSON text was parsed on every query. 3 floats must be
    # 12 bytes, not a string.
    blob = vs.encode([1.0, 2.0, 3.0])
    assert isinstance(blob, (bytes, bytearray))
    assert len(blob) == 12


def test_extension_loads_and_reports_a_pinned_pair():
    conn = sqlite3.connect(":memory:")
    vs.load_extension(conn)
    pkg, ext = vs.versions(conn)
    assert pkg and ext
    # Recorded in the frozen report for reproducibility; asserted equal here
    # because a divergence is exactly the trap worth catching early.
    assert ext.lstrip("v") == pkg, f"package {pkg} vs extension {ext}"


def test_full_scan_preserves_every_candidate(tmp_path):
    # NOT the vec0 KNN path. Today every chunk receives a fused score, so a
    # chunk with a strong keyword score but mediocre semantics can still reach
    # the top-10. Semantic KNN would make those invisible — a candidate-policy
    # change disguised as a storage migration.
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(str(db))
    vs.load_extension(conn)
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, "
                 "model TEXT, dimension INTEGER, vector BLOB)")
    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [-1.0, 0.0]}
    conn.executemany("INSERT INTO embeddings VALUES (?,?,?,?,?)",
                     [(k, "p", "m", 2, vs.encode(v)) for k, v in vectors.items()])
    conn.commit()

    scored = dict(vs.scan_similarities(conn, [1.0, 0.0]))
    assert set(scored) == set(vectors), "a full scan must score EVERY chunk"
    assert scored["a"] == pytest.approx(1.0, abs=1e-6)
    assert scored["b"] == pytest.approx(0.0, abs=1e-6)
    assert scored["c"] == pytest.approx(-1.0, abs=1e-6)


def test_scan_matches_the_reference_cosine_within_tolerance(tmp_path):
    import math
    import random

    def ref(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return 0.0 if na == 0 or nb == 0 else dot / (na * nb)

    random.seed(11)
    vecs = {f"c{i}": [random.uniform(-1, 1) for _ in range(16)] for i in range(40)}
    q = [random.uniform(-1, 1) for _ in range(16)]

    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    vs.load_extension(conn)
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, "
                 "model TEXT, dimension INTEGER, vector BLOB)")
    conn.executemany("INSERT INTO embeddings VALUES (?,?,?,?,?)",
                     [(k, "p", "m", 16, vs.encode(v)) for k, v in vecs.items()])
    conn.commit()

    got = dict(vs.scan_similarities(conn, q))
    for k, v in vecs.items():
        # float32 storage vs float64 reference: agree to float32 resolution.
        assert got[k] == pytest.approx(ref(q, v), abs=1e-6)

    # Scores are this layer's contract; the ORDER they imply is asserted here by
    # sorting both sides, never by trusting the order rows arrived in.
    assert [k for k, _ in sorted(got.items(), key=lambda kv: -kv[1])] == \
           [k for k, _ in sorted(vecs.items(), key=lambda kv: -ref(q, kv[1]))]


def test_ties_break_deterministically_regardless_of_scan_order(tmp_path):
    # This test used to assert that `scan_similarities` ITSELF returned ties in
    # chunk_id order, because it sorted in SQL. That put a ranking rule inside a
    # storage function, and the sort cost ~half the query. The rule now lives in
    # `hybrid_search._Ranked`, so what must be asserted is that the RESULT is
    # deterministic however the scan happens to deliver rows — the storage layer
    # promises completeness and correct scores, nothing about order.
    import heapq
    from scripts.hybrid_search import _Ranked

    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    vs.load_extension(conn)
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, "
                 "model TEXT, dimension INTEGER, vector BLOB)")
    same = vs.encode([1.0, 0.0])
    conn.executemany("INSERT INTO embeddings VALUES (?,?,?,?,?)",
                     [(k, "p", "m", 2, same) for k in ("zzz", "aaa", "mmm")])
    conn.commit()

    scanned = dict(vs.scan_similarities(conn, [1.0, 0.0]))
    assert set(scanned) == {"zzz", "aaa", "mmm"}, "every chunk must still be scored"

    def best_of(order, limit=1):
        heap = []
        for cid in order:
            e = _Ranked(scanned[cid], cid)
            if len(heap) < limit:
                heapq.heappush(heap, e)
            elif e.outranks(heap[0]):
                heapq.heapreplace(heap, e)
        return sorted(x.chunk_id for x in heap)

    # Every delivery order the storage layer could legally produce.
    import itertools
    for perm in itertools.permutations(["zzz", "aaa", "mmm"]):
        assert best_of(perm) == ["aaa"]
        assert best_of(perm, limit=2) == ["aaa", "mmm"]


# ── integration: the rebuild path itself must write BLOBs ─────────────────────

def test_rebuild_writes_blob_vectors_not_json(tmp_path, monkeypatch):
    # vec_storage.py can be perfect while some forgotten branch of the rebuild
    # still writes JSON text. This asserts the STORED bytes, not the helper.
    import os

    from scripts.rebuild_search_index import rebuild_search_index

    corpus = tmp_path / "corpus"
    (corpus / "wiki").mkdir(parents=True)
    (corpus / "wiki" / "a.md").write_text("# Alpha\n\nretrieval fusion evidence\n", encoding="utf-8")
    (corpus / "wiki" / "b.md").write_text("# Beta\n\ngraph nodes and edges\n", encoding="utf-8")

    db = str(tmp_path / "search.sqlite")
    saved = {k: os.environ.get(k) for k in ("ZURVAN_EMBED_PROVIDER", "ZURVAN_EMBED_MODEL")}
    os.environ["ZURVAN_EMBED_PROVIDER"] = "mock"
    os.environ["ZURVAN_EMBED_MODEL"] = "mock_model"
    try:
        rebuild_search_index(db, root=corpus)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT typeof(vector), length(vector), dimension FROM embeddings").fetchall()
    conn.close()

    assert rows, "nothing was indexed"
    for kind, length, dim in rows:
        assert kind == "blob", f"vector stored as {kind}, not blob — a JSON branch survives"
        assert length == dim * 4, f"{length} bytes for {dim} float32 dimensions"


def test_rebuild_output_scores_identically_to_the_reference(tmp_path):
    # End-to-end: the stored blobs, read back through scan_similarities, must
    # reproduce the reference cosine ordering.
    import math
    import os

    from scripts.rebuild_search_index import rebuild_search_index

    corpus = tmp_path / "corpus"
    (corpus / "wiki").mkdir(parents=True)
    for i in range(6):
        (corpus / "wiki" / f"p{i}.md").write_text(f"# Page {i}\n\ntopic {i} content\n", encoding="utf-8")

    db = str(tmp_path / "search.sqlite")
    saved = {k: os.environ.get(k) for k in ("ZURVAN_EMBED_PROVIDER", "ZURVAN_EMBED_MODEL")}
    os.environ["ZURVAN_EMBED_PROVIDER"] = "mock"
    os.environ["ZURVAN_EMBED_MODEL"] = "mock_model"
    try:
        rebuild_search_index(db, root=corpus)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    conn = sqlite3.connect(db)
    vs.load_extension(conn)
    stored = {cid: vs.decode(b) for cid, b in conn.execute("SELECT chunk_id, vector FROM embeddings")}
    query = next(iter(stored.values()))

    def ref(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return 0.0 if na == 0 or nb == 0 else dot / (na * nb)

    got = dict(vs.scan_similarities(conn, query))
    conn.close()

    assert set(got) == set(stored), "every stored chunk must be scored"
    assert [k for k, _ in sorted(got.items(), key=lambda kv: (-kv[1], kv[0]))] == \
           [k for k, _ in sorted(stored.items(), key=lambda kv: (-ref(query, kv[1]), kv[0]))]


def test_prior_index_json_vectors_are_converted_not_reused_raw(tmp_path):
    # Migration hazard caught by the dimension guard on the first real rebuild:
    # the prior index stores vectors as JSON TEXT, and reusing one verbatim
    # would put an 8,481-byte string into a BLOB column. vec_distance_cosine
    # would then return garbage for that row — a ranking catastrophe that looks
    # like a quality regression. The VALUES are identical across the format
    # change, so convert rather than re-embed (and avoid a ~50 min rebuild).
    import json

    from scripts.rebuild_search_index import _PriorIndex

    db = tmp_path / "prev.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT PRIMARY KEY, provider TEXT, "
                 "model TEXT, dimension INTEGER, vector)")
    conn.execute("INSERT INTO embeddings VALUES ('c1','p','m',3,?)",
                 (json.dumps([1.0, 2.0, 3.0]),))
    conn.commit(); conn.close()

    prior = _PriorIndex(str(db))
    try:
        row = prior.get("c1")
        assert row is not None
        provider, model, dimension, vector = row
        assert isinstance(vector, (bytes, bytearray)), "JSON text must be converted to a BLOB"
        assert len(vector) == dimension * 4
        assert vs.decode(vector) == pytest.approx([1.0, 2.0, 3.0], abs=1e-6)
    finally:
        prior.close()
