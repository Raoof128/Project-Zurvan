import os
import stat

import pytest

from scripts import eval_runs as er
from scripts.eval_datasets import DatasetInfo, DatasetNotEligible

DEV = DatasetInfo("unregistered", "DEV", "a" * 64)
METRICS = {"result_digest": "d", "top1_accuracy": 0.5}


def _rows(*ranks):
    return [{"index": i, "retrieved": [], "first_hit_rank": r} for i, r in enumerate(ranks)]


def test_save_run_is_private_and_records_dataset(tmp_path):
    path = er.save_run(METRICS, _rows(1, None), DEV, runs_dir=tmp_path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    run = er.load_run(path)
    assert run["dataset"] == {"name": "unregistered", "state": "DEV", "sha256": "a" * 64}
    assert run["rows"][1]["first_hit_rank"] is None


@pytest.mark.parametrize("state", ["HOLDOUT", "RESERVE"])
def test_save_run_refuses_holdout_before_writing(tmp_path, state):
    with pytest.raises(DatasetNotEligible):
        er.save_run(METRICS, _rows(1), DatasetInfo("h", state, "b" * 64), runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_movers_lists_changed_ranks_by_index_only(tmp_path):
    a = er.load_run(er.save_run(METRICS, _rows(1, 2, None, 4), DEV, runs_dir=tmp_path / "a"))
    b = er.load_run(er.save_run(METRICS, _rows(1, 5, 3, 4), DEV, runs_dir=tmp_path / "b"))
    assert er.movers(a, b) == [{"index": 1, "rank_a": 2, "rank_b": 5},
                               {"index": 2, "rank_a": None, "rank_b": 3}]


def test_movers_refuses_different_datasets(tmp_path):
    other = DatasetInfo("unregistered", "DEV", "c" * 64)
    a = er.load_run(er.save_run(METRICS, _rows(1), DEV, runs_dir=tmp_path / "a"))
    b = er.load_run(er.save_run(METRICS, _rows(1), other, runs_dir=tmp_path / "b"))
    with pytest.raises(DatasetNotEligible) as exc:
        er.movers(a, b)
    assert exc.value.code == "DATASET_MISMATCH"


def test_movers_refuses_a_run_file_claiming_a_sealed_state(tmp_path):
    a = er.load_run(er.save_run(METRICS, _rows(1), DEV, runs_dir=tmp_path))
    forged = dict(a, dataset=dict(a["dataset"], state="HOLDOUT"))
    with pytest.raises(DatasetNotEligible):
        er.movers(forged, forged)


def test_cli_movers_prints_indices_and_refuses_sealed(tmp_path, monkeypatch, capsys):
    import json

    import scripts.cli as cli
    a = er.save_run(METRICS, _rows(1, 2), DEV, runs_dir=tmp_path / "a")
    b = er.save_run(METRICS, _rows(1, 4), DEV, runs_dir=tmp_path / "b")
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "movers", str(a), str(b)])
    cli.main()
    assert "item    1: rank 2 -> 4" in capsys.readouterr().out
    forged = tmp_path / "forged.json"
    body = json.loads(a.read_text())
    body["dataset"]["state"] = "HOLDOUT"
    forged.write_text(json.dumps(body))
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "movers", str(forged), str(forged)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
