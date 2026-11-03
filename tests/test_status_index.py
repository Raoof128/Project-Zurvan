"""Audit 2026-10-22 §2: README and MCP tables lag the change log, and a test
count is not a status. `docs/status/index.md` is GENERATED from versioned
receipts that distinguish implemented / tested here / operationally closed /
promoted, and `--check` catches drift."""
import json

import pytest

from scripts.status_index import check, render

ROWS = [
    {"item": "Rust MCP", "status": "implemented", "evidence": "CHANGELOG.md", "date": "2026-10-20", "note": ""},
    {"item": "F2 admission", "status": "tested_here", "evidence": "tests/test_evidence_admission.py", "date": "2026-11-03", "note": ""},
    {"item": "Z1b", "status": "implemented", "evidence": "CHANGELOG.md", "date": "2026-10-06", "note": "NOT CLOSED"},
]


def _repo(tmp_path, rows):
    (tmp_path / "docs" / "status").mkdir(parents=True)
    (tmp_path / "docs" / "status" / "receipts.json").write_text(json.dumps(rows))
    for r in rows:
        p = tmp_path / r["evidence"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return tmp_path


def test_render_groups_by_status_in_fixed_order_newest_first(tmp_path):
    text = render(ROWS, repo_root=_repo(tmp_path, ROWS))
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    assert heads == ["## Promoted", "## Operationally closed", "## Tested here", "## Implemented", "## Not built"]
    assert text.index("Rust MCP") < text.index("Z1b")
    assert "NOT CLOSED" in text and "tests/test_evidence_admission.py" in text


@pytest.mark.parametrize("bad", [{**ROWS[0], "status": "done"}, {**ROWS[0], "evidence": "nope.md"}])
def test_bad_rows_are_rejected(bad, tmp_path):
    with pytest.raises(ValueError):
        render([bad], repo_root=_repo(tmp_path, [ROWS[0]]))


def test_check_detects_drift(tmp_path):
    root = _repo(tmp_path, ROWS)
    (root / "docs" / "status" / "index.md").write_text(render(ROWS, repo_root=root))
    assert check(repo_root=root) == 0
    (root / "docs" / "status" / "index.md").write_text("stale\n")
    assert check(repo_root=root) == 1
