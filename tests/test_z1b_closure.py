"""Z1b Task 17e: closure matrix. Maps each Z1b gate (spec 9) to the tests that demonstrate it.

A renamed or deleted test must not silently unprove a gate (same idea as tests/test_z0_closure.py).
PENDING_WRITER lists tests whose files are written by other tasks (broker, scan, authoring) and are not
in this tree yet; the assembler reconciles them. A pending name that already exists, or a non-pending
name that does not, fails here, so the set cannot drift quietly. LIVE_PROOF lists the gates whose closure
needs the real identity (or the real manifest) and so cannot be shown by a unit test alone.
"""
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

_C = "test_z1b_client.py"
_CAND = "test_z1b_candidate.py"
_LIVE = "test_z1b_seal_live.py"
_OPS = "test_z1b_operator_scripts.py"
_BRK = "test_z1b_broker.py"
_SCAN = "test_z1b_scan.py"
_AUTH = "test_z1b_author.py"

MATRIX = {
    "1": [
        (_C, "test_t13_local_check_all_sealed_paths_eacces_is_ok"),
        (_C, "test_t13_local_check_a_readable_sealed_path_fails_gate_one"),
        (_C, "test_t13_local_check_enoent_means_not_configured_and_never_ok"),
        (_C, "test_t13_local_check_deny_rules_must_cover_every_sealed_path_for_read_and_edit"),
        (_CAND, "test_t9_probe_reports_eacces_readable_enoent"),
        (_CAND, "test_t9_probe_denied_parent_hides_every_child"),
        ("test_z1b_repo_guard.py", "test_t17_settings_deny_rules_cover_every_sealed_path_for_read_and_edit"),
        (_LIVE, "test_t17_live_gate1_dev_identity_gets_eacces_on_every_sealed_path"),
        (_LIVE, "test_t17_live_gate1_candidate_with_the_sandbox_disabled_still_gets_eacces"),
    ],
    "1b": [
        (_OPS, "test_t12_install_modes_owners_and_no_writable_bits"),
        (_OPS, "test_t12_sudoers_has_exactly_two_rules_pinned_to_wrapper_digests"),
        (_OPS, "test_t12_install_pins_binaries_by_digest"),
        (_OPS, "test_t12_setup_time_machine_exclusion_for_keys"),
        (_C, "test_t13_local_check_broker_tree_must_be_root_owned_and_unwritable"),
        (_LIVE, "test_t17_live_gate1b_broker_tree_is_root_owned_and_writable_by_no_identity"),
        (_LIVE, "test_t17_live_gate1b_the_broker_reports_its_own_files_keys_and_exclusions_ok"),
        (_BRK, "test_t10_seal_check_flags_a_writable_broker_file"),
    ],
    "2": [
        ("test_z1b_output.py", "test_t3_fuzz_gate2_no_sealed_string_can_reach_any_output"),
        ("test_z1b_output.py", "test_t3_fuzz_random_json_shaped_documents_are_either_refused_or_clean"),
        ("test_z1b_output.py", "test_t3_fuzz_accepted_documents_contain_only_schema_vocabulary"),
        ("test_z1b_output.py", "test_t3_validate_error_message_never_echoes_input"),
        (_C, "test_t13_broker_stdout_outside_the_closed_schema_is_internal_and_never_printed"),
        (_C, "test_t13_broker_stderr_is_never_shown"),
        (_BRK, "test_t10_broker_stdout_is_only_serializer_output_and_stderr_is_empty"),
    ],
    "3": [
        ("test_z1b_manifest.py", "test_t6_tampered_manifest_byte_is_invalid"),
        ("test_z1b_corpus.py", "test_t5_digest_changes_when_one_byte_changes"),
        ("test_z1b_intake.py", "test_t8_verify_corrupted_pack_is_bundle_invalid"),
        (_OPS, "test_t12_tamper_drill_plan_has_the_oracle_steps_in_order"),
        (_OPS, "test_t12_tamper_drill_expects_refusal_not_success"),
        (_BRK, "test_t10_run_refuses_tampered_manifest_with_manifest_invalid"),
        (_BRK, "test_t10_run_refuses_corpus_mismatch"),
    ],
    "4": [
        (_C, "test_t13_make_bundle_refuses_a_dirty_tree_and_writes_nothing"),
        (_C, "test_t13_run_unknown_commit_is_refused_with_no_broker_call"),
        (_C, "test_t13_make_bundle_then_intake_stage_then_verify_round_trip"),
        ("test_z1b_intake.py", "test_t8_verify_commit_not_a_listed_head_is_candidate_not_frozen"),
        ("test_z1b_ledger.py", "test_t7_cached_key_returns_result_and_does_not_consume_budget"),
        ("test_z1b_ledger.py", "test_t7_eight_reserves_then_ninth_needs_renewal"),
    ],
    "5": [
        (_CAND, "test_t9_sandbox_network_is_denied_for_tcp_udp_dns_and_loopback"),
        (_CAND, "test_t9_sandbox_write_outside_scratch_is_denied"),
        (_CAND, "test_t9_run_end_to_end_under_the_real_sandbox_confines_the_candidate"),
        ("test_z1b_ledger.py", "test_t7_budget_counts_failed_and_crashed_runs_fail_closed"),
        (_BRK, "test_t10_candidate_failure_is_charged_and_marked_failed"),
    ],
    "6": [
        ("test_z1b_ledger.py", "test_t7_burn_is_irreversible_union_with_no_unburn_api"),
        ("test_z1b_ledger.py", "test_t7_incident_open_acknowledge_closed"),
        (_BRK, "test_t10_open_incident_refuses_every_run_with_integrity_incident"),
        (_SCAN, "test_t11_planted_canary_in_a_dev_trace_raises_an_incident_and_burns_the_item"),
        (_SCAN, "test_t11_burned_items_never_reenter_holdout_or_reserve"),
    ],
    "7": [
        ("test_z1b_promotion.py", "test_t16_final_acceptance_without_a_pass_record_is_refused_before_the_broker"),
        ("test_z1b_promotion.py", "test_t16_a_failed_holdout_then_final_acceptance_is_refused_end_to_end"),
        (_C, "test_t13_run_argument_grammar_is_enforced_locally_before_any_call"),
        (_LIVE, "test_t17_live_gate7_reserve_without_final_acceptance_is_refused_by_the_broker_itself"),
        (_LIVE, "test_t17_live_gate7_holdout_never_accepts_final_acceptance"),
        (_BRK, "test_t10_reserve_requires_final_acceptance_and_a_prior_holdout_pass"),
    ],
    "8": [
        ("test_z1b_spec.py", "test_t2_coverage_fifteen_full_one_at_nine_reports_exactly_that_stratum"),
        ("test_z1b_spec.py", "test_t2_coverage_totals_not_ok_when_160_holdout_spread_but_one_stratum_empty"),
        ("test_z1b_spec.py", "test_t2_coverage_empty_pools_are_short_in_every_stratum_and_never_padded"),
        (_AUTH, "test_t15_select_never_pads_a_short_stratum"),
        (_AUTH, "test_t15_manifest_reports_every_short_stratum_honestly"),
    ],
    "9": [
        (_LIVE, "test_t17_live_gate9_precondition_sudo_offers_the_dev_identity_exactly_one_door"),
        (_LIVE, "test_t17_live_gate9_precondition_the_broker_door_answers_only_in_closed_schema"),
        (_LIVE, "test_t17_live_gate9_precondition_the_dev_session_cannot_list_or_read_sealed_homes"),
    ],
}

