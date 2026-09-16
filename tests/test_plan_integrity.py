"""Structural checks on implementation plans.

Written after a self-review passed a plan containing six defects, including a
final task whose only instruction was "implement until the gauntlet is green".
The review had checked that interface NAMES appeared somewhere in the document —
which is satisfied by a plan that mentions a function and never defines it
consistently.

These checks are deliberately mechanical. They cannot judge whether a plan is
good; they can refuse to let it be silently incomplete.
"""
import re
from pathlib import Path

import pytest

PLANS = Path(__file__).resolve().parents[1] / "docs" / "superpowers" / "plans"

# Plans written BEFORE this standard existed (2026-09-16), each with its reason.
# Exemptions are named and counted, never a silent skip: a grandfather clause
# that can grow quietly is how a standard dies. `test_the_exemption_list_does_
# not_grow` pins the size, so adding one is a deliberate, reviewed act.
GRANDFATHERED = {
    "2026-06-02-phase18-living-wiki.md":
        "predates the plan-integrity standard; executed and shipped 2026-06",
}

PLAN_FILES = [p for p in sorted(PLANS.glob("*.md")) if p.name not in GRANDFATHERED] \
    if PLANS.is_dir() else []

pytestmark = pytest.mark.skipif(not PLAN_FILES, reason="no plans yet")


def _tasks(text):
    """[(heading, body)] for each '## Task N' section."""
    parts = re.split(r"^## (Task [^\n]+)$", text, flags=re.M)
    return list(zip(parts[1::2], parts[2::2]))


@pytest.mark.parametrize("plan", PLAN_FILES, ids=lambda p: p.name)
def test_every_task_shows_code(plan):
    """A task that only describes is a placeholder wearing a task's clothes.

    The doctrine's plan contract calls this out by name: steps that describe
    without showing are plan failures.
    """
    for heading, body in _tasks(plan.read_text(encoding="utf-8")):
        fences = body.count("\n```")
        assert fences >= 2, f"{plan.name}: '{heading}' shows no code block"


@pytest.mark.parametrize("plan", PLAN_FILES, ids=lambda p: p.name)
def test_no_function_signature_drifts_within_a_plan(plan):
    """The same function must have ONE signature everywhere it appears.

    An Interfaces block promising `confirm(root, source_id, ...) -> int` while
    the implementation defines `confirm(source_bytes, ...) -> tuple` is precisely
    what a zero-context implementer cannot recover from: they see only their own
    task, and both statements look authoritative.
    """
    text = plan.read_text(encoding="utf-8")
    def _strip_nested(sig: str) -> str:
        # `Callable[[Path], None]` contains a comma that is NOT a parameter
        # separator. Collapse bracketed groups before splitting, or the checker
        # reports drift that does not exist — a check that fails for the wrong
        # reason is worse than no check.
        prev = None
        while prev != sig:
            prev = sig
            sig = re.sub(r"\[[^\[\]]*\]", "", sig)
        return sig

    sigs = {}
    for m in re.finditer(r"^def (\w+)\((.*?)\)\s*(?:->|:)", text, flags=re.M | re.S):
        name = m.group(1)
        params = _strip_nested(re.sub(r"\s+", " ", m.group(2))).strip()
        names = tuple(p.split(":")[0].split("=")[0].strip() for p in params.split(",") if p.strip())
        sigs.setdefault(name, set()).add(names)
    drifted = {n: v for n, v in sigs.items() if len(v) > 1}
    assert not drifted, "signature drift: " + "; ".join(
        f"{n} declared as {sorted(v)}" for n, v in drifted.items()
    )


@pytest.mark.parametrize("plan", PLAN_FILES, ids=lambda p: p.name)
def test_no_deferred_markers(plan):
    text = plan.read_text(encoding="utf-8")
    hits = [m.group(0) for m in re.finditer(r"\bTBD\b|\bTODO\b|\bFIXME\b|\bXXX\b", text)]
    assert not hits, f"{plan.name}: deferred markers {hits}"


@pytest.mark.parametrize("plan", PLAN_FILES, ids=lambda p: p.name)
def test_plan_declares_global_constraints_verbatim(plan):
    """Every task implicitly includes them, so they must actually be present."""
    text = plan.read_text(encoding="utf-8")
    assert "## Global Constraints" in text, f"{plan.name}: no Global Constraints section"


@pytest.mark.parametrize("plan", PLAN_FILES, ids=lambda p: p.name)
def test_a_plan_does_not_leak_what_its_subject_protects(plan):
    import json
    text = plan.read_text(encoding="utf-8")
    names = [l.strip() for l in Path(".private-projects").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    hit = [n for n in names if re.search(re.escape(n), text, re.I)]
    assert not hit, f"{plan.name} contains private project name(s): {hit}"
    gold = [json.loads(l)["query"] for l in open(__import__("scripts.instruments", fromlist=["x"]).gold_path()) if l.strip()]
    echo = [q for q in gold if q.lower() in text.lower()]
    assert not echo, f"{plan.name} echoes evaluation item(s) verbatim"


def test_the_exemption_list_does_not_grow():
    """A grandfather clause that grows quietly is how a standard dies.

    Adding a plan here must be deliberate and reviewed — which is exactly what
    changing this number forces.
    """
    assert len(GRANDFATHERED) == 1, (
        f"{len(GRANDFATHERED)} grandfathered plans; every new plan must meet the "
        "standard rather than join the exemption list"
    )


def test_grandfathered_plans_still_exist():
    """An exemption for a file that is gone is dead weight hiding a real gap."""
    for name in GRANDFATHERED:
        assert (PLANS / name).is_file(), f"exemption names a missing plan: {name}"
