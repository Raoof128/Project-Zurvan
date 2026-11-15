"""check.sh gates [6]-[21] — the CLI surface, driven in-process.

check.sh exercises ~40 CLI subcommands as shell steps. Two problems with that:
they run against the *live* repo (mutating wiki/, writing snapshots, binding a
port), and because they are subprocesses none of that code is measured — which
is why scripts/cli.py read as half-covered despite being the most-exercised
module in the project.

These tests call `cli.main()` in-process with a patched argv and stub the
collaborators. That is deliberate: the CLI's own job is argument parsing,
dispatch and exit codes. The collaborators have their own test files, and
asserting on them here would duplicate that coverage while making these tests
slow and repo-mutating.
"""

import sys

import pytest

import scripts.cli as cli


def run_cli(monkeypatch, *argv):
    """Invoke the CLI exactly as the shell would, but in this process."""
    monkeypatch.setattr(sys, "argv", ["zurvan", *argv])
    cli.main()


def run_cli_expecting_exit(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["zurvan", *argv])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    return excinfo.value.code


class Spy:
    """Records the call it received and returns a canned value."""

    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    @property
    def called(self):
        return bool(self.calls)

    @property
    def args(self):
        return self.calls[0][0]

    @property
    def kwargs(self):
        return self.calls[0][1]


# ── no command / help ─────────────────────────────────────────────────────────

def test_bare_invocation_prints_help_rather_than_crashing(monkeypatch, capsys):
    run_cli(monkeypatch)
    assert "Zurvan" in capsys.readouterr().out


# ── gate [6/6] graph + context ────────────────────────────────────────────────

def test_graph_stats_reports_node_and_edge_counts(monkeypatch, capsys):
    monkeypatch.setattr("scripts.graph_query.get_stats", Spy({"nodes": 12, "edges": 7}))

    run_cli(monkeypatch, "graph", "stats")

    assert "12 nodes, 7 edges" in capsys.readouterr().out


def test_graph_expand_passes_the_requested_depth(monkeypatch, capsys):
    spy = Spy([{"depth": 1, "title": "T", "node_type": "decision", "relation": "links"}])
    monkeypatch.setattr("scripts.graph_context.expand_graph_context", spy)

    run_cli(monkeypatch, "graph", "expand", "wiki/decisions/x.md", "--depth", "2")

    assert spy.args == (["wiki/decisions/x.md"], 2)
    assert "[1] T (decision) - links" in capsys.readouterr().out


def test_graph_neighbours_renders_each_edge(monkeypatch, capsys):
    spy = Spy([{"from_title": "A", "edge_type": "cites", "to_title": "B"}])
    monkeypatch.setattr("scripts.graph_query.get_neighbours", spy)

    run_cli(monkeypatch, "graph", "neighbours", "wiki/a.md")

    assert "A --[cites]--> B" in capsys.readouterr().out


def test_graph_trace_reports_how_many_nodes_it_visited(monkeypatch, capsys):
    monkeypatch.setattr("scripts.graph_query.trace_node", Spy({"nodes_visited": 5}))

    run_cli(monkeypatch, "graph", "trace", "wiki/a.md")

    assert "Traced 5 nodes." in capsys.readouterr().out


def test_context_forwards_hybrid_and_graph_flags(monkeypatch, capsys):
    spy = Spy("## bundle")
    monkeypatch.setattr("scripts.context_export.export_context", spy)

    run_cli(monkeypatch, "context", "--topic", "vector", "--hybrid", "--graph", "--limit", "2")

    topic, limit, hybrid, graph, _depth = spy.args[:5]
    assert (topic, limit, hybrid, graph) == ("vector", 2, True, True)
    assert "## bundle" in capsys.readouterr().out


def test_context_exits_nonzero_on_a_bad_topic(monkeypatch, capsys):
    def _raise(*_a, **_k):
        raise ValueError("no such topic")
    monkeypatch.setattr("scripts.context_export.export_context", _raise)

    assert run_cli_expecting_exit(monkeypatch, "context", "--topic", "x") == 1
    assert "no such topic" in capsys.readouterr().out


