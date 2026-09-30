import json

import pytest

from scripts import eval_runs as er


class FakeSnap:
    def __init__(self, tmp_path):
        self.index_path = tmp_path / "search.sqlite"
        self.index_path.write_bytes(b"")
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"coherence": {"status": "VERIFIED", "revision": 7, "atomicity": "MANAGED",
                           "purpose": "measurement", "reason": None}}))
        self.released = False

    def release(self):
        self.released = True


def _gold(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"query":"q","expected_paths":["wiki/a.md"]}\n')
    return p


def test_equal_digests_on_one_snapshot_and_release(tmp_path):
    snap = FakeSnap(tmp_path)
    calls = []

    def fake_capture(**kw):
        calls.append(kw)
        return snap
    out = er.reproduce(_gold(tmp_path), capture_fn=fake_capture,
                       evaluate_fn=lambda ds, index_path, hybrid: ({"result_digest": "same"}, []))
    assert out["equal"] is True and out["digests"] == ["same", "same"]
    assert out["coherence"]["status"] == "VERIFIED"
    assert calls[0]["required"] == ("wiki", "search", "graph")
    assert calls[0]["purpose"] == "measurement"
    # capture() refuses components= together with required= (SnapshotComponentError)
    assert "components" not in calls[0]
    assert snap.released


def test_differing_digests_are_reported_not_hidden(tmp_path):
    snap = FakeSnap(tmp_path)
    digests = iter(["one", "two"])
    out = er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: snap,
                       evaluate_fn=lambda *a, **k: ({"result_digest": next(digests)}, []))
    assert out["equal"] is False and snap.released


def test_snapshot_released_even_when_evaluation_raises(tmp_path):
    snap = FakeSnap(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: snap, evaluate_fn=boom)
    assert snap.released


def test_reproduce_call_is_accepted_by_the_real_capture_contract(tmp_path):
    # The fakes above accept any keywords; this pins the call against the real
    # signature, which refuses `components=` together with `required=`.
    import inspect

    from scripts import measurement_snapshot as ms
    seen = {}
    er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: seen.update(kw) or FakeSnap(tmp_path),
                 evaluate_fn=lambda *a, **k: ({"result_digest": "d"}, []))
    inspect.signature(ms.capture).bind(**seen)
    assert not ({"components", "required"} <= set(seen))


@pytest.mark.parametrize("equal,status,code", [(True, "VERIFIED", 0), (False, "VERIFIED", 1),
                                               (True, "UNVERIFIED", 1)])
def test_cli_reproduce_prints_json_and_sets_exit_code(monkeypatch, capsys, equal, status, code):
    # cli.py's dispatcher has a function-local `import json`, which makes `json`
    # unbound in any branch that did not execute it (measured: a 2-minute
    # live run died printing its result).
    import scripts.cli as cli
    monkeypatch.setattr(er, "reproduce", lambda gold: {"equal": equal, "digests": ["d", "d"],
                                                        "coherence": {"status": status},
                                                        "snapshot_index": "x"})
    monkeypatch.setattr(cli, "_resolved_gold", lambda g: "g")
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "reproduce"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code
    assert json.loads(capsys.readouterr().out)["equal"] is equal
