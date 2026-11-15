"""RTBench final-review fixes (2026-11-15): each test reproduces one finding."""

import argparse
import json

import pytest

from scripts.rtbench import cli as rcli
from scripts.rtbench.canon import sha256_file
from scripts.rtbench.generate import DEV_SEEDS, write_split


def _args(**kw):
    base = dict(adapter="oracle", track="S", split="dev", cases_dir=None, report_dir=None, work_dir=None, limit=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def split(tmp_path, monkeypatch):
    """A 5-case split registered in a private registry, as `split` (dev or holdout)."""
    def make(name):
        d = tmp_path / name
        m = write_split(name, DEV_SEEDS[:5], d)
        reg = tmp_path / "datasets.json"
        reg.write_text(json.dumps({"datasets": {f"rtbench_{name}": {
            "state": "DEV" if name == "dev" else "HOLDOUT", "sha256": m["sha256"],
            "gold_sha256": m["gold_sha256"], "n_items": 5}}}))
        monkeypatch.setattr(rcli, "REGISTRY", reg)
        return d
    return make


def test_an_edited_gold_file_is_refused(split, tmp_path, capsys):            # finding 1
    d = split("dev")
    g = d / "dev.gold.jsonl"
    g.write_text(g.read_text().replace('"S":"none"', '"S":"tampered"', 1))
    assert rcli._run(_args(cases_dir=str(d), report_dir=str(tmp_path / "r"))) == 1
    assert "gold" in capsys.readouterr().err


def test_an_unregistered_holdout_is_refused(tmp_path, monkeypatch):          # finding 1
    d = tmp_path / "h"
    write_split("holdout", DEV_SEEDS[:3], d)
    monkeypatch.setattr(rcli, "REGISTRY", tmp_path / "missing.json")
    assert rcli._run(_args(split="holdout", cases_dir=str(d), report_dir=str(tmp_path / "r"))) == 1


def test_limit_is_refused_on_holdout(split, tmp_path):                       # finding 2
    d = split("holdout")
    assert rcli._run(_args(split="holdout", cases_dir=str(d), report_dir=str(tmp_path / "r"), limit=2)) == 2


def test_a_sealed_cli_run_leaves_no_items(split, tmp_path, capsys):          # finding 7 (CLI path)
    d = split("holdout")
    assert rcli._run(_args(adapter="overwrite", split="holdout", cases_dir=str(d), report_dir=str(tmp_path / "r"))) == 0
    text = capsys.readouterr().out + "".join(p.read_text() for p in (tmp_path / "r").iterdir())
    assert "holdout-0" not in text and "qid" not in text and "misses" not in text


def test_prose_set_answers_are_scored():                                     # finding 3
    from scripts.rtbench.score import normalise, score_answers
    assert normalise("none") == normalise([]) or score_answers(
        [{"type": "Q1", "gold": [], "answer": "none"}])["by_type"]["Q1"]["accuracy"] == 1.0
    r = score_answers([{"type": "Q1", "gold": ["100 staff", "4210 tonnes"], "answer": "4,210 tonnes, 100 staff"},
                       {"type": "Q1", "gold": [], "answer": "None."},
                       {"type": "Q1", "gold": ["[erased]", "7 vehicles"], "answer": "7 vehicles, [erased]"}])
    assert r["by_type"]["Q1"]["accuracy"] == 1.0


def test_a_none_inside_a_list_answer_is_wrong_not_a_crash():               # finding 3 (minor folded in)
    from scripts.rtbench.score import score_answers
    r = score_answers([{"type": "Q1", "gold": ["a"], "answer": ["a", None]}])
    assert r["by_type"]["Q1"]["accuracy"] == 0.0


def test_a_report_without_a_control_renders():                             # finding 3 (minor folded in)
    rep = {"adapter": "x", "track": "S", "n_cases": 1, "n_questions": 1, "errors": 0,
           "score": {"headline": None, "control": None, "by_type": {}}}
    assert "n/a" in rcli._markdown(rep, sealed=False)


def test_a_used_work_dir_is_refused(tmp_path):                             # finding 4
    from scripts.rtbench.zurvan_adapter import ZurvanAdapter
    (tmp_path / "case-1").mkdir()
    (tmp_path / "case-1" / "leftover").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        ZurvanAdapter(tmp_path)


def _rep(head, ctrl=1.0, errors=0):
    return {"score": {"headline": head, "control": ctrl}, "errors": errors}


def test_validity_needs_every_report_and_no_errors():                      # finding 5
    from scripts.rtbench.run import validity
    full = {(n, t): _rep(1.0 if n == "oracle" else 0.3) for n in ("oracle", "overwrite", "append_only") for t in "SN"}
    assert validity(full)["pass"]
    assert not validity({("oracle", "S"): _rep(1.0), ("oracle", "N"): _rep(1.0)})["pass"]
    broken = dict(full)
    broken[("overwrite", "N")] = _rep(0.0, errors=12)
    assert not validity(broken)["pass"]


def test_validity_runs_from_the_cli(tmp_path):                             # finding 5
    d = tmp_path / "d"
    write_split("dev", DEV_SEEDS, d)           # the registered split; a subset is refused as MISMATCH
    out = rcli.validity_command(argparse.Namespace(split="dev", cases_dir=str(d), limit=20))
    assert out["pass"] is True and len(out["checks"]) == 12
