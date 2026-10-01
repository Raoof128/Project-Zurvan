import json
import subprocess

import pytest

from scripts.config import PROJECT_ROOT
from scripts.trace_schema import (SCHEMA_VERSION, TRACE_MODES, TraceRecord,
                                  record_from_dict)
from scripts.trace_validate import validate_trace_file

V1 = "zurvan.trace.v1"


def _v2_rec(**kw):
    base = dict(trace_id="trace-20260929T000000Z-modes001", title="t", summary="s")
    base.update(kw)
    return TraceRecord(**base)


def test_new_records_are_v2_and_carry_their_mode():
    data = _v2_rec(trace_mode="summary").to_dict()
    assert data["schema_version"] == SCHEMA_VERSION == "zurvan.trace.v2"
    assert data["trace_mode"] == "summary"
    assert TRACE_MODES == ("summary", "full", "protected_eval")


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="trace_mode"):
        _v2_rec(trace_mode="verbose")


def test_v1_cannot_claim_a_mode():
    # v1 predates modes and stored raw payloads: calling one "summary" would lie.
    with pytest.raises(ValueError, match="trace_mode"):
        _v2_rec(schema_version=V1, trace_mode="summary")


def test_v1_dict_round_trips_byte_for_byte():
    original = {"schema_version": V1, "trace_id": "trace-20260614T000000Z-roundtr1",
                "created_at": "2026-06-14T00:00:00Z", "title": "t", "summary": "s",
                "events": []}
    assert record_from_dict(original).to_dict() == original


def test_validator_accepts_v2_and_rejects_a_bad_mode(tmp_path):
    good = _v2_rec(trace_mode="summary").to_dict()
    path = tmp_path / "good.json"
    path.write_text(json.dumps(good))
    assert validate_trace_file(path).valid

    bad = dict(good, trace_mode="verbose")
    path.write_text(json.dumps(bad))
    result = validate_trace_file(path)
    assert not result.valid and any("trace_mode" in i for i in result.issues)

    missing = {k: v for k, v in good.items() if k != "trace_mode"}
    path.write_text(json.dumps(missing))
    assert any("trace_mode" in i for i in validate_trace_file(path).issues)


def test_committed_v1_traces_have_no_schema_issue():
    # Frozen RQ1 artifacts: the bump must not change how they validate.
    out = subprocess.run(["git", "-C", str(PROJECT_ROOT), "ls-files", "data/traces/*.json"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out, "expected committed traces"
    for rel in out:
        # every committed trace is v1 and stays fully valid (0 issues of any kind
        # at a79b26b), so any validity change at all shows up here
        assert json.loads((PROJECT_ROOT / rel).read_text())["schema_version"] == V1, rel
        assert validate_trace_file(PROJECT_ROOT / rel).issues == [], rel
