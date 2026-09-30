"""Z1a closure matrix (design §9, "Z1a closes when"): each gate names the test
that demonstrates it. `tests/` is not a package, so tests are found by
scanning files (as test_z0_closure does), never by importing `tests.*`."""
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

MATRIX = {
    1: ("test_trace_modes_retrieval.py", "test_summary_trace_contains_no_query_text_anywhere"),
    2: ("test_trace_privacy.py", "test_trace_files_and_dirs_are_private"),
    3: ("test_trace_privacy.py", "test_prune_dry_run_on_live_repo_never_selects_tracked"),
    4: ("test_stage_timings.py", "test_timings_are_filled_and_ranking_unchanged"),
    5: ("test_eval_runs.py", "test_save_run_refuses_holdout_before_writing"),
    6: ("test_eval_datasets.py", "test_only_holdout_can_gate_a_promotion"),
    7: ("test_eval_reproduce.py", "test_equal_digests_on_one_snapshot_and_release"),
    8: ("test_trace_modes.py", "test_committed_v1_traces_have_no_schema_issue"),
}


@pytest.mark.parametrize("gate", sorted(MATRIX))
def test_every_z1a_gate_names_a_test_that_exists(gate):
    filename, name = MATRIX[gate]
    source = (TESTS / filename).read_text(encoding="utf-8")
    assert re.search(rf"^def {name}\(", source, re.M), f"gate {gate}: {filename}::{name}"
