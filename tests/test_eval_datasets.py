import hashlib
import json

import pytest

from scripts import eval_datasets as ed
from scripts.config import PROJECT_ROOT


def test_registry_retires_the_burned_instrument():
    reg = json.loads((PROJECT_ROOT / "eval" / "datasets.json").read_text())
    manifest = json.loads((PROJECT_ROOT / "eval" / "search_gold.manifest.json").read_text())
    entry = reg["datasets"]["search_gold"]
    assert entry["state"] == "RETIRED"
    assert entry["sha256"] == manifest["sha256"]
    # whole-file digests only: nothing per item (confirmation-oracle rule)
    assert set(entry) <= {"state", "sha256", "n_items", "retired_on", "reason"}


def _file(tmp_path, body=b'{"query":"q","expected_paths":["p"]}\n'):
    p = tmp_path / "g.jsonl"
    p.write_bytes(body)
    return p, hashlib.sha256(body).hexdigest()


def test_dataset_for_file_matches_by_whole_file_digest(tmp_path, monkeypatch):
    p, digest = _file(tmp_path)
    monkeypatch.setattr(ed, "_load_registry",
                        lambda: {"datasets": {"x": {"state": "RETIRED", "sha256": digest}}})
    assert ed.dataset_for_file(p) == ed.DatasetInfo("x", "RETIRED", digest)


def test_unregistered_file_is_dev(tmp_path, monkeypatch):
    p, digest = _file(tmp_path)
    monkeypatch.setattr(ed, "_load_registry", lambda: {"datasets": {}})
    assert ed.dataset_for_file(p) == ed.DatasetInfo("unregistered", "DEV", digest)


@pytest.mark.parametrize("state", ["DEV", "RETIRED", "RESERVE"])
def test_only_holdout_can_gate_a_promotion(state):
    with pytest.raises(ed.DatasetNotEligible) as exc:
        ed.require_promotion_eligible(ed.DatasetInfo("x", state, "0" * 64))
    assert exc.value.code == "NOT_PROMOTION_ELIGIBLE"
    ed.require_promotion_eligible(ed.DatasetInfo("x", "HOLDOUT", "0" * 64))


@pytest.mark.parametrize("state", ["HOLDOUT", "RESERVE"])
def test_sealed_states_never_allow_item_rows(state):
    with pytest.raises(ed.DatasetNotEligible) as exc:
        ed.require_itemised_allowed(ed.DatasetInfo("x", state, "0" * 64))
    assert exc.value.code == "SEALED_DATASET"


def test_unknown_state_in_registry_fails_closed(tmp_path, monkeypatch):
    p, digest = _file(tmp_path)
    monkeypatch.setattr(ed, "_load_registry",
                        lambda: {"datasets": {"x": {"state": "SORT_OF", "sha256": digest}}})
    with pytest.raises(ValueError):
        ed.dataset_for_file(p)
