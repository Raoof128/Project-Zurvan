"""Saved per-item eval runs and rank movers (Z1 design §3.4).

Runs are private local files under ~/.zurvan/eval-runs (never the repo,
never indexed). Items are named by INDEX. HOLDOUT/RESERVE runs are refused
before anything is written: their per-item rows never leave the seal.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.eval_datasets import DatasetInfo, DatasetNotEligible, require_itemised_allowed

RUNS_DIR = Path.home() / ".zurvan" / "eval-runs"


def save_run(metrics: dict, rows: list, dataset: DatasetInfo, runs_dir: Path = RUNS_DIR) -> Path:
    require_itemised_allowed(dataset)
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runs_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = runs_dir / f"run-{stamp}-{metrics.get('result_digest', 'nodigest')[:12]}.json"
    body = {"schema": "zurvan.eval.run.v1",
            "dataset": {"name": dataset.name, "state": dataset.state, "sha256": dataset.sha256},
            "metrics": {k: v for k, v in metrics.items() if not k.startswith("_")},
            "rows": [{"index": r["index"], "first_hit_rank": r["first_hit_rank"],
                      "retrieved": r["retrieved"]} for r in rows]}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2, sort_keys=True)
    return path


def load_run(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _info(run: dict) -> DatasetInfo:
    d = run["dataset"]
    return DatasetInfo(d["name"], d["state"], d["sha256"])


def movers(a: dict, b: dict) -> list:
    info_a, info_b = _info(a), _info(b)
    require_itemised_allowed(info_a)
    require_itemised_allowed(info_b)
    if info_a.sha256 != info_b.sha256:
        raise DatasetNotEligible("DATASET_MISMATCH", "runs were scored on different datasets")
    rank_b = {r["index"]: r["first_hit_rank"] for r in b["rows"]}
    return [{"index": r["index"], "rank_a": r["first_hit_rank"], "rank_b": rank_b.get(r["index"])}
            for r in a["rows"] if rank_b.get(r["index"]) != r["first_hit_rank"]]
