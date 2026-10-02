"""Sealed filesystem layout (spec s4-s5; contract s2).

SealPaths.real() is hard-coded to "/" and never consults the environment. Tests use
SealPaths.for_root(tmp_path) so nothing sealed is ever touched. Every public property
must be annotated `-> Path`; the foundation tests introspect on that to prove each one
stays under the root.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EVAL_USER = "zurvaneval"
CANDIDATE_USER = "zurvancandidate"
CORPUS_GROUP = "zurvancorpus"
AUTHOR_USER = "zurvanauthor"


@dataclass(frozen=True)
class SealPaths:
    root: Path

    @classmethod
    def real(cls) -> "SealPaths":
        return cls(Path("/"))  # hard-coded: NEVER env-derived

    @classmethod
    def for_root(cls, root: Path) -> "SealPaths":
        root = Path(root)
        if not root.is_absolute():
            raise ValueError("root must be absolute")
        return cls(root)

    def _r(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @property
    def broker_dir(self) -> Path:  # root:wheel 0555
        return self._r("Library", "ZurvanEval", "broker")

    @property
    def broker_bin(self) -> Path:  # zurvan-broker, zurvan-candidate-run, zurvan-author, age, claude
        return self.broker_dir / "bin"

    @property
    def broker_lib(self) -> Path:  # mirrors scripts/eval_seal + scripts/boundary/d1.py
        return self.broker_dir / "lib"

    @property
    def installed_sha_file(self) -> Path:  # 40-hex + newline
        return self.broker_dir / "INSTALLED_SHA"

    @property
    def installed_tree_file(self) -> Path:  # git tree id of scripts/eval_seal at install time
        return self.broker_dir / "INSTALLED_TREE"

    @property
    def scan_targets_file(self) -> Path:  # {"dev_home", "repo"}, written by install.sh
        return self.broker_dir / "SCAN_TARGETS.json"

    @property
    def pinned_file(self) -> Path:  # "<sha256>  <path>" per pinned binary
        return self.broker_dir / "PINNED.sha256"

    @property
    def empty_mcp_file(self) -> Path:  # the empty MCP config the authoring runner passes to claude
        return self.broker_dir / "empty-mcp.json"

    @property
    def runtime_dir(self) -> Path:  # root-owned candidate runtime (hf models)
        return self._r("Library", "ZurvanEval", "runtime")

    @property
    def corpus_dir(self) -> Path:  # root:zurvancorpus 0750, files 0440
        return self._r("Library", "ZurvanEval", "corpus")

    @property
    def stage_dir(self) -> Path:  # zurvaneval:zurvancandidate 0750 (bundle copies)
        return self._r("Library", "ZurvanEval", "stage")

    @property
    def eval_home(self) -> Path:  # 0700
        return self._r("Users", EVAL_USER)

    @property
    def keys_dir(self) -> Path:  # 0700, TM-excluded: age.key, signing_ed25519
        return self.eval_home / "keys"

    @property
    def private_dir(self) -> Path:  # 0700, TM-excluded: plaintext authoring audit files, claude session state
        return self.eval_home / "private"

    @property
    def author_home(self) -> Path:  # 0700 zurvanauthor: the drafting identity; cannot read keys/sets/runs
        return self._r("Users", AUTHOR_USER)

    @property
    def author_workspace(self) -> Path:  # zurvanauthor-owned, zurvaneval-readable, dev-unreadable, TM-excluded
        return self.author_home / "workspace"

    @property
    def sets_dir(self) -> Path:  # container: canaries + dev-ref + generations/ + active (see sealed_store)
        return self.eval_home / "sets"

    @property
    def runs_dir(self) -> Path:  # ledger.jsonl ledger.lock cache/ rows/ errors.log
        return self.eval_home / "runs"

    @property
    def incidents_dir(self) -> Path:
        return self.eval_home / "incidents"

    @property
    def manifest_file(self) -> Path:  # sealed authoritative copy (+ .sig)
        return self.eval_home / "manifest.json"

    @property
    def candidate_home(self) -> Path:
        return self._r("Users", CANDIDATE_USER)

    @property
    def scratch_dir(self) -> Path:  # 0700 zurvancandidate, wiped per run
        return self.candidate_home / "scratch"

    @property
    def drop_dir(self) -> Path:  # 1733
        return self._r("Users", "Shared", "zurvan-eval-drop")

    @property
    def sudoers_file(self) -> Path:
        return self._r("etc", "sudoers.d", "zurvan-eval")

    @property
    def pf_anchor(self) -> Path:
        return self._r("etc", "pf.anchors", "zurvan-eval")
