"""Evaluation dataset registry (Z1 design §3.5, §4.1).

A dataset is identified by the SHA-256 of its whole file. Its state decides
what it may be used for: only HOLDOUT gates a promotion, and HOLDOUT/RESERVE
never produce per-item rows on the development side.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from scripts.config import PROJECT_ROOT

STATES = ("DEV", "HOLDOUT", "RESERVE", "RETIRED")
REGISTRY = PROJECT_ROOT / "eval" / "datasets.json"


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    state: str
    sha256: str


class DatasetNotEligible(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dataset_for_file(path) -> DatasetInfo:
    digest = file_sha256(path)
    for name, entry in _load_registry().get("datasets", {}).items():
        if entry.get("sha256") == digest:
            state = entry.get("state")
            if state not in STATES:
                raise ValueError(f"dataset {name!r} has unknown state {state!r}")
            return DatasetInfo(name, state, digest)
    return DatasetInfo("unregistered", "DEV", digest)


def require_promotion_eligible(info: DatasetInfo) -> None:
    if info.state != "HOLDOUT":
        raise DatasetNotEligible(
            "NOT_PROMOTION_ELIGIBLE",
            f"dataset {info.name!r} is {info.state}; only a sealed HOLDOUT gates a promotion")


def require_itemised_allowed(info: DatasetInfo) -> None:
    if info.state in ("HOLDOUT", "RESERVE"):
        raise DatasetNotEligible(
            "SEALED_DATASET",
            f"dataset {info.name!r} is {info.state}; per-item rows never leave the seal")
