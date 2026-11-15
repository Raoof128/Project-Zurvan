"""Z2 closure matrix: each gate of the spec (§8, G1-G12) mapped to the tests that
demonstrate it. A renamed or deleted test must not silently unprove a gate (the
same idea as tests/test_z0_closure.py and tests/test_z1b_closure.py).

LIVE_PROOF names the gates whose closure ALSO needs a recorded live run on the
real store (plan Task 15); a unit test alone does not close them.
"""

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

_MIG = "test_z2_migrate.py"
_LED = "test_z2_ledger.py"
_EV = "test_z2_evidence.py"
_VER = "test_z2_verify.py"
_ERA = "test_z2_erase.py"
_REC = "test_z2_reconcile.py"
_WR = "test_z2_writers.py"
_SNAP = "test_z2_snapshot.py"
_VIS = "test_z2_visibility.py"
_PAGES = "test_z2_pages.py"
_QTV = "test_z2_query_time_visibility.py"

MATRIX = {
    "G1": [  # authored Markdown intact
        (_MIG, "test_authored_markdown_untouched"),
        (_MIG, "test_freeze_first_and_read_only"),
    ],
    "G2": [  # migrated claims retain provenance
        (_MIG, "test_provenance_retained_for_every_parsed_page"),
        (_MIG, "test_broken_evidence_orphaned_with_reason"),
        (_MIG, "test_unparseable_imported_as_unknown_not_dropped"),
        (_MIG, "test_whitespace_only_quote_is_moved_normalised_not_orphaned"),
        (_MIG, "test_parse_both_shapes"),
        (_MIG, "test_quote_without_a_source_is_imported_and_orphaned_not_a_crash"),
        (_MIG, "test_migrate_cli_real_run_reports_and_keeps_files_beside_the_store"),
    ],
    "G3": [  # historical/current views reconstruct
        ("test_z2_belief_property.py", "test_as_of_revision_replays_every_recorded_current_view"),
        (_LED, "test_as_of_revision_and_pre_ledger"),
        (_LED, "test_aborted_operation_rows_invisible"),
        (_MIG, "test_pre_ledger_as_of_before_migration"),
    ],
    "G4": [  # belief replay agrees with snapshots
        (_SNAP, "test_snapshot_belief_matches_as_of_revision"),
        (_SNAP, "test_snapshot_sidecar_hides_an_orphaned_row"),
        (_SNAP, "test_snapshot_includes_rows_of_a_reconciled_mutation"),
    ],
    "G5": [  # evidence decay is detected
        (_EV, "test_intact_when_file_unchanged"),
        (_EV, "test_moved_when_text_inserted_before"),
        (_EV, "test_moved_normalised_when_whitespace_reflowed"),
        (_EV, "test_ambiguous_when_quote_repeats_with_identical_context"),
        (_EV, "test_changed_when_quote_deleted"),
        (_EV, "test_gone_when_file_removed"),
        (_VER, "test_apply_orphans_then_restores"),
        (_VER, "test_ambiguous_never_orphans"),
        (_MIG, "test_a_quote_matching_twice_after_normalising_is_ambiguous_not_orphaned"),
        (_VER, "test_apply_never_orphans_a_claim_whose_volume_is_unmounted"),
    ],
    "G6": [  # no silent overwrite
        (_LED, "test_supersede_links_and_ends_old"),
        (_LED, "test_retract_keeps_history"),
        (_LED, "test_reason_required"),
        (_LED, "test_expected_rev_conflict_writes_nothing"),
        (_LED, "test_write_needs_an_operation"),
        (_LED, "test_refused_write_on_a_fresh_store_creates_nothing"),
        (_PAGES, "test_supersede_refuses_when_page_has_unadopted_edit"),
        (_VER, "test_restore_by_assertion_refused"),
    ],
    "G7": [  # erasure keeps the chain
        (_ERA, "test_erase_blanks_every_body_and_keeps_chain"),
        (_ERA, "test_erased_text_absent_from_db_bytes"),
        (_ERA, "test_erase_rewrites_pages_without_the_text"),
        (_ERA, "test_erase_lists_snapshots_holding_copies"),
        ("test_z2_store.py", "test_chain_survives_content_erasure"),
        (_ERA, "test_a_store_keeps_its_migration_files_beside_itself_not_in_the_real_home"),
        (_ERA, "test_erase_lists_the_log_line_and_the_migration_report"),
        (_ERA, "test_erase_reports_a_hand_edit_it_had_to_overwrite"),
        (_WR, "test_reextraction_of_a_source_with_an_erased_claim_is_refused"),
    ],
    "G8": [  # crash safety
        (_REC, "test_crash_mid_claim_write_then_reconcile"),
        (_REC, "test_reconcile_does_not_crash_on_sqlite"),
        (_REC, "test_a_torn_claims_store_needs_review"),
        (_REC, "test_a_reader_names_a_hot_journal_instead_of_crashing_or_going_empty"),
        (_REC, "test_reconcile_rolls_back_a_hot_journal_left_by_a_crashed_claim_write"),
    ],
    "G9": [  # writers routed
        (_WR, "test_only_renderer_writes_wiki_claims"),
        (_WR, "test_f1_two_sources_same_model_id_two_claims"),
        (_WR, "test_add_claim_writes_ledger_and_page"),
        (_WR, "test_mcp_claim_add_records_agent"),
    ],
    "G10": [],  # retrieval: a live eval_search before/after only (LIVE_PROOF)
    "G11": [  # snapshot coherence
        (_SNAP, "test_require_claims_refuses_while_claim_mutation_active"),
        (_SNAP, "test_claims_can_be_required_and_are_captured"),
    ],
    "G12": [  # non-current claims leave derived views
        (_VIS, "test_search_chunker_skips_noncurrent"),
        (_VIS, "test_graph_skips_noncurrent"),
        (_VIS, "test_digest_skips_noncurrent"),
        (_VIS, "test_federation_skips_noncurrent"),
        (_ERA, "test_erased_claim_hidden_from_views"),
        (_VER, "test_orphaned_claim_hidden_from_views"),
        (_PAGES, "test_status_banner_and_supersedes_line"),
        # IOU-Z2-6: hidden at query time, before the index is rebuilt
        (_QTV, "test_a_claim_made_noncurrent_after_indexing_is_hidden_at_once"),
        (_QTV, "test_the_result_is_refilled_to_limit"),
        (_QTV, "test_a_snapshot_search_is_unchanged"),
        (_QTV, "test_keyword_mode_hides_it"),
    ],
}

LIVE_PROOF = {
    "G2": "live migration report: provenance_mismatches == []",
    "G5": "live `zurvan claim verify` reproduces the migration outcome counts",
    "G10": "eval_search --hybrid --min-top3 0.6 before/after the live migration; no "
           "previously-passing Top-3 query fails",
}


def _defs(filename):
    path = TESTS / filename
    if not path.exists():
        return set()
    return set(re.findall(r"^def (test_[A-Za-z0-9_]+)\(", path.read_text(encoding="utf-8"), re.M))


def test_matrix_covers_g1_to_g12_exactly():
    assert sorted(MATRIX, key=lambda g: int(g[1:])) == [f"G{n}" for n in range(1, 13)]


def test_every_listed_test_exists():
    missing = [(g, f, t) for g, rows in MATRIX.items() for f, t in rows if t not in _defs(f)]
    assert missing == []


def test_no_duplicates():
    for gate, rows in MATRIX.items():
        assert len(rows) == len(set(rows)), gate


def test_every_gate_without_unit_tests_is_a_live_proof():
    empty = {g for g, rows in MATRIX.items() if not rows}
    assert empty <= set(LIVE_PROOF), empty
    assert set(LIVE_PROOF) <= set(MATRIX)