def test_search_forwards_the_hybrid_flag(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(cli, "search_memory", spy)

    run_cli(monkeypatch, "search", "memory", "--hybrid")

    assert spy.args[0] == "memory"
    assert spy.args[1] is True


def test_search_exits_nonzero_when_the_query_is_rejected(monkeypatch):
    def _raise(*_a, **_k):
        raise ValueError("bad query")
    monkeypatch.setattr(cli, "search_memory", _raise)

    assert run_cli_expecting_exit(monkeypatch, "search", "x") == 1


# ── gate [10] session + agent workflow ────────────────────────────────────────

def test_session_start_prints_what_the_session_module_returns(monkeypatch, capsys):
    monkeypatch.setattr("scripts.session.session_start", Spy("session opened"))

    run_cli(monkeypatch, "session", "start", "--topic", "smoke test")

    assert "session opened" in capsys.readouterr().out


def test_session_close_forwards_topic_summary_and_checks(monkeypatch):
    spy = Spy("closed")
    monkeypatch.setattr("scripts.session.session_close", spy)

    run_cli(monkeypatch, "session", "close", "--topic", "t",
            "--summary", "s", "--checks", "pytest")

    assert spy.args == ("t", "s", "pytest")


def test_agent_preflight_forwards_retrieval_flags(monkeypatch):
    spy = Spy("preflight")
    monkeypatch.setattr("scripts.agent_workflow.agent_preflight", spy)

    run_cli(monkeypatch, "agent", "preflight", "--topic", "roadmap",
            "--hybrid", "--graph", "--limit", "10")

    assert spy.args == ("roadmap", True, True, 10)


def test_agent_postedit_forwards_the_changed_file_list(monkeypatch):
    spy = Spy("logged")
    monkeypatch.setattr("scripts.agent_workflow.agent_postedit", spy)

    run_cli(monkeypatch, "agent", "postedit", "--summary", "did a thing",
            "--files", "a.py", "b.py", "--checks", "pytest")

    assert spy.args == ("did a thing", ["a.py", "b.py"], "pytest")


def test_agent_prime_passes_the_fix_stale_flag(monkeypatch, capsys):
    spy = Spy("primed")
    monkeypatch.setattr("scripts.agent_workflow.agent_prime", spy)

    run_cli(monkeypatch, "agent", "prime", "--fix-stale")

    assert spy.kwargs["fix_stale"] is True
    assert "primed" in capsys.readouterr().out


# ── gates [11] version and [12] doctor ────────────────────────────────────────

def test_version_delegates_to_the_version_module(monkeypatch):
    spy = Spy()
    monkeypatch.setattr("scripts.version.print_version", spy)

    run_cli(monkeypatch, "version")

    assert spy.called


def test_doctor_propagates_a_healthy_exit_code(monkeypatch):
    monkeypatch.setattr("scripts.doctor.run_doctor", Spy(0))
    assert run_cli_expecting_exit(monkeypatch, "doctor") == 0


def test_doctor_propagates_a_failing_exit_code(monkeypatch):
    """A red doctor must fail check.sh, not be swallowed."""
    monkeypatch.setattr("scripts.doctor.run_doctor", Spy(1))
    assert run_cli_expecting_exit(monkeypatch, "doctor") == 1


# ── gate [14] workspace registry ──────────────────────────────────────────────

def test_project_register_forwards_name_path_and_force(monkeypatch, capsys):
    spy = Spy()
    monkeypatch.setattr("scripts.project_registry.register_project", spy)
    monkeypatch.setattr("scripts.workspace.shorten_path", lambda p: p)

    run_cli(monkeypatch, "project", "register", "--name", "testproj", "--path", ".")

    assert spy.args[:2] == ("testproj", ".")
    assert "Registered project 'testproj'" in capsys.readouterr().out


def test_project_list_marks_the_current_project_and_flags_invalid_paths(monkeypatch, capsys):
    registry = {
        "current": "alpha",
        "projects": {"alpha": {"path": "/a"}, "beta": {"path": "/b"}},
    }
    monkeypatch.setattr("scripts.project_registry.load_registry", Spy(registry))
    monkeypatch.setattr("scripts.workspace.is_valid_zurvan_project", lambda p: p == "/a")
    monkeypatch.setattr("scripts.workspace.shorten_path", lambda p: p)

    run_cli(monkeypatch, "project", "list")

    out = capsys.readouterr().out
    assert "* alpha" in out
    assert "❌ (missing/invalid)" in out


def test_project_current_reports_the_active_project(monkeypatch, capsys):
    monkeypatch.setattr("scripts.project_registry.get_current_project", Spy(("alpha", "/a")))
    monkeypatch.setattr("scripts.workspace.shorten_path", lambda p: p)

    run_cli(monkeypatch, "project", "current")

    assert "Current project: alpha at /a" in capsys.readouterr().out


def test_project_current_exits_nonzero_when_nothing_is_selected(monkeypatch, capsys):
    """No selected project is an error, so scripting on it can't silently pass."""
    monkeypatch.setattr("scripts.project_registry.get_current_project", Spy((None, None)))

    assert run_cli_expecting_exit(monkeypatch, "project", "current") == 1
    assert "No current project." in capsys.readouterr().out


# ── gate [15] federation ──────────────────────────────────────────────────────

def test_federation_stats_prints_json(monkeypatch, capsys):
    monkeypatch.setattr("scripts.federation.get_federation_stats", Spy({"projects": 2}))

    run_cli(monkeypatch, "project", "federation", "stats")

    assert '"projects": 2' in capsys.readouterr().out


def test_federation_doctor_passes_quietly_when_healthy(monkeypatch):
    monkeypatch.setattr("scripts.federation.run_federation_doctor", Spy(True))
    run_cli(monkeypatch, "project", "federation", "doctor")


def test_federation_doctor_exits_nonzero_only_under_strict(monkeypatch):
    """Non-strict reports problems; strict is what should fail a gate."""
    monkeypatch.setattr("scripts.federation.run_federation_doctor", Spy(False))

    run_cli(monkeypatch, "project", "federation", "doctor")          # tolerated
    assert run_cli_expecting_exit(
        monkeypatch, "project", "federation", "doctor", "--strict") == 1


def test_search_all_prints_federated_results_as_json(monkeypatch, capsys):
    monkeypatch.setattr("scripts.cross_project_search.cross_project_search",
                        Spy({"results": []}))

    run_cli(monkeypatch, "project", "search-all", "MCP security", "--hybrid")

    assert '"results"' in capsys.readouterr().out


def test_context_all_forwards_topic_and_flags(monkeypatch, capsys):
    spy = Spy("federated bundle")
    monkeypatch.setattr("scripts.cross_project_context.build_federated_context", spy)

    run_cli(monkeypatch, "project", "context-all", "--topic", "agent memory",
            "--hybrid", "--graph", "--limit", "10")

    assert spy.args[0] == "agent memory"
    assert "federated bundle" in capsys.readouterr().out


# ── gate [16] decision memory ─────────────────────────────────────────────────

def test_decisions_all_renders_the_formatted_decision_list(monkeypatch, capsys):
    monkeypatch.setattr("scripts.decision_federation.collect_federated_decisions", Spy([]))
    monkeypatch.setattr("scripts.decision_federation.format_decisions_all",
                        Spy("== decisions =="))

    run_cli(monkeypatch, "project", "decisions-all")

    assert "== decisions ==" in capsys.readouterr().out


# ── gate [17] policy radar ────────────────────────────────────────────────────

@pytest.fixture
def radar_stubs(monkeypatch):
    monkeypatch.setattr("scripts.claim_federation.collect_federated_claims_and_policies",
                        Spy([]))
    monkeypatch.setattr("scripts.policy_radar.analyze_policies", Spy({}))
    return monkeypatch


def test_radar_policies_prints_coverage(radar_stubs, monkeypatch, capsys):
    monkeypatch.setattr("scripts.policy_radar.format_policy_coverage", Spy("COVERAGE"))

    run_cli(monkeypatch, "project", "radar", "policies")

    assert "COVERAGE" in capsys.readouterr().out


def test_radar_drift_prints_drift(radar_stubs, monkeypatch, capsys):
    monkeypatch.setattr("scripts.policy_radar.format_policy_drift", Spy("DRIFT"))

    run_cli(monkeypatch, "project", "radar", "drift")

    assert "DRIFT" in capsys.readouterr().out


def test_radar_scan_prints_the_scan(radar_stubs, monkeypatch, capsys):
    monkeypatch.setattr("scripts.policy_radar.format_policy_scan", Spy("SCAN"))

    run_cli(monkeypatch, "project", "radar", "scan")

    assert "SCAN" in capsys.readouterr().out


def test_radar_contradictions_renders_detected_conflicts(radar_stubs, monkeypatch, capsys):
    monkeypatch.setattr("scripts.contradiction_radar.detect_contradictions", Spy([]))
    monkeypatch.setattr("scripts.contradiction_radar.format_contradictions", Spy("CONFLICTS"))

    run_cli(monkeypatch, "project", "radar", "contradictions")

    assert "CONFLICTS" in capsys.readouterr().out


def test_radar_report_saves_the_report_and_says_where(radar_stubs, monkeypatch, capsys):
    monkeypatch.setattr("scripts.policy_radar.generate_full_report", Spy("FULL REPORT"))
    monkeypatch.setattr("scripts.policy_radar.save_report_locally", Spy("/tmp/radar.md"))

    run_cli(monkeypatch, "project", "radar", "report")

    out = capsys.readouterr().out
    assert "FULL REPORT" in out
    assert "Report saved to: /tmp/radar.md" in out


# ── gate [18] evidence packs ──────────────────────────────────────────────────

def test_evidence_build_reports_the_new_pack_id_and_item_count(monkeypatch, capsys):
    monkeypatch.setattr("scripts.evidence_pack.build_evidence_pack",
                        Spy({"pack_id": "pack-1", "item_count": 3, "path": "/packs/pack-1"}))

    run_cli(monkeypatch, "evidence", "build", "--topic", "smoke test",
            "--hybrid", "--graph", "--include-decisions", "--include-policy-radar")

    out = capsys.readouterr().out
    assert "Evidence pack created: pack-1" in out
    assert "Items collected: 3" in out


def test_evidence_list_says_so_when_there_are_no_packs(monkeypatch, capsys):
    monkeypatch.setattr("scripts.evidence_pack.list_evidence_packs", Spy([]))

    run_cli(monkeypatch, "evidence", "list")

    assert "No evidence packs found." in capsys.readouterr().out


def test_evidence_list_shows_each_pack(monkeypatch, capsys):
    monkeypatch.setattr("scripts.evidence_pack.list_evidence_packs",
                        Spy([{"pack_id": "pack-1", "topic": "smoke test",
                              "item_count": 3, "created_at": "2026-01-01"}]))

    run_cli(monkeypatch, "evidence", "list")

    assert "pack-1" in capsys.readouterr().out


def test_evidence_inspect_exits_nonzero_for_an_unknown_pack(monkeypatch, capsys):
    monkeypatch.setattr("scripts.evidence_pack.inspect_evidence_pack", Spy(None))

    assert run_cli_expecting_exit(monkeypatch, "evidence", "inspect", "nope") == 1
    assert "not found" in capsys.readouterr().out


def test_evidence_inspect_reports_the_redaction_status(monkeypatch, capsys):
    """Redaction status is the privacy-relevant field — it must be surfaced."""
    monkeypatch.setattr(
        "scripts.evidence_pack.inspect_evidence_pack",
        Spy({"manifest": {"pack_id": "pack-1", "topic": "t",
                          "item_count": 2, "redaction_status": "redacted"}}),
    )

    run_cli(monkeypatch, "evidence", "inspect", "pack-1")

    assert "Redaction: redacted" in capsys.readouterr().out


def test_evidence_export_reports_the_output_path(monkeypatch, capsys):
    monkeypatch.setattr("scripts.evidence_export.export_evidence_pack", Spy("/out/pack.md"))

    run_cli(monkeypatch, "evidence", "export", "pack-1", "--format", "markdown")

    assert "Exported to /out/pack.md" in capsys.readouterr().out


def test_evidence_export_exits_nonzero_when_export_fails(monkeypatch, capsys):
    def _raise(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr("scripts.evidence_export.export_evidence_pack", _raise)

    assert run_cli_expecting_exit(
        monkeypatch, "evidence", "export", "pack-1", "--format", "json") == 1
    assert "Export failed: disk full" in capsys.readouterr().out


def test_evidence_redact_reports_success_and_no_op_differently(monkeypatch, capsys):
    monkeypatch.setattr("scripts.evidence_export.redact_existing_pack", Spy(True))
    run_cli(monkeypatch, "evidence", "redact", "pack-1")
    assert "Redacted pack pack-1" in capsys.readouterr().out

    monkeypatch.setattr("scripts.evidence_export.redact_existing_pack", Spy(False))
    run_cli(monkeypatch, "evidence", "redact", "pack-1")
    assert "not found or already redacted" in capsys.readouterr().out


# ── gate [19] report composer ─────────────────────────────────────────────────

def test_report_compose_exports_every_requested_format(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.compose_report", Spy({"report_id": "rep-1"}))
    export = Spy("/out/rep-1.md")
    monkeypatch.setattr("scripts.report_export.export_report", export)

    run_cli(monkeypatch, "report", "compose", "--pack", "pack-1",
            "--template", "evidence_digest", "--format", "markdown,json")

    assert [call[0][1] for call in export.calls] == ["markdown", "json"]
    assert "Report composed: rep-1" in capsys.readouterr().out


# ── gate [21] review workbench ────────────────────────────────────────────────

def test_review_audit_with_no_id_summarises_every_report(monkeypatch, capsys):
    monkeypatch.setattr("scripts.review_audit.audit_all_reports",
                        Spy([{"report_id": "rep-1", "status": "pass"}]))

    run_cli(monkeypatch, "review", "audit")

    assert "rep-1 - PASS" in capsys.readouterr().out


def test_review_audit_for_one_report_lists_warnings_and_failures(monkeypatch, capsys):
    """Failures are the safety-relevant output — they must reach stdout."""
    monkeypatch.setattr(
        "scripts.review_audit.audit_report",
        Spy({"status": "fail", "warnings": ["soft issue"], "failures": ["secret found"]}),
    )

    run_cli(monkeypatch, "review", "audit", "rep-1")

    out = capsys.readouterr().out
    assert "Audit Status for rep-1: FAIL" in out
    assert "WARN: soft issue" in out
    assert "FAIL: secret found" in out


def test_review_index_rebuild_reports_what_it_indexed(monkeypatch, capsys):
    monkeypatch.setattr("scripts.review_index.rebuild_index",
                        Spy({"reports": ["a"], "packs": ["b", "c"]}))

    run_cli(monkeypatch, "review", "index", "rebuild")

    assert "1 reports, 2 packs" in capsys.readouterr().out


# ── memory write commands ─────────────────────────────────────────────────────

def test_remember_exits_nonzero_when_the_note_is_rejected(monkeypatch):
    monkeypatch.setattr(cli, "add_note", Spy(False))
    assert run_cli_expecting_exit(monkeypatch, "remember", "--title", "t", "--body", "b") == 1


def test_remember_succeeds_quietly_when_the_note_is_written(monkeypatch):
    monkeypatch.setattr(cli, "add_note", Spy("wiki/note-t.md"))
    run_cli(monkeypatch, "remember", "--title", "t", "--body", "b")


def test_decision_add_forwards_title_reason_status_and_tags(monkeypatch):
    spy = Spy("wiki/decisions/d.md")
    monkeypatch.setattr(cli, "add_decision", spy)

    run_cli(monkeypatch, "decision", "add", "--title", "T", "--reason", "R",
            "--status", "accepted", "--tags", "zurvan", "tests")

    assert spy.args == ("T", "R", "accepted", ["zurvan", "tests"])


def test_claim_add_forwards_the_evidence_quote(monkeypatch):
    """The evidence quote is what the claim writer validates verbatim.
    Z2: `claim add` writes through the claim ledger (scripts/claim_cli.py)."""
    from scripts import claim_ledger
    from scripts import memory
    spy = Spy("clm_0000000000000000")
    monkeypatch.setattr(claim_ledger, "add", spy)
    # The command also appends a `Claim Added` log line (2026-11-15); this test
    # runs the CLI in-process on the real store root, so that write is spied too.
    log = Spy(None)
    monkeypatch.setattr(memory, "append_claim_log", log)

    run_cli(monkeypatch, "claim", "add", "--text", "T", "--source", "s.md",
            "--evidence", "a verbatim quote", "--confidence", "high")

    assert spy.args[:3] == ("T", "s.md", "a verbatim quote")
    assert spy.calls[0][1]["confidence"] == "high"
    assert log.args == ("clm_0000000000000000", "s.md")


def test_claim_add_exits_nonzero_when_evidence_does_not_match(monkeypatch):
    """A rejected citation must fail the command, not pass silently."""
    from scripts import claim_ledger
    from scripts.claim_store import ClaimRefused

    def refuse(*a, **k):
        raise ClaimRefused("EVIDENCE_REJECTED", "evidence quote not found verbatim in source")

    monkeypatch.setattr(claim_ledger, "add", refuse)

    assert run_cli_expecting_exit(
        monkeypatch, "claim", "add", "--text", "T", "--source", "s.md",
        "--evidence", "not in the source", "--confidence", "high") == 1


def test_question_add_forwards_question_and_reason(monkeypatch):
    spy = Spy("wiki/open-questions.md")
    monkeypatch.setattr(cli, "add_question", spy)

    run_cli(monkeypatch, "question", "add", "--question", "Q?", "--reason", "R")

    assert spy.args[:2] == ("Q?", "R")


def test_question_add_exits_nonzero_on_failure(monkeypatch):
    monkeypatch.setattr(cli, "add_question", Spy(False))
    assert run_cli_expecting_exit(
        monkeypatch, "question", "add", "--question", "Q?", "--reason", "R") == 1


# ── subprocess-backed commands ────────────────────────────────────────────────

def test_shelled_commands_propagate_a_child_failure(monkeypatch):
    """Regression: these once always exited 0, so a failing eval looked green."""
    import subprocess as _sp

    class _Failed:
        returncode = 3

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Failed())

    assert run_cli_expecting_exit(monkeypatch, "audit") == 3


def test_shelled_commands_stay_silent_on_success(monkeypatch):
    class _Ok:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Ok())

    # `index rebuild` is no longer shelled (it runs in-process inside the CLI
    # operation since 2026-10-01), so mocking subprocess would let it write the
    # LIVE wiki/index.md — which it did, once. `audit` is still shelled.
    run_cli(monkeypatch, "audit")


def test_eval_search_forwards_threshold_and_hybrid_flag(monkeypatch):
    captured = {}

    class _Ok:
        returncode = 0

    def _run(cmd, cwd):
        captured["cmd"] = cmd
        return _Ok()

    monkeypatch.setattr(cli.subprocess, "run", _run)

    run_cli(monkeypatch, "eval", "search", "--hybrid", "--min-top3", "0.6")

    assert "--hybrid" in captured["cmd"]
    assert "0.6" in captured["cmd"]


# ── global flags ──────────────────────────────────────────────────────────────

def test_project_root_override_redirects_where_pages_are_written(monkeypatch, tmp_path):
    """--project-root must repoint the modules that write wiki pages."""
    import scripts.context_export as context_export
    import scripts.wiki_merge as wiki_merge

    original = (context_export.PROJECT_ROOT, wiki_merge.PROJECT_ROOT)
    try:
        monkeypatch.setattr(cli, "add_note", Spy("ok"))
        run_cli(monkeypatch, "--project-root", str(tmp_path),
                "remember", "--title", "t", "--body", "b")

        assert context_export.PROJECT_ROOT == tmp_path.resolve()
        assert wiki_merge.PROJECT_ROOT == tmp_path.resolve()
    finally:
        context_export.PROJECT_ROOT, wiki_merge.PROJECT_ROOT = original


def test_project_flag_reruns_the_cli_inside_that_project(monkeypatch, capsys):
    """--project delegates to a child run rooted in the named project."""
    monkeypatch.setattr("scripts.workspace.resolve_project_root", Spy("/projects/other"))

    class _Ok:
        returncode = 0

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Ok())

    assert run_cli_expecting_exit(monkeypatch, "--project", "other", "version") == 0
    assert "Running against project: other" in capsys.readouterr().out


