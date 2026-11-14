"""Z2 Task 10: erase keeps the chain, zeroes content, reports every residual copy
(spec §7.7, gate G7)."""

import json
import stat

import pytest

from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

SRC = "wiki/sources/client.md"
BODY = "AcmeClientSecretContext the client owes 4812 dollars TrailingSecretContext\n"
Q = "the client owes 4812 dollars"
TEXT = "Acme client owes 4812 dollars"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root, **kw):
    with kr.operation(writer="test", root=root):
        return cl.add(TEXT, SRC, Q, confidence="high", tags=["acmeclienttag"], actor="local:t",
                      method="human", root=root, **kw)


def _erase(root, cid, **kw):
    with kr.operation(writer="test", root=root):
        return cl.erase(cid, reason_class="client_request", actor="local:t", root=root, **kw)


def test_erase_blanks_every_body_and_keeps_chain(root):
    cid = _add(root)
    with kr.operation(writer="test", root=root):
        cl.revise(cid, reason="note on client", actor="local:t", method="human",
                  confidence="medium", root=root)
    out = _erase(root, cid)
    assert out["rev"] == 3
    conn = cs.connect(root, readonly=True)
    assert cs.chain_ok(conn) == (True, None)
    bodies = [r[0] for r in conn.execute(
        "SELECT c.body FROM content c WHERE c.erased_at IS NOT NULL")]
    assert bodies and all(b is None for b in bodies)
    s = cl.current(root)[cid]
    assert s.status == "erased" and s.text is None and s.tags == []


def test_erased_text_absent_from_db_bytes(root):
    cid = _add(root)
    _erase(root, cid)
    raw = cs.db_path(root).read_bytes()
    for fragment in (b"4812", b"AcmeClientSecretContext", b"TrailingSecretContext",
                     b"acmeclienttag", b"client.md", b"owes"):
        assert fragment not in raw, fragment
    assert not cs.db_path(root).with_name("claims.sqlite-journal").exists()


def test_erase_rewrites_pages_without_the_text(root):
    old = _add(root)
    with kr.operation(writer="test", root=root):
        new = cl.supersede(old, text="Client account settled", source=SRC,
                           evidence="TrailingSecretContext", reason="paid", actor="local:t",
                           method="human", root=root)
    _erase(root, old)
    for path in (root / "wiki" / "claims").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "4812" not in text and "owes" not in text, path.name


def test_erase_stubs_legacy_copy_and_records_it(root, tmp_path_factory):
    legacy = tmp_path_factory.mktemp("legacy")
    (legacy / "claim-0001.md").write_text(f"# Claim\n{TEXT}\n", encoding="utf-8")
    (legacy / "MANIFEST.json").write_text(json.dumps({"files": {"claim-0001.md": "x"}}),
                                          encoding="utf-8")
    for p in legacy.iterdir():
        p.chmod(0o444)
    legacy.chmod(0o555)
    with kr.operation(writer="test", root=root):
        cid = cl.add(TEXT, SRC, Q, confidence="high", actor="migration", method="migration",
                     legacy_id="claim-0001", root=root, _migrating=True)
    out = _erase(root, cid, legacy_dir=legacy)
    assert "4812" not in (legacy / "claim-0001.md").read_text(encoding="utf-8")
    manifest = json.loads((legacy / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["erased"]["claim-0001.md"]["reason_class"] == "client_request"
    assert stat.S_IMODE((legacy / "claim-0001.md").stat().st_mode) == 0o444
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o555
    assert any(r["kind"] == "legacy_copy" and r["action"] == "stubbed" for r in out["residue"])


def test_erase_lists_snapshots_holding_copies(root):
    cid = _add(root)
    snap = root / "data" / "snapshots" / "s1"
    snap.mkdir(parents=True)
    (snap / "claims.sqlite").write_bytes(cs.db_path(root).read_bytes())
    out = _erase(root, cid)
    kinds = {r["kind"] for r in out["residue"]}
    assert {"snapshot", "time_machine"} <= kinds
    assert any(r["kind"] == "snapshot" and "s1" in r["where"] for r in out["residue"])


def test_erased_claim_hidden_from_views(root):
    from scripts import chunk
    cid = _add(root)
    _erase(root, cid)
    files = {f.replace("\\", "/") for f in chunk.scan_markdown_files(root)}
    assert f"wiki/claims/{cid}.md" not in files


def test_erase_reason_class_is_closed(root):
    cid = _add(root)
    with kr.operation(writer="test", root=root):
        with pytest.raises(ValueError):
            cl.erase(cid, reason_class="because the client said so", actor="a", root=root)


def test_erase_cli(root):
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    cid = _add(root)
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(repo))
    res = subprocess.run([sys.executable, str(repo / "scripts" / "cli.py"), "claim", "erase", cid,
                          "--reason-class", "client_request"],
                         capture_output=True, text=True, env=env, cwd=str(repo))
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["rev"] == 2 and "not guaranteed removed" in out["bound"]
    assert cl.current(root)[cid].status == "erased"
