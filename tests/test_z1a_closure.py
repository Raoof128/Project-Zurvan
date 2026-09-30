"""Z1a closure matrix (design §9, "Z1a closes when"): each gate names the tests
that demonstrate it. `tests/` is not a package, so tests are found by
scanning files (as test_z0_closure does), never by importing `tests.*`.

This matrix proves each gate's tests exist; it is not by itself a closure
claim. Gate 6 closes on the guard function because the promotion workflow is
Z1b scope."""
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

MATRIX = {
    1: [("test_trace_modes_retrieval.py", "test_summary_trace_contains_no_query_text_anywhere")],
    2: [("test_trace_privacy.py", "test_trace_files_and_dirs_are_private")],
    3: [("test_trace_privacy.py", "test_prune_dry_run_on_live_repo_never_selects_tracked")],
    4: [("test_stage_timings.py", "test_timings_are_filled_and_ranking_unchanged")],
    5: [
        ("test_eval_runs.py", "test_save_run_refuses_holdout_before_writing"),
        ("test_eval_runs.py", "test_movers_refuses_a_run_file_claiming_a_sealed_state"),
        ("test_eval_runs.py", "test_movers_refuses_a_dataset_promoted_to_holdout_after_the_run"),
    ],
    6: [
        ("test_eval_datasets.py", "test_registry_retires_the_burned_instrument"),
        ("test_eval_datasets.py", "test_only_holdout_can_gate_a_promotion"),
    ],
    7: [("test_eval_reproduce.py", "test_equal_digests_on_one_snapshot_and_release")],
    8: [
        ("test_instruments.py", "test_the_manifest_cannot_reconstruct_the_items"),
        ("test_instruments.py", "test_the_live_instrument_is_not_tracked_by_git"),
        ("test_trace_modes.py", "test_committed_v1_traces_have_no_schema_issue"),
    ],
}

# Gates that ALSO need the Task 9 live run before Z1a may be called closed.
LIVE_PROOF = {
    1: "live `search --trace` writes a summary trace with no query text, 0600, no Markdown twin",
    4: "BASE-vs-HEAD top-10 lists byte-identical on one VERIFIED snapshot (ranking_proof)",
    7: "`zurvan eval reproduce` on the live store: equal digests over all gold items, VERIFIED",
    8: "full suite 0 failed on merged main (the worktree's 15 environmental failures cannot occur there)",
}


@pytest.mark.parametrize("gate", sorted(MATRIX))
def test_every_z1a_gate_names_tests_that_exist(gate):
    for filename, name in MATRIX[gate]:
        source = (TESTS / filename).read_text(encoding="utf-8")
        assert re.search(rf"^def {name}\(", source, re.M), f"gate {gate}: {filename}::{name}"


def test_live_proof_gates_are_exactly_1_4_7_8():
    assert set(LIVE_PROOF) == {1, 4, 7, 8}