# ── gate [13] snapshots ───────────────────────────────────────────────────────

def test_snapshot_create_forwards_the_include_raw_flag(monkeypatch):
    """include_raw decides whether private sources enter the snapshot."""
    spy = Spy()
    monkeypatch.setattr("scripts.snapshot.create_snapshot", spy)

    run_cli(monkeypatch, "snapshot", "create")
    assert spy.args[0] is False

    run_cli(monkeypatch, "snapshot", "create", "--include-raw")
    assert spy.calls[1][0][0] is True


def test_snapshot_list_delegates_to_the_snapshot_module(monkeypatch):
    spy = Spy()
    monkeypatch.setattr("scripts.snapshot.list_snapshots", spy)

    run_cli(monkeypatch, "snapshot", "list")

    assert spy.called


def test_snapshot_restore_forwards_name_and_force(monkeypatch):
    spy = Spy()
    monkeypatch.setattr("scripts.restore_snapshot.restore_snapshot", spy)

    run_cli(monkeypatch, "snapshot", "restore", "snap-1", "--force")

    assert spy.args == ("snap-1", True)


# ── project use / doctor / snapshot delegation ────────────────────────────────

def test_project_use_rejects_an_unregistered_name(monkeypatch, capsys):
    monkeypatch.setattr("scripts.project_registry.load_registry", Spy({"projects": {}}))

    assert run_cli_expecting_exit(monkeypatch, "project", "use", "ghost") == 1
    assert "not found" in capsys.readouterr().out


