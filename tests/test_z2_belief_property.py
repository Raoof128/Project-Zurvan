"""Z2 gate G3: historical and current views reconstruct correctly.

Seeded random sequences of claim operations, some aborted, interleaved with
unrelated wiki writes. After every committed operation the live current view is
recorded against the revision it minted; afterwards `as_of_revision(R)` must
return exactly what `current()` said at R, for every R.
"""

import random

import pytest

from scripts import claim_ledger as cl
from scripts import knowledge_revision as kr
from scripts.safe_write import write_file_safely

SRC = "wiki/sources/s.md"
QUOTES = [f"fact number {n} is recorded here" for n in range(12)]
SEEDS = range(200)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text("\n".join(QUOTES) + "\n", encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _one(rng, root, live):
    kind = rng.choice(["add", "add", "supersede", "retract", "revise", "wiki"])
    common = dict(actor="local:prop", method="human", root=root)
    if kind == "wiki" or (kind != "add" and not live):
        write_file_safely(str(root / "wiki" / f"n{rng.randrange(10**9)}.md"), "x\n")
        return
    if kind == "add":
        live.append(cl.add(f"claim {rng.randrange(10**6)}", SRC, rng.choice(QUOTES),
                           confidence=rng.choice(["low", "medium", "high"]), **common))
        return
    # A correction targets a CURRENT claim (2026-11-15: supersede/retract refuse an
    # already-ended one, CLAIM_NOT_CURRENT). Recorded aborts may have been adopted
    # by reconcile, so ask the ledger rather than trusting `live`.
    believed = sorted(c for c, st in cl.current(root).items()
                      if st.status in cl.CURRENT_STATUSES)
    if not believed:
        write_file_safely(str(root / "wiki" / f"n{rng.randrange(10**9)}.md"), "x\n")
        return
    target = rng.choice(believed)
    if kind == "supersede":
        live.append(cl.supersede(target, text=f"newer {rng.randrange(10**6)}", source=SRC,
                                 evidence=rng.choice(QUOTES), reason="newer", **common))
    elif kind == "retract":
        cl.retract(target, reason="wrong", basis=["agent_inference"], **common)
    else:
        cl.revise(target, reason="tweak", confidence=rng.choice(["low", "high"]), **common)


@pytest.mark.parametrize("seed", SEEDS)
def test_as_of_revision_replays_every_recorded_current_view(seed, root):
    rng = random.Random(seed)
    live, recorded = [], {}
    for _ in range(rng.randint(5, 30)):
        abort = rng.random() < 0.15
        snapshot_live = list(live)
        try:
            with kr.operation(writer="prop", root=root) as op:
                _one(rng, root, live)
                if abort:
                    raise RuntimeError("abort")
        except RuntimeError:
            live[:] = snapshot_live
            if kr.current(root).state == kr.SUSPECT:
                orphans = [m.mutation_id for m in kr.mutations_in(kr.ORPHANED, root)]
                res = kr.reconcile_revision(orphans, actor="prop", root=root)
                recorded[res["to_revision"]] = cl.current(root)
            continue
        if op.minted_revision is not None:
            recorded[op.minted_revision] = cl.current(root)
    for revision, expected in recorded.items():
        assert cl.as_of_revision(revision, root).claims == expected, (seed, revision)
