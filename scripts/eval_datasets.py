"""Evaluation dataset registry (Z1 design §3.5, §4.1).

A dataset is identified by the SHA-256 of its whole file. Its state decides
what it may be used for: only HOLDOUT gates a promotion, and HOLDOUT/RESERVE
never produce per-item rows on the development side.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from scripts.config import PROJECT_ROOT

STATES = ("DEV", "HOLDOUT", "RESERVE", "RETIRED")
REGISTRY = PROJECT_ROOT / "eval" / "datasets.json"


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
#: the only states whose per-item rows may leave the seal (an allow-list)
ITEMISED_STATES = ("DEV", "RETIRED")


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    state: str
    sha256: str

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f"dataset {self.name!r} has unknown state {self.state!r}")
        if not SHA256_RE.fullmatch(str(self.sha256)):
            raise ValueError(f"dataset {self.name!r} digest is not a lowercase sha256")


class DatasetNotEligible(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dataset_for_file(path) -> DatasetInfo:
    return dataset_for_digest(file_sha256(path))


def dataset_for_digest(digest: str) -> DatasetInfo:
    matches = [(name, entry) for name, entry in _load_registry().get("datasets", {}).items()
               if entry.get("sha256") == digest]
    if len(matches) > 1:
        # two states for one file: refuse rather than let registry order decide
        raise ValueError(f"digest registered twice: {sorted(n for n, _ in matches)}")
    if matches:
        name, entry = matches[0]
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


def itemised_allowed(info: DatasetInfo) -> bool:
    return info.state in ITEMISED_STATES


def require_itemised_allowed(info: DatasetInfo) -> None:
    if not itemised_allowed(info):
        raise DatasetNotEligible(
            "SEALED_DATASET",
            f"dataset {info.name!r} is {info.state}; per-item rows never leave the seal")