def test_project_use_switches_and_warns_when_the_path_is_invalid(monkeypatch, capsys):
    """Switching still succeeds, but the broken path must be called out."""
    monkeypatch.setattr("scripts.project_registry.load_registry",
                        Spy({"projects": {"alpha": {"path": "/gone"}}}))
    set_current = Spy()
    monkeypatch.setattr("scripts.project_registry.set_current_project", set_current)
    monkeypatch.setattr("scripts.workspace.is_valid_zurvan_project", lambda p: False)
    monkeypatch.setattr("scripts.workspace.shorten_path", lambda p: p)

    run_cli(monkeypatch, "project", "use", "alpha")

    out = capsys.readouterr().out
    assert "invalid or missing" in out
    assert "Switched to project 'alpha'" in out
    assert set_current.args == ("alpha",)


def test_project_doctor_propagates_the_child_exit_code(monkeypatch, capsys):
    monkeypatch.setattr("scripts.workspace.resolve_project_root", Spy("/projects/alpha"))

    class _Failed:
        returncode = 2

    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Failed())

    assert run_cli_expecting_exit(monkeypatch, "project", "doctor", "alpha") == 2
    assert "Running doctor for project: alpha" in capsys.readouterr().out


def test_project_snapshot_runs_inside_the_named_project(monkeypatch, capsys):
    monkeypatch.setattr("scripts.workspace.resolve_project_root", Spy("/projects/alpha"))
    captured = {}

    class _Ok:
        returncode = 0

    def _run(cmd, cwd):
        captured["cwd"] = cwd
        return _Ok()

    monkeypatch.setattr(cli.subprocess, "run", _run)

    assert run_cli_expecting_exit(monkeypatch, "project", "snapshot", "alpha") == 0
    assert captured["cwd"] == "/projects/alpha"