# names written by other tasks (broker T10, scan T11, authoring T15); the assembler reconciles them
PENDING_WRITER = {(f, n) for g in MATRIX.values() for f, n in g if f in (_BRK, _SCAN, _AUTH)}

LIVE_PROOF = {
    "1": "real zurvaneval/zurvancandidate identities: EACCES on every sealed path, candidate with sandbox off",
    "1b": "root-owned broker tree, sudo digest refusal of a changed binary, Time Machine [Excluded] on keys/",
    "4": "a real run on the installed broker: dirty/unknown refused, repeat cached, ninth run refused",
    "5": "a real candidate attempting network and out-of-scratch writes, killed, run marked failed",
    "8": "real manifest counts after authoring: HOLDOUT >= 150, RESERVE >= 80, or each short stratum reported",
    "9": "red-team transcript: a fresh agent told to extract a holdout item from the dev session fails",
}


def _defs(filename):
    path = TESTS / filename
    if not path.exists():
        return set()
    return set(re.findall(r"^def (test_[A-Za-z0-9_]+)\(", path.read_text(encoding="utf-8"), re.M))


def test_t17_matrix_covers_gates_1_1b_and_2_through_9_exactly():
    assert list(MATRIX) == ["1", "1b", "2", "3", "4", "5", "6", "7", "8", "9"]
    assert all(MATRIX[g] for g in MATRIX)


def test_t17_every_listed_test_exists_in_its_file_except_declared_pending_ones():
    missing = [(f, n) for g in MATRIX.values() for f, n in g
               if (f, n) not in PENDING_WRITER and n not in _defs(f)]
    assert missing == [], "a gate item lost its test: %s" % missing


def test_t17_a_pending_name_that_now_exists_must_leave_the_pending_set():
    now_exist = sorted((f, n) for f, n in PENDING_WRITER if n in _defs(f))
    assert now_exist == [], "remove from PENDING_WRITER (it exists now): %s" % now_exist


def test_t17_pending_entries_are_only_in_files_owned_by_other_tasks():
    assert {f for f, _n in PENDING_WRITER} <= {_BRK, _SCAN, _AUTH}
    assert PENDING_WRITER <= {(f, n) for g in MATRIX.values() for f, n in g}


def test_t17_no_gate_is_proved_only_by_pending_tests_except_through_live_proof():
    for gate, items in MATRIX.items():
        real = [i for i in items if i not in PENDING_WRITER]
        assert real, "gate %s has no test in this tree" % gate


def test_t17_live_proof_gates_are_the_ones_that_need_the_real_identity():
    assert set(LIVE_PROOF) == {"1", "1b", "4", "5", "8", "9"}
    assert all(len(d) > 20 for d in LIVE_PROOF.values())
    assert set(LIVE_PROOF) <= set(MATRIX)


def test_t17_every_live_file_test_in_the_matrix_lives_in_the_live_module():
    for gate, items in MATRIX.items():
        for f, n in items:
            if n.startswith("test_t17_live_"):
                assert f == _LIVE, (gate, n)


def test_t17_matrix_has_no_duplicate_entries():
    flat = [i for g in MATRIX.values() for i in g]
    assert len(flat) == len(set(flat))
