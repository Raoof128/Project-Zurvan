"""Z1a hardening, eval: the review minors deferred during Z1a (plan,
"Execution record (2026-10-01)"). Synthetic data only; never the instrument."""
import json
import os
import stat

import pytest

import scripts.context_export as ce
import scripts.eval_datasets as ed
import scripts.eval_search as es
from scripts import eval_runs as er
from scripts.eval_datasets import DatasetInfo, DatasetNotEligible

H = "c" * 64


class Snap:
    def __init__(self, tmp_path):
        self.index_path = tmp_path / "snap-1" / "search.sqlite"
        self.index_path.parent.mkdir()
        self.index_path.write_bytes(b"")
        (self.index_path.parent / "manifest.json").write_text(json.dumps(
            {"coherence": {"status": "VERIFIED", "revision": 7, "atomicity": "MANAGED",
                           "purpose": "measurement", "reason": None}}))
        self.released = False

    def release(self):
        self.released = True


def _gold(tmp_path, n=1):
    page = tmp_path / "wiki" / "a.md"  # the gold validator requires the page to exist
    page.parent.mkdir(exist_ok=True)
    page.write_text("x")
    p = tmp_path / "g.jsonl"
    p.write_text("".join(json.dumps({"query": f"q{i}", "expected_paths": [str(page)]}) + "\n"
                         for i in range(n)))
    return p


# --- dataset states: allow-list, validated construction, unique digests -------

def test_itemised_output_is_an_allow_list(monkeypatch):
    # a DatasetInfo cannot even be built with a bogus state now; bypass the
    # constructor to prove the guard itself fails closed on one
    bogus = object.__new__(DatasetInfo)
    object.__setattr__(bogus, "name", "x")
    object.__setattr__(bogus, "state", "SORT_OF")
    object.__setattr__(bogus, "sha256", H)
    with pytest.raises(DatasetNotEligible) as exc:
        ed.require_itemised_allowed(bogus)
    assert exc.value.code == "SEALED_DATASET"
    for state in ("DEV", "RETIRED"):
        ed.require_itemised_allowed(DatasetInfo("x", state, H))


@pytest.mark.parametrize("state,digest", [("SORT_OF", H), ("DEV", "nothex"), ("DEV", "C" * 64)])
def test_dataset_info_is_validated_on_construction(state, digest):
    with pytest.raises(ValueError):
        DatasetInfo("x", state, digest)


def test_duplicate_digest_in_registry_fails_closed(monkeypatch):
    monkeypatch.setattr(ed, "_load_registry", lambda: {"datasets": {
        "a": {"state": "DEV", "sha256": H}, "b": {"state": "HOLDOUT", "sha256": H}}})
    with pytest.raises(ValueError, match="registered twice"):
        ed.dataset_for_digest(H)


# --- rows never ride inside the metrics dict ---------------------------------

def test_keep_rows_returns_rows_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "evaluate", lambda ds, index_path, hybrid: (
        {"queries": 1, "top1_accuracy": 1.0, "top3_accuracy": 1.0, "mrr": 1.0,
         "failures": 0, "failing_items": [], "result_digest": "d"},
        [{"index": 0, "retrieved": ["wiki/a.md"], "first_hit_rank": 1}]))
    metrics, rows = es.run_search_evaluation(str(_gold(tmp_path)), hybrid=True, as_json=True,
                                             index_path="x", keep_rows=True)
    assert rows[0]["index"] == 0
    assert not any(k.startswith("_") for k in metrics)


# --- sealed datasets: no per-item indices anywhere ---------------------------

def test_sealed_dataset_json_output_carries_no_failing_items(tmp_path, monkeypatch, capsys):
    gold = _gold(tmp_path)
    monkeypatch.setattr(ed, "_load_registry", lambda: {"datasets": {
        "h": {"state": "HOLDOUT", "sha256": ed.file_sha256(gold)}}})
    monkeypatch.setattr(es, "evaluate", lambda ds, index_path, hybrid: (
        {"queries": 1, "top1_accuracy": 0.0, "top3_accuracy": 0.0, "mrr": 0.0,
         "failures": 1, "failing_items": [0], "result_digest": "d"}, []))
    metrics = es.run_search_evaluation(str(gold), hybrid=True, as_json=True, index_path="x")
    out = json.loads(capsys.readouterr().out)
    assert "failing_items" not in out and "failing_items" not in metrics
    assert out["failures"] == 1


