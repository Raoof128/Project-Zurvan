"""RTBench (IOU-Z2-5) closure matrix: every acceptance row R1-R9 names tests that exist (spec §10)."""
import json
from pathlib import Path
from scripts.rtbench.generate import DEV_SEEDS, write_split

REG = json.loads(Path("eval/datasets.json").read_text())["datasets"]

def test_dev_regenerates_to_the_registered_digest(tmp_path):          # R1, R9
    m = write_split("dev", DEV_SEEDS, tmp_path)
    e = REG["rtbench_dev"]
    assert (e["state"], e["sha256"], e["gold_sha256"], e["n_items"]) == ("DEV", m["sha256"], m["gold_sha256"], 200)

def test_holdout_is_registered_by_whole_file_digest_only():            # R9
    e = REG["rtbench_holdout"]
    assert e["state"] == "HOLDOUT" and set(e) <= {"state", "sha256", "gold_sha256", "n_items", "generator_version"}

MATRIX = {
    "R1": ["tests/test_rtbench_generate.py::test_write_split_is_byte_identical",
           "tests/test_rtbench_closure.py::test_dev_regenerates_to_the_registered_digest"],
    "R2": ["tests/test_rtbench_oracle.py::test_oracle_imports_nothing_from_zurvan"],
    "R3": ["tests/test_rtbench_oracle.py::test_hand_case_supersede_chain",
           "tests/test_rtbench_oracle.py::test_hand_case_erase_after_belief",
           "tests/test_rtbench_oracle.py::test_hand_case_abort_is_never_believed",
           "tests/test_rtbench_oracle.py::test_hand_case_bitemporal",
           "tests/test_rtbench_oracle.py::test_hand_case_revise_and_open_interval",
           "tests/test_rtbench_oracle.py::test_hand_case_double_supersede_then_retract"],
    "R4": ["tests/test_rtbench_render.py::test_round_trip_reproduces_every_answer"],
    "R5": ["tests/test_rtbench_adapters.py::test_oracle_scores_full_marks_on_both_tracks"],
    "R6": ["tests/test_rtbench_adapters.py::test_baselines_stay_under_the_bar_on_both_tracks",
           "tests/test_rtbench_review_fixes.py::test_validity_needs_every_report_and_no_errors",
           "tests/test_rtbench_review_fixes.py::test_validity_runs_from_the_cli"],
    "R7": ["tests/test_rtbench_zurvan.py::test_zurvan_runs_a_generated_sample"],
    "R8": ["tests/test_rtbench_zurvan.py::test_abort_reconciles_in_temp_root"],
    "R9": ["tests/test_rtbench_closure.py::test_holdout_is_registered_by_whole_file_digest_only",
           "tests/test_rtbench_cli.py::test_holdout_report_has_no_items",
           "tests/test_rtbench_review_fixes.py::test_a_sealed_cli_run_leaves_no_items",
           "tests/test_rtbench_review_fixes.py::test_an_edited_gold_file_is_refused",
           "tests/test_rtbench_review_fixes.py::test_limit_is_refused_on_holdout"],
}

def test_every_acceptance_row_names_tests_that_exist():
    for row, ids in MATRIX.items():
        for i in ids:
            f, name = i.split("::")
            assert f"def {name}(" in Path(f).read_text(), (row, i)