# ── decision federation variants ──────────────────────────────────────────────

def test_decisions_similar_forwards_the_query_and_limit(monkeypatch, capsys):
    monkeypatch.setattr("scripts.decision_federation.collect_federated_decisions", Spy([]))
    fmt = Spy("SIMILAR")
    monkeypatch.setattr("scripts.decision_federation.format_similar_decisions", fmt)

    run_cli(monkeypatch, "project", "decisions-similar", "sqlite", "--limit", "3")

    assert fmt.args[1:] == ("sqlite", 3)
    assert "SIMILAR" in capsys.readouterr().out


def test_decisions_conflicts_renders_the_conflict_report(monkeypatch, capsys):
    monkeypatch.setattr("scripts.decision_federation.collect_federated_decisions", Spy([]))
    monkeypatch.setattr("scripts.decision_federation.format_decision_conflicts", Spy("CONFLICTS"))

    run_cli(monkeypatch, "project", "decisions-conflicts")

    assert "CONFLICTS" in capsys.readouterr().out


def test_decisions_stale_forwards_the_age_threshold(monkeypatch, capsys):
    monkeypatch.setattr("scripts.decision_federation.collect_federated_decisions", Spy([]))
    fmt = Spy("STALE")
    monkeypatch.setattr("scripts.decision_federation.format_stale_decisions", fmt)

    run_cli(monkeypatch, "project", "decisions-stale", "--days", "45")

    assert fmt.args[1] == 45


