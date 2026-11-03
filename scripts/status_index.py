"""Render `docs/status/index.md` from `docs/status/receipts.json`.

Audit 2026-10-22 §2: README and the MCP tables lag the change log, and
neither a test count nor a commit count says what state a thing is in. Each
receipt names ONE item, ONE of four statuses, and the artifact that is the
evidence for it (a repo path or a `CHANGELOG.md#<date>` anchor). The page is
generated so it cannot drift from the receipts; `--check` fails when it has.

    PYTHONPATH=. python scripts/status_index.py          # (re)render
    PYTHONPATH=. python scripts/status_index.py --check  # exit 1 on drift

The statuses are deliberately distinct and NOT a ladder a thing climbs on its
own: "tested here" is a test in this tree; "operationally closed" is a
recorded closure on the real deployment; "promoted" is a passed predeclared
quality/resource gate. Historical entries stay as recorded.
"""
import argparse
import json
import sys
from pathlib import Path

STATUSES = (
    ("promoted", "Promoted"),
    ("operationally_closed", "Operationally closed"),
    ("tested_here", "Tested here"),
    ("implemented", "Implemented"),
    ("not_built", "Not built"),
)
_VALID = {s for s, _ in STATUSES}
_FIELDS = ("item", "status", "evidence", "date", "note")

RECEIPTS = Path("docs") / "status" / "receipts.json"
PAGE = Path("docs") / "status" / "index.md"


def _validate(rows: list[dict], repo_root: Path) -> None:
    for i, row in enumerate(rows):
        missing = [f for f in _FIELDS if f not in row]
        if missing:
            raise ValueError(f"receipt {i} ({row.get('item', '?')!r}) is missing {missing}")
        if row["status"] not in _VALID:
            raise ValueError(f"receipt {i} ({row['item']!r}): unknown status {row['status']!r}; "
                             f"one of {sorted(_VALID)}")
        evidence = row["evidence"].split("#", 1)[0]
        if not (repo_root / evidence).exists():
            raise ValueError(f"receipt {i} ({row['item']!r}): evidence path {evidence!r} does not exist")


def render(rows: list[dict], repo_root: str | Path) -> str:
    repo_root = Path(repo_root)
    _validate(rows, repo_root)
    out = [
        "# Status index",
        "",
        "Generated from `docs/status/receipts.json` by `scripts/status_index.py`;",
        "do not edit by hand. A status is a recorded fact with its evidence, not a",
        "rung on a ladder: *implemented* (code is on `main`), *tested here* (a test",
        "in this tree exercises it), *operationally closed* (a recorded closure on",
        "the real deployment), *promoted* (a passed predeclared quality/resource",
        "gate), and *not built* for a thing people might otherwise infer exists.",
        "Newest first within a group.",
        "",
    ]
    for status, title in STATUSES:
        out.append(f"## {title}")
        out.append("")
        group = sorted((r for r in rows if r["status"] == status), key=lambda r: r["date"], reverse=True)
        if not group:
            out.append("_none recorded_")
        else:
            out.append("| Date | Item | Evidence | Note |")
            out.append("|---|---|---|---|")
            for r in group:
                note = r["note"].replace("|", "\\|")
                out.append(f"| {r['date']} | {r['item']} | `{r['evidence']}` | {note} |")
        out.append("")
    return "\n".join(out)


def _load(repo_root: Path) -> list[dict]:
    return json.loads((repo_root / RECEIPTS).read_text(encoding="utf-8"))


def check(repo_root: str | Path) -> int:
    repo_root = Path(repo_root)
    expected = render(_load(repo_root), repo_root)
    page = repo_root / PAGE
    if not page.exists() or page.read_text(encoding="utf-8") != expected:
        print(f"{PAGE} is out of date; run: PYTHONPATH=. python scripts/status_index.py", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    from scripts.config import PROJECT_ROOT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/status/index.md is stale")
    args = ap.parse_args(argv)
    root = Path(PROJECT_ROOT)
    if args.check:
        return check(root)
    (root / PAGE).write_text(render(_load(root), root), encoding="utf-8")
    print(f"wrote {PAGE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
