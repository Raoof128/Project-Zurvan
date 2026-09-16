"""The gauntlet's own guard: every declared row must have a test, always.

A gauntlet of `importorskip` tests is invisible — it reports green while proving
nothing, and a row can be dropped without anyone noticing. This file runs
UNCONDITIONALLY. It does not check whether the gauntlet passes; it checks that
the gauntlet still EXISTS and still covers every row the review declared.

If you add a row to the review table, this fails until a test exists for it.
If you delete a test, this fails until the row is formally retired here.
"""
import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

# The 17 declared rows -> the test function that discharges each one.
DECLARED = {
    "fts-equivalence":       ("test_fts_candidates_gauntlet.py", "test_fts_limit_above_total_matches_is_identical_to_the_unbounded_path"),
    "fts-cutoff-tie":        ("test_fts_candidates_gauntlet.py", "test_equal_bm25_at_the_cutoff_admits_a_deterministic_candidate_set"),
    "fts-ranking":           ("test_fts_candidates_gauntlet.py", "test_no_new_top3_failure_and_aggregates_hold_their_declared_floor"),
    "fts-timing":            ("test_fts_candidates_gauntlet.py", "test_timing_is_measured_in_a_fresh_process_per_variant"),
    "boundary-T-edges":      ("test_boundary_gauntlet.py", "test_threshold_edges_T_minus_1_T_and_T_plus_1"),
    "d1-duplicate-posting":  ("test_boundary_gauntlet.py", "test_one_fingerprint_with_many_origins_cannot_hide_a_protected_match"),
    "d2-casing":             ("test_boundary_gauntlet.py", "test_d2_literals_match_under_every_declared_casefold"),
    "d3-laundering":         ("test_boundary_gauntlet.py", "test_protected_text_in_an_unapproved_public_file_stays_blocked"),
    "d3-partial-overlap":    ("test_boundary_gauntlet.py", "test_a_public_subspan_does_not_exempt_the_longer_protected_residual"),
    "multi-sink":            ("test_boundary_gauntlet.py", "test_multi_label_sink_uses_deny_overrides_allow"),
    "stale-index":           ("test_boundary_gauntlet.py", "test_stale_index_fails_closed_and_never_reports_clean"),
    "git-boundary":          ("test_boundary_gauntlet.py", "test_span_assembled_across_two_changed_hunks_still_blocks"),
    "rename-copy":           ("test_boundary_gauntlet.py", "test_protected_blob_renamed_without_textual_diff_still_blocks"),
    "release-isolation":     ("test_boundary_gauntlet.py", "test_a_release_for_one_sink_does_not_declassify_another"),
    "instrument":            ("test_boundary_gauntlet.py", "test_instrument_text_can_never_reach_an_indexed_or_public_artefact"),
    "gate-self-protection":  ("test_boundary_gauntlet.py", "test_corrupt_boundary_state_fails_closed_not_clean"),
    "clean-corpus-fp":       ("test_boundary_gauntlet.py", "test_clean_corpus_stays_under_the_predeclared_false_positive_ceiling"),
}


def _functions(filename):
    tree = ast.parse((TESTS / filename).read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


@pytest.mark.parametrize("row", sorted(DECLARED))
def test_every_declared_gauntlet_row_has_a_test(row):
    filename, func = DECLARED[row]
    assert (TESTS / filename).is_file(), f"{filename} is missing"
    assert func in _functions(filename), f"row '{row}' has no test: {func}"


def test_the_gauntlet_covers_seventeen_rows():
    """The count is part of the contract; silently shrinking it is the failure."""
    assert len(DECLARED) == 17, f"gauntlet declares {len(DECLARED)} rows, expected 17"


def test_the_gauntlet_does_not_itself_leak():
    """A leak detector's tests must not contain protected material.

    Checked against the live denylist and the live instrument, so this tightens
    automatically as either grows.
    """
    import json, re
    names = [l.strip() for l in Path(".private-projects").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    gold = [json.loads(l)["query"] for l in open("eval/search_gold.jsonl") if l.strip()]
    for filename in sorted({f for f, _ in DECLARED.values()} | {Path(__file__).name}):
        text = (TESTS / filename).read_text(encoding="utf-8")
        hit = [n for n in names if re.search(re.escape(n), text, re.I)]
        assert not hit, f"{filename} contains private project name(s): {hit}"
        echo = [q for q in gold if q.lower() in text.lower()]
        assert not echo, f"{filename} echoes instrument item(s) verbatim"