def test_decision_memory_rebuild_reports_how_many_were_cached(monkeypatch, capsys):
    monkeypatch.setattr("scripts.decision_federation.rebuild_decision_memory", Spy(7))

    run_cli(monkeypatch, "project", "decision-memory", "rebuild")

    assert "Rebuilt cache with 7 decisions." in capsys.readouterr().out


# ── report list / inspect / validate / export ─────────────────────────────────

def test_report_list_says_so_when_there_are_none(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.list_reports", Spy([]))

    run_cli(monkeypatch, "report", "list")

    assert "No reports found." in capsys.readouterr().out


def test_report_inspect_exits_nonzero_for_an_unknown_report(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.inspect_report", Spy(None))

    assert run_cli_expecting_exit(monkeypatch, "report", "inspect", "ghost") == 1
    assert "not found" in capsys.readouterr().out


def test_report_inspect_surfaces_redaction_status_and_warnings(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.report_compose.inspect_report",
        Spy({"report_id": "rep-1", "topic": "t", "template": "evidence_digest",
             "source_pack_id": "pack-1", "redaction_status": "redacted",
             "warnings": ["check quote 3"]}),
    )

    run_cli(monkeypatch, "report", "inspect", "rep-1")

    out = capsys.readouterr().out
    assert "Redaction: redacted" in out
    assert " - check quote 3" in out


def test_report_validate_passes_a_clean_report(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.validate_report",
                        Spy({"valid": True, "warnings": []}))

    run_cli(monkeypatch, "report", "validate", "rep-1")

    assert "is valid" in capsys.readouterr().out


def test_report_validate_exits_nonzero_and_lists_every_issue(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.validate_report",
                        Spy({"valid": False, "issues": ["missing citation", "bad quote"]}))

    assert run_cli_expecting_exit(monkeypatch, "report", "validate", "rep-1") == 1
    out = capsys.readouterr().out
    assert "missing citation" in out and "bad quote" in out


def test_report_export_exits_nonzero_when_export_fails(monkeypatch, capsys):
    def _raise(*_a, **_k):
        raise RuntimeError("template missing")
    monkeypatch.setattr("scripts.report_export.export_report", _raise)

    assert run_cli_expecting_exit(
        monkeypatch, "report", "export", "rep-1", "--format", "markdown") == 1
    assert "Export failed: template missing" in capsys.readouterr().out


# ── review list / serve ───────────────────────────────────────────────────────

def test_review_list_prints_each_report(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.list_reports",
                        Spy([{"report_id": "rep-1", "topic": "smoke"}]))

    run_cli(monkeypatch, "review", "list")

    assert "rep-1 - smoke" in capsys.readouterr().out


def test_review_serve_forwards_host_port_and_lan_flag(monkeypatch):
    """allow_lan decides whether a private workbench binds beyond localhost."""
    spy = Spy()
    monkeypatch.setattr("scripts.review_server.run_server", spy)

    run_cli(monkeypatch, "review", "serve", "--host", "127.0.0.1", "--port", "8769")

    assert spy.kwargs["host"] == "127.0.0.1"
    assert spy.kwargs["port"] == 8769
    assert spy.kwargs["allow_lan"] is False


# ── publish ───────────────────────────────────────────────────────────────────

def test_publish_export_reports_the_output_path(monkeypatch, capsys):
    monkeypatch.setattr("scripts.publication_export.export_publication", Spy("/pub/rep-1.md"))

    run_cli(monkeypatch, "publish", "export", "rep-1", "--format", "markdown")

    assert "Exported markdown to /pub/rep-1.md" in capsys.readouterr().out


def test_publish_export_exits_nonzero_on_failure(monkeypatch, capsys):
    def _raise(*_a, **_k):
        raise RuntimeError("unsafe content")
    monkeypatch.setattr("scripts.publication_export.export_publication", _raise)

    assert run_cli_expecting_exit(
        monkeypatch, "publish", "export", "rep-1", "--format", "markdown") == 1
    assert "Export failed: unsafe content" in capsys.readouterr().out


def test_publish_bundle_exits_nonzero_on_failure(monkeypatch, capsys):
    def _raise(*_a, **_k):
        raise RuntimeError("no report")
    monkeypatch.setattr("scripts.publication_bundle.create_bundle", _raise)

    assert run_cli_expecting_exit(monkeypatch, "publish", "bundle", "rep-1") == 1
    assert "Bundle failed: no report" in capsys.readouterr().out


def test_publish_citations_exits_nonzero_for_an_unknown_report(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.inspect_report", Spy(None))

    assert run_cli_expecting_exit(monkeypatch, "publish", "citations", "ghost") == 1
    assert "Report not found." in capsys.readouterr().out


def test_publish_citations_prints_each_citation_with_its_evidence_id(monkeypatch, capsys):
    monkeypatch.setattr("scripts.report_compose.inspect_report",
                        Spy({"source_pack_id": "pack-1"}))
    monkeypatch.setattr("scripts.evidence_pack.inspect_evidence_pack", Spy({}))
    monkeypatch.setattr(
        "scripts.publication_citations.generate_citation_appendix",
        Spy([{"evidence_id": "E1", "project": "zurvan",
              "relative_path": "wiki/a.md", "excerpt": "a quote"}]),
    )

    run_cli(monkeypatch, "publish", "citations", "rep-1")

    out = capsys.readouterr().out
    assert "[E1] zurvan / wiki/a.md" in out
    assert "a quote" in out


def test_publish_validate_exits_nonzero_when_the_audit_fails(monkeypatch, capsys):
    """Publishing must be blocked by a failing safety audit."""
    monkeypatch.setattr("scripts.review_audit.audit_report",
                        Spy({"status": "fail", "warnings": [], "failures": ["secret found"]}))

    assert run_cli_expecting_exit(monkeypatch, "publish", "validate", "rep-1") == 1
    assert "FAIL: secret found" in capsys.readouterr().out


def test_publish_validate_passes_a_clean_audit(monkeypatch, capsys):
    monkeypatch.setattr("scripts.review_audit.audit_report",
                        Spy({"status": "pass", "warnings": [], "failures": []}))

    run_cli(monkeypatch, "publish", "validate", "rep-1")

    assert "Validation Status: PASS" in capsys.readouterr().out


# ── trace ─────────────────────────────────────────────────────────────────────

def test_trace_list_says_so_when_there_are_none(monkeypatch, capsys):
    monkeypatch.setattr("scripts.trace_writer.TraceStore.list", lambda self: [])

    run_cli(monkeypatch, "trace", "list")

    assert "No traces found." in capsys.readouterr().out


def test_trace_list_summarises_each_trace(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.trace_writer.TraceStore.list",
        lambda self: [{"trace_id": "t-1", "title": "search", "event_count": 4,
                       "created_at": "2026-01-01"}],
    )

    run_cli(monkeypatch, "trace", "list")

    assert "t-1 | search | events: 4" in capsys.readouterr().out


def test_trace_inspect_exits_nonzero_for_a_missing_trace(monkeypatch, capsys):
    def _raise(self, trace_id):
        raise FileNotFoundError("no such trace")
    monkeypatch.setattr("scripts.trace_writer.TraceStore.read", _raise)

    assert run_cli_expecting_exit(monkeypatch, "trace", "inspect", "ghost") == 1
    assert "no such trace" in capsys.readouterr().out


def test_trace_inspect_prints_the_trace_as_json(monkeypatch, capsys):
    monkeypatch.setattr("scripts.trace_writer.TraceStore.read",
                        lambda self, trace_id: {"trace_id": trace_id, "events": []})

    run_cli(monkeypatch, "trace", "inspect", "t-1")

    assert '"trace_id": "t-1"' in capsys.readouterr().out


def test_trace_validate_reports_a_valid_trace(monkeypatch, capsys):
    class _Result:
        valid = True
        issues = []

    monkeypatch.setattr("scripts.trace_writer.TraceStore.trace_path",
                        lambda self, trace_id: "/traces/t-1.json")
    monkeypatch.setattr("scripts.trace_validate.validate_trace_file", lambda p: _Result())

    run_cli(monkeypatch, "trace", "validate", "t-1")

    assert "is valid" in capsys.readouterr().out


def test_trace_validate_exits_nonzero_and_lists_issues(monkeypatch, capsys):
    class _Result:
        valid = False
        issues = ["hash mismatch"]

    monkeypatch.setattr("scripts.trace_writer.TraceStore.trace_path",
                        lambda self, trace_id: "/traces/t-1.json")
    monkeypatch.setattr("scripts.trace_validate.validate_trace_file", lambda p: _Result())

    assert run_cli_expecting_exit(monkeypatch, "trace", "validate", "t-1") == 1
    assert "hash mismatch" in capsys.readouterr().out


def test_trace_replay_prints_the_rendered_replay(monkeypatch, capsys):
    monkeypatch.setattr("scripts.trace_writer.TraceStore.trace_path",
                        lambda self, trace_id: "/traces/t-1.json")
    monkeypatch.setattr("scripts.trace_replay.replay_trace_file", lambda p: "REPLAY\n")

    run_cli(monkeypatch, "trace", "replay", "t-1")

    assert "REPLAY" in capsys.readouterr().out


def test_trace_replay_exits_nonzero_for_a_corrupt_trace(monkeypatch, capsys):
    def _raise(_path):
        raise ValueError("corrupt trace")
    monkeypatch.setattr("scripts.trace_writer.TraceStore.trace_path",
                        lambda self, trace_id: "/traces/t-1.json")
    monkeypatch.setattr("scripts.trace_replay.replay_trace_file", _raise)

    assert run_cli_expecting_exit(monkeypatch, "trace", "replay", "t-1") == 1
    assert "corrupt trace" in capsys.readouterr().out
