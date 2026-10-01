"""Z1b Task 14 tests: DEV set tooling (spec s6.5). DEV is public, but its tooling still reports counts and
indexes only, refuses to refreeze silently, and never touches the sealed side."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import instruments
from scripts import z1b_dev as D
from scripts.eval_seal import spec as seal_spec

REPO = Path(__file__).resolve().parents[1]
SPEC = seal_spec.load_spec(REPO / "eval" / "z1b" / "spec.json")
STRATA = seal_spec.STRATA


def _t14_item(stratum: str, i: int, *, query=None, provenance=True) -> dict:
    grading = {e["id"]: e["grading"] for e in SPEC["strata"]}[stratum]
    item = {
        "id": hashlib.sha256(("t14-%s-%d" % (stratum, i)).encode()).hexdigest()[:32],
        "stratum": stratum,
        "query": query if query is not None else "t14 query %s number %d zebra" % (stratum, i),
        "targets": ([] if grading == "abstain" else
                    [{"page": "wiki/t14-%s-%d.md" % (stratum, i), "source_id": None,
                      "locator": {"kind": "page", "value": ""}}]),
        "grading": grading,
        "difficulty": ("easy", "medium", "hard")[i % 3],
    }
    if provenance:
        item["provenance"] = {"author": "dev", "reviewer": "dev"}
    return item


def _t14_write(path: Path, items) -> Path:
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    return path


def _t14_full(tmp_path, per=12, name="dev.jsonl") -> Path:
    return _t14_write(tmp_path / name, [_t14_item(s, i) for s in STRATA for i in range(per)])


def _t14_registry(tmp_path) -> Path:
    reg = tmp_path / "datasets.json"
    reg.write_text(json.dumps({
        "schema": "zurvan.eval.datasets.v1", "note": "n",
        "datasets": {"search_gold": {"state": "RETIRED", "sha256": "a" * 64, "n_items": 50}}}, indent=2) + "\n")
    return reg


def test_dev_path_follows_instruments_dir_and_never_the_legacy_eval_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(instruments, "INSTRUMENT_DIR", tmp_path / "inst")
    assert D.dev_path() == tmp_path / "inst" / "z1-dev.jsonl"
    with pytest.raises(instruments.InstrumentMissing):
        D.dev_path(must_exist=True)
    (tmp_path / "inst").mkdir()
    (tmp_path / "inst" / "z1-dev.jsonl").write_text("")
    assert D.dev_path(must_exist=True).name == "z1-dev.jsonl"


def test_dev_validate_reports_counts_per_stratum_and_never_items(tmp_path, capsys):
    secret = "UNIQUE-QUERY-SENTINEL-8841"
    items = [_t14_item(s, i) for s in STRATA for i in range(12)]
    items[3] = _t14_item(items[3]["stratum"], 3, query="contains " + secret)
    out = D.dev_validate(_t14_write(tmp_path / "d.jsonl", items), SPEC)
    assert out["n_items"] == 192 and out["invalid"] == 0 and out["ok"] is True
    assert out["per_stratum"] == {s: 12 for s in STRATA}
    assert secret not in json.dumps(out)
    assert "t14 query" not in json.dumps(out) and "wiki/t14" not in json.dumps(out)
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""


def test_dev_validate_flags_invalid_and_short_by_index_only(tmp_path):
    secret = "BAD-ITEM-SENTINEL-5520"
    items = [_t14_item(s, i) for s in STRATA for i in range(12)]
    bad = _t14_item("path", 99, query="   ")
    bad["notes"] = secret                       # an unknown key makes the item invalid
    items.append(bad)
    p = _t14_write(tmp_path / "d.jsonl", items)
    with p.open("a") as fh:
        fh.write("{not json " + secret + "\n")
    out = D.dev_validate(p, SPEC)
    assert out["invalid"] == 2 and out["ok"] is False
    assert out["invalid_indexes"] == [192, 193]
    assert secret not in json.dumps(out)
    assert set(out["invalid_fields"]) <= {"keys", "query", "json"}
    short = _t14_write(tmp_path / "s.jsonl", [_t14_item("hash", i) for i in range(4)])
    out2 = D.dev_validate(short, SPEC)
    assert out2["ok"] is False
    assert {"stratum": "hash", "have": 4, "need": 10} in out2["short"]
    assert {"stratum": "path", "have": 0, "need": 10} in out2["short"]


def test_dev_validate_counts_duplicate_ids_and_queries_without_showing_them(tmp_path):
    a = _t14_item("hash", 1)
    b = dict(_t14_item("hash", 2), id=a["id"])                       # duplicate id
    c = _t14_item("hash", 3, query=a["query"].upper())               # duplicate query (case)
    out = D.dev_validate(_t14_write(tmp_path / "d.jsonl", [a, b, c]), SPEC)
    assert out["duplicate_ids"] == 1 and out["duplicate_queries"] == 1 and out["ok"] is False


def test_dev_validate_accepts_items_without_provenance_but_not_other_missing_fields(tmp_path):
    ok = [_t14_item(s, i, provenance=False) for s in STRATA for i in range(10)]
    assert D.dev_validate(_t14_write(tmp_path / "a.jsonl", ok), SPEC)["ok"] is True
    broken = [dict(i) for i in ok]
    del broken[0]["difficulty"]
    out = D.dev_validate(_t14_write(tmp_path / "b.jsonl", broken), SPEC)
    assert out["invalid"] == 1 and out["invalid_fields"] == {"difficulty": 1}


def test_dev_freeze_registers_z1b_dev_with_the_whole_file_digest(tmp_path):
    p, reg = _t14_full(tmp_path), _t14_registry(tmp_path)
    digest = D.dev_freeze(p, reg, spec=SPEC)
    assert digest == hashlib.sha256(p.read_bytes()).hexdigest()
    data = json.loads(reg.read_text())
    assert data["datasets"]["z1b_dev"]["state"] == "DEV"
    assert data["datasets"]["z1b_dev"]["sha256"] == digest
    assert data["datasets"]["z1b_dev"]["n_items"] == 192
    assert data["datasets"]["search_gold"]["state"] == "RETIRED"      # others untouched
    assert json.dumps(data).count(hashlib.sha256(b"x").hexdigest()) == 0
    # no per-item digest anywhere in the registry: only whole-file digests
    assert sorted(k for k, v in data["datasets"].items() if "sha256" in v) == ["search_gold", "z1b_dev"]


def test_dev_freeze_is_idempotent_for_the_same_bytes(tmp_path):
    p, reg = _t14_full(tmp_path), _t14_registry(tmp_path)
    d1 = D.dev_freeze(p, reg, spec=SPEC)
    before = reg.read_bytes()
    assert D.dev_freeze(p, reg, spec=SPEC) == d1
    assert reg.read_bytes() == before


def test_dev_freeze_refuses_a_different_digest_without_new_suite_version(tmp_path):
    p, reg = _t14_full(tmp_path), _t14_registry(tmp_path)
    old = D.dev_freeze(p, reg, spec=SPEC)
    p2 = _t14_full(tmp_path, per=13, name="dev2.jsonl")
    before = reg.read_bytes()
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_freeze(p2, reg, spec=SPEC)
    assert ei.value.code == "DEV_ALREADY_FROZEN"
    assert reg.read_bytes() == before                                  # registry untouched
    new = D.dev_freeze(p2, reg, spec=SPEC, new_suite_version=True)
    assert new != old
    entry = json.loads(reg.read_text())["datasets"]["z1b_dev"]
    assert entry["sha256"] == new and entry["superseded"] == [old]


def test_dev_freeze_refuses_invalid_or_duplicated_files_and_leaves_registry_alone(tmp_path):
    reg = _t14_registry(tmp_path)
    before = reg.read_bytes()
    bad = _t14_write(tmp_path / "bad.jsonl", [_t14_item("hash", 1, query="")])
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_freeze(bad, reg, spec=SPEC)
    assert ei.value.code == "DEV_INVALID"
    dup = _t14_write(tmp_path / "dup.jsonl", [_t14_item("hash", 1), _t14_item("hash", 1)])
    with pytest.raises(D.DevRefusal):
        D.dev_freeze(dup, reg, spec=SPEC)
    assert reg.read_bytes() == before


def test_dev_freeze_refuses_a_digest_already_registered_under_another_name(tmp_path):
    p, reg = _t14_full(tmp_path), _t14_registry(tmp_path)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    data = json.loads(reg.read_text())
    data["datasets"]["z1b_holdout"] = {"state": "HOLDOUT", "sha256": digest}
    reg.write_text(json.dumps(data))
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_freeze(p, reg, spec=SPEC)
    assert ei.value.code == "DEV_DIGEST_CONFLICT"


def test_dev_freeze_refuses_a_missing_or_corrupt_registry(tmp_path):
    p = _t14_full(tmp_path)
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_freeze(p, tmp_path / "nope.json", spec=SPEC)
    assert ei.value.code == "REGISTRY_INVALID"
    junk = tmp_path / "junk.json"
    junk.write_text("[1,2")
    with pytest.raises(D.DevRefusal):
        D.dev_freeze(p, junk, spec=SPEC)


def test_dev_ref_export_copies_identical_bytes_and_returns_the_digest(tmp_path):
    p = _t14_full(tmp_path)
    dest = tmp_path / "sets" / "dev-ref.jsonl"
    digest = D.dev_ref_export(p, dest, spec=SPEC)
    assert dest.read_bytes() == p.read_bytes()
    assert digest == hashlib.sha256(p.read_bytes()).hexdigest()
    assert (os.stat(dest).st_mode & 0o777) == 0o644
    assert not [f for f in dest.parent.iterdir() if f.name != "dev-ref.jsonl"]   # no temp litter


def test_dev_ref_export_requires_the_frozen_digest_when_a_registry_is_given(tmp_path):
    p, reg = _t14_full(tmp_path), _t14_registry(tmp_path)
    dest = tmp_path / "out.jsonl"
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_ref_export(p, dest, spec=SPEC, registry_path=reg)
    assert ei.value.code == "DEV_NOT_FROZEN" and not dest.exists()
    D.dev_freeze(p, reg, spec=SPEC)
    assert D.dev_ref_export(p, dest, spec=SPEC, registry_path=reg)
    p.write_text(p.read_text() + json.dumps(_t14_item("hash", 77)) + "\n")      # edited after the freeze
    dest2 = tmp_path / "out2.jsonl"
    with pytest.raises(D.DevRefusal) as ei2:
        D.dev_ref_export(p, dest2, spec=SPEC, registry_path=reg)
    assert ei2.value.code == "DEV_NOT_FROZEN" and not dest2.exists()


def test_dev_ref_export_refuses_invalid_files_and_self_overwrite(tmp_path):
    bad = _t14_write(tmp_path / "bad.jsonl", [_t14_item("hash", 1, query="")])
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_ref_export(bad, tmp_path / "o.jsonl", spec=SPEC)
    assert ei.value.code == "DEV_INVALID"
    good = _t14_full(tmp_path)
    with pytest.raises(D.DevRefusal):
        D.dev_ref_export(good, good, spec=SPEC)


def test_dev_finalize_adds_ids_grading_and_provenance_deterministically(tmp_path):
    raw = [{"stratum": s, "query": "raw %s %d" % (s, i), "difficulty": "easy",
            "targets": ([] if s == "not_found" else
                        [{"page": "wiki/r%d.md" % i, "locator": {"kind": "page", "value": ""}}])}
           for s in STRATA for i in range(10)]
    rawp = _t14_write(tmp_path / "raw.jsonl", raw)
    out1, out2 = tmp_path / "f1.jsonl", tmp_path / "f2.jsonl"
    D.dev_finalize(rawp, out1, spec=SPEC)
    D.dev_finalize(rawp, out2, spec=SPEC)
    assert out1.read_bytes() == out2.read_bytes()
    res = D.dev_validate(out1, SPEC)
    assert res["ok"] is True and res["n_items"] == 160 and res["duplicate_ids"] == 0
    first = json.loads(out1.read_text().splitlines()[0])
    assert first["provenance"] == {"author": "dev", "reviewer": "dev"}
    assert first["targets"][0]["source_id"] is None


def test_dev_finalize_never_overwrites_an_existing_destination(tmp_path):
    rawp = _t14_write(tmp_path / "raw.jsonl", [{"stratum": "hash", "query": "q", "difficulty": "easy",
                                                "targets": [{"page": "wiki/a.md",
                                                             "locator": {"kind": "page", "value": ""}}]}])
    dest = tmp_path / "f.jsonl"
    dest.write_text("keep")
    with pytest.raises(D.DevRefusal) as ei:
        D.dev_finalize(rawp, dest, spec=SPEC)
    assert ei.value.code == "DEV_EXISTS" and dest.read_text() == "keep"


def test_the_tracked_dev_author_prompt_is_public_and_content_free():
    text = (REPO / "eval" / "z1b" / "prompts" / "dev-author.md").read_text()
    for sid in STRATA:
        assert sid in text
    assert "z1-dev-raw.jsonl" in text and "~/.zurvan/instruments" in text
    assert "```" not in text            # no fenced corpus excerpts; the rubric is prose and schema only
