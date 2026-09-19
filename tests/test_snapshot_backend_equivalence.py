"""The two backends must produce the same snapshot, or "portable-first" is a slogan.

`clonefile` is free and APFS-only. A byte copy is universal, and measured on
this corpus it is 25-37x slower and allocates 147 MB. Both are correct, and
downstream code may never be able to tell which one ran.

The fast path works on the machine doing the developing, so a fallback that
silently diverged would be discovered by someone else, later, on hardware we do
not have. That is what makes this contract worth a test rather than a comment.
"""

import ast
import hashlib
import inspect
import sqlite3
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib
from scripts import measurement_snapshot as ms
from scripts.rebuild_search_index import rebuild_search_index


def _raises_enotsup(source, destination):
    raise OSError(45, "Operation not supported")


def _hash_tree(root):
    """relpath -> sha256 over every file, so two captures compare by CONTENT
    rather than by the mechanism that produced them."""
    digests = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            digests[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return digests


@pytest.fixture
def equivalence_store(tmp_path, monkeypatch):
    from scripts import config

    (tmp_path / "data").mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "data" / "index.sqlite"))
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    wiki = tmp_path / "wiki"
    (wiki / "decisions").mkdir(parents=True)
    (wiki / "a.md").write_text("# Alpha\n\nthe original bytes\n", encoding="utf-8")
    (wiki / "decisions" / "b.md").write_text("# Beta\n\nmore bytes\n", encoding="utf-8")
    rebuild_search_index(root=tmp_path)
    conn = sqlite3.connect(tmp_path / "data" / "graph.sqlite")
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO nodes VALUES (1)")
    conn.commit()
    conn.close()
    return tmp_path


def _capture_both(root, monkeypatch):
    """One capture per backend, against the same unchanged store."""
    cloned = ms.capture(root=root, label="clone",
                        components=("wiki", "search", "graph"))
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    copied = ms.capture(root=root, label="copy",
                        components=("wiki", "search", "graph"))
    return cloned, copied


def test_the_two_backends_really_are_different_backends(equivalence_store,
                                                        monkeypatch):
    """If both captures used the same mechanism, every comparison below is
    vacuous. This is the guard that keeps the rest of the file honest."""
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        assert cloned.identity["components"]["wiki"]["capture_backend"] == "clonefile"
        assert copied.identity["components"]["wiki"]["capture_backend"] == "copy"
    finally:
        cloned.release()
        copied.release()


def test_both_backends_capture_the_same_component_set(equivalence_store,
                                                      monkeypatch):
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        assert (set(cloned.identity["components"])
                == set(copied.identity["components"]))
    finally:
        cloned.release()
        copied.release()


def test_both_backends_produce_the_same_paths_and_the_same_bytes(
        equivalence_store, monkeypatch):
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        a = _hash_tree(cloned.path / "wiki")
        b = _hash_tree(copied.path / "wiki")
        assert set(a) == set(b), "relative paths differ between backends"
        assert a == b, "byte content differs between backends"
    finally:
        cloned.release()
        copied.release()


def test_both_backends_report_the_same_manifest_semantics(equivalence_store,
                                                          monkeypatch):
    """Everything in the component record except the backend name must match.
    Scope and verification status are semantics; the mechanism is diagnostics."""
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        for name in cloned.identity["components"]:
            a = dict(cloned.identity["components"][name])
            b = dict(copied.identity["components"][name])
            a.pop("capture_backend")
            b.pop("capture_backend")
            assert a == b, f"{name}: semantics differ between backends"
    finally:
        cloned.release()
        copied.release()


def test_both_backends_survive_the_same_live_edits(equivalence_store, monkeypatch):
    """Immutability is a property of the snapshot, not of the backend. The
    matrix runs against the fallback too, because a copy that somehow shared
    state would be the same defect with a different cause."""
    import os

    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        page = equivalence_store / "wiki" / "a.md"
        replacement = equivalence_store / "wiki" / "a.md.tmp"
        replacement.write_text("rewritten", encoding="utf-8")
        os.replace(replacement, page)
        page.write_text("rewritten again", encoding="utf-8")
        page.unlink()

        for snapshot in (cloned, copied):
            frozen = snapshot.path / "wiki" / "a.md"
            assert frozen.read_text(encoding="utf-8").startswith("# Alpha")
    finally:
        cloned.release()
        copied.release()


def test_no_correctness_path_branches_on_the_capture_backend():
    """A LINT, not a proof, and worth keeping only because it is cheap.

    It walks one module's AST for a comparison against a backend constant.
    Assigning one is fine — that is how the mechanism gets recorded. Comparing
    one means behaviour depends on which filesystem happened to be underneath.

    What it cannot see: a string literal comparison (`== "clonefile"`), a
    branch in any other module, or a dict lookup keyed on the backend. The REAL
    contract is the behavioural equivalence above — same paths, same bytes,
    same semantics, same immutability. If this lint and those tests ever
    disagree, the behavioural tests are the ones that matter.
    """
    tree = ast.parse(inspect.getsource(ms))
    constants = {"CLONEFILE", "COPY", "HARDLINK"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for side in [node.left, *node.comparators]:
            if isinstance(side, ast.Name) and side.id in constants:
                offenders.append(ast.unparse(node))
            elif isinstance(side, ast.Attribute) and side.attr in constants:
                offenders.append(ast.unparse(node))
    assert not offenders, (
        "correctness branches on the capture backend: " + "; ".join(offenders))
