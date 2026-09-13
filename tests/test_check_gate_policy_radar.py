"""check.sh gate [17/19] — Policy Radar.

The radar decides which cross-project policies count as consistent, missing, or
drifting. check.sh only asserted the three commands exit 0, so the analysis
itself — the part that produces claims about a project's safety posture — was
uncovered. Every fixture is synthetic; nothing reads the real registry.
"""

import pytest

from scripts.policy_radar import (
    analyze_policies,
    format_policy_coverage,
    format_policy_drift,
    format_policy_scan,
    generate_full_report,
    save_report_locally,
)
from scripts.policy_rules import POLICY_CATEGORIES


# "raw is immutable" is a positive marker of the raw_protection category.
RAW_POLICY = "raw is immutable"
CLOUD_POLICY = "no cloud"


def item(project, title="A policy", text=RAW_POLICY, kind="decision", path="wiki/d.md"):
    return {
        "project": project,
        "title": title,
        "full_text": text,
        "source_kind": kind,
        "relative_path": path,
    }


# ── analyze_policies ──────────────────────────────────────────────────────────

def test_no_items_yields_an_empty_analysis():
    analysis = analyze_policies([])

    assert analysis["projects_scanned"] == []
    assert analysis["consistent"] == []
    assert analysis["drift"] == []
    assert analysis["reusable"] == []


def test_a_policy_every_project_states_is_consistent():
    analysis = analyze_policies([item("alpha"), item("beta")])

    assert "raw_protection" in analysis["consistent"]
    assert analysis["drift"] == []


def test_a_policy_only_one_project_states_is_drift():
    """Drift is the finding that matters: one project protects raw, one doesn't."""
    analysis = analyze_policies([item("alpha"), item("beta", text="unrelated prose")])

    drift_categories = {d["category"] for d in analysis["drift"]}
    assert "raw_protection" in drift_categories

    entry = next(d for d in analysis["drift"] if d["category"] == "raw_protection")
    assert entry["present"] == ["alpha"]
    assert entry["absent"] == ["beta"]


def test_a_category_nobody_states_is_missing_everywhere_but_is_not_drift():
    analysis = analyze_policies([item("alpha"), item("beta")])

    assert "no_cloud" in analysis["missing"]["alpha"]
    assert "no_cloud" in analysis["missing"]["beta"]
    assert "no_cloud" not in {d["category"] for d in analysis["drift"]}


def test_a_consistent_policy_is_only_reusable_across_several_projects():
    """One project agreeing with itself is not evidence a policy travels."""
    single = analyze_policies([item("alpha")])
    assert "raw_protection" in single["consistent"]
    assert single["reusable"] == []

    several = analyze_policies([item("alpha"), item("beta")])
    assert "raw_protection" in several["reusable"]


def test_the_title_is_scanned_as_well_as_the_body():
    analysis = analyze_policies([item("alpha", title=RAW_POLICY, text="nothing here")])

    assert "raw_protection" in analysis["consistent"]


def test_several_sources_in_one_project_all_count_toward_coverage():
    analysis = analyze_policies([
        item("alpha", path="wiki/a.md"),
        item("alpha", path="wiki/b.md"),
    ])

    assert len(analysis["project_coverage"]["alpha"]["raw_protection"]) == 2


def test_every_known_category_appears_in_a_projects_coverage_map():
    analysis = analyze_policies([item("alpha")])

    assert set(analysis["project_coverage"]["alpha"]) == set(POLICY_CATEGORIES)


# ── formatters ────────────────────────────────────────────────────────────────

def test_scan_groups_discovered_items_under_their_project():
    out = format_policy_scan([
        item("alpha", title="Alpha rule", path="wiki/a.md"),
        item("beta", title="Beta rule", path="wiki/b.md"),
    ])

    assert "### Project: alpha" in out
    assert "**Alpha rule** (decision) - `wiki/a.md`" in out
    assert "### Project: beta" in out


def test_scan_of_nothing_still_renders_a_header():
    assert "Policy Radar Report" in format_policy_scan([])


def test_coverage_lists_only_the_categories_actually_detected():
    out = format_policy_coverage(analyze_policies([item("alpha")]))

    assert "**raw_protection**: Detected in 1 source(s)" in out
    assert "no_cloud" not in out


def test_drift_report_names_both_the_adopters_and_the_gaps():
    analysis = analyze_policies([item("alpha"), item("beta", text="unrelated prose")])

    out = format_policy_drift(analysis)

    assert "### Category: raw_protection" in out
    assert "**Adopted by**: alpha" in out
    assert "**Missing in**: beta" in out


def test_drift_report_says_so_when_there_is_none():
    out = format_policy_drift(analyze_policies([item("alpha"), item("beta")]))

    assert "No policy drift detected." in out


# ── generate_full_report ──────────────────────────────────────────────────────

def test_full_report_contains_every_section():
    report = generate_full_report([item("alpha"), item("beta")])

    for heading in ("## Projects Scanned", "## Consistent Policies",
                    "## Missing Policies", "## Possible Policy Drift",
                    "## Possible Contradiction Candidates", "## Reusable Policies",
                    "## Warnings"):
        assert heading in report


def test_full_report_carries_the_heuristic_caveat():
    """The report must not read as authoritative — it is candidate output."""
    report = generate_full_report([item("alpha")])

    assert "local heuristics" in report
    assert "candidates for review" in report


def test_full_report_of_nothing_reports_none_rather_than_crashing():
    report = generate_full_report([])

    assert report.count("None") >= 3


def test_full_report_lists_a_consistent_policy(capsys):
    report = generate_full_report([item("alpha"), item("beta")])

    consistent_section = report.split("## Consistent Policies")[1]
    assert "raw_protection" in consistent_section.split("## Missing Policies")[0]


# ── save_report_locally ───────────────────────────────────────────────────────

def test_report_is_saved_under_the_configured_config_dir(tmp_path, monkeypatch):
    """Reports are private output and must land in ZURVAN_CONFIG_DIR, not the repo."""
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path))

    path = save_report_locally("# report body")

    assert path.startswith(tmp_path.as_posix())
    assert (tmp_path / "reports" / "policy_radar_report.md").read_text() == "# report body"


def test_saving_creates_the_reports_directory_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path / "fresh"))

    save_report_locally("body")

    assert (tmp_path / "fresh" / "reports").is_dir()


def test_saving_twice_overwrites_rather_than_appending(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path))

    save_report_locally("first")
    save_report_locally("second")

    assert (tmp_path / "reports" / "policy_radar_report.md").read_text() == "second"