def test_save_run_on_a_sealed_dataset_exits_2_before_evaluating(tmp_path, monkeypatch, capsys):
    gold = _gold(tmp_path)
    monkeypatch.setattr(ed, "_load_registry", lambda: {"datasets": {
        "h": {"state": "RESERVE", "sha256": ed.file_sha256(gold)}}})
    ran = []
    monkeypatch.setattr(es, "run_search_evaluation", lambda *a, **k: ran.append(1))
    with pytest.raises(SystemExit) as exc:
        es.main(["--gold", str(gold), "--hybrid", "--save-run"])
    assert exc.value.code == 2 and not ran
    assert "SEALED_DATASET" in capsys.readouterr().err


def test_runs_dir_is_created_private(tmp_path):
    runs = tmp_path / "eval-runs"
    er.save_run({"result_digest": "d"}, [], DatasetInfo("x", "DEV", H), runs_dir=runs)
    assert stat.S_IMODE(os.stat(runs).st_mode) == 0o700


def test_cli_movers_exits_2_on_an_invalid_registry_state(tmp_path, monkeypatch, capsys):
    import scripts.cli as cli
    run = {"dataset": {"name": "x", "state": "DEV", "sha256": H}, "rows": []}
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(run))
    b.write_text(json.dumps(run))
    monkeypatch.setattr(ed, "_load_registry", lambda: {"datasets": {
        "x": {"state": "SORT_OF", "sha256": H}}})
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "movers", str(a), str(b)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


# --- reproduce: no vacuous pass, no dangling path, always released ------------

def test_reproduce_refuses_an_empty_gold_set(tmp_path):
    empty = tmp_path / "e.jsonl"
    empty.write_text("")
    with pytest.raises(ValueError, match="no items"):
        er.reproduce(empty, capture_fn=lambda **kw: pytest.fail("captured"))


def test_reproduce_refuses_fewer_than_two_runs(tmp_path):
    with pytest.raises(ValueError, match="at least 2"):
        er.reproduce(_gold(tmp_path), runs=1, capture_fn=lambda **kw: pytest.fail("captured"))


def test_reproduce_reports_items_and_snapshot_id_not_a_released_path(tmp_path):
    snap = Snap(tmp_path)
    out = er.reproduce(_gold(tmp_path, n=3), capture_fn=lambda **kw: snap,
                       evaluate_fn=lambda ds, index_path, hybrid: ({"result_digest": "d"}, []))
    assert out["items"] == 3 and out["snapshot_id"] == "snap-1"
    assert "snapshot_index" not in out and snap.released


def test_reproduce_releases_on_keyboard_interrupt(tmp_path):
    snap = Snap(tmp_path)

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: snap, evaluate_fn=interrupted)
    assert snap.released


def test_cli_reproduce_exits_2_when_snapshot_not_ready(monkeypatch, capsys):
    import scripts.cli as cli
    from scripts.build_snapshot import SnapshotNotReady

    def not_ready(gold):
        raise SnapshotNotReady("graph", "STALE", 296, 302, "behind")

    monkeypatch.setattr(er, "reproduce", not_ready)
    monkeypatch.setattr(cli, "_resolved_gold", lambda g: "g")
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "reproduce"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "SNAPSHOT_NOT_READY" in capsys.readouterr().err


# --- search --explain --json --------------------------------------------------

def test_search_explain_json_carries_timings(monkeypatch, capsys):
    def fake_search(query, hybrid, limit=10, timings=None):
        if timings is not None:
            timings.update({"keyword_scan": 1.0, "total": 2.0})
        return []

    monkeypatch.setattr(ce, "_search_internal", fake_search)
    ce.search_memory("q", as_json=True, explain=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["timings_ms"] == {"keyword_scan": 1.0, "total": 2.0}


# --- keyword mode: store-relative paths, so the digest is portable (item #19) --

def test_keyword_retrieve_returns_store_relative_paths(tmp_path, monkeypatch):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "decisions" / "a.md").write_text("alpha beta")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "b.md").write_text("alpha")
    monkeypatch.setattr(es, "PROJECT_ROOT", tmp_path)
    assert es._keyword_retrieve("alpha beta") == ["wiki/decisions/a.md", "docs/b.md"]


def test_keyword_digest_is_the_same_from_two_checkouts(tmp_path, monkeypatch):
    digests = []
    for name in ("one", "two"):
        root = tmp_path / name
        (root / "wiki").mkdir(parents=True)
        (root / "wiki" / "a.md").write_text("alpha")
        monkeypatch.setattr(es, "PROJECT_ROOT", root)
        metrics, _ = es.evaluate([{"query": "alpha", "expected_paths": ["wiki/a.md"]}],
                                 index_path="unused", hybrid=False)
        digests.append(metrics["result_digest"])
    assert digests[0] == digests[1]
