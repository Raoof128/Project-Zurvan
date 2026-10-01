"""Z1b development-side client for the sealed broker (contract s4.1).

This module is the ONLY thing the development identity uses to reach the seal. It builds a
frozen-commit bundle, drops it in the world-writable drop directory, and runs the one broker
executable through `sudo -n -u zurvaneval`. Everything that comes back is validated against the
broker's closed output schema before it is believed; the raw text of a malformed answer is never
printed, and the broker's stderr is never captured at all.

Refusals are `ClientRefusal(code)`: a code and nothing else. `SEAL_NOT_CONFIGURED` is a refusal
(exit 2), never a quiet success: a machine without the seal must not look like a machine that
passed it.
"""
from __future__ import annotations

import errno
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.eval_seal import output as _seal_output
from scripts.eval_seal import sysenv
from scripts.eval_seal.codes import ALL_CODES, SealRefusal
from scripts.eval_seal.paths import EVAL_USER, SealPaths

BROKER = "/Library/ZurvanEval/broker/bin/zurvan-broker"
REPO = Path(__file__).resolve().parent.parent
DROP_DIR = SealPaths.real().drop_dir
SETTINGS_PATH = REPO / ".claude" / "settings.json"
ROOT_UID = 0  # the uid that must own the broker tree (a test may substitute its own)

GIT = "/usr/bin/git"
SUDO = "/usr/bin/sudo"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_DECLARE_RE = re.compile(r"eval/z1b/declarations/[a-z0-9-]{1,64}\.json")
_MAX_STDOUT = 1024 * 1024
_BROKER_TIMEOUT_S = 3900  # the broker's own run timeout is 3600 s
_GIT_TIMEOUT_S = 600
_SUITES = ("holdout", "reserve")
_DENY_TOOLS = ("Read", "Edit", "Write")
_RULE_RE = re.compile(r"(Read|Edit|Write)\((.*)\)")


class ClientRefusal(Exception):
    """A refusal on the development side. Carries a code, never text."""

    def __init__(self, code: str):
        if code not in ALL_CODES:
            code = "INTERNAL"
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------- configuration

def seal_configured() -> bool:
    """The zurvaneval identity exists AND the broker executable exists."""
    try:
        pwd.getpwnam(EVAL_USER)
    except KeyError:
        return False
    return os.path.exists(BROKER)


def _client_require_configured() -> None:
    if not seal_configured():
        raise ClientRefusal("SEAL_NOT_CONFIGURED")


# ---------------------------------------------------------------------------- git helpers

def _client_git_env(home: Path) -> dict:
    return sysenv.clean_env(home, {"TMPDIR": str(home)})


def _client_git(args: list, *, repo: Path = None, git_dir: Path = None, home: Path):
    cmd = [GIT, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    if git_dir is not None:
        cmd += ["--git-dir", str(git_dir)]
    cmd += list(args)
    return subprocess.run(cmd, env=_client_git_env(home), cwd="/", stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT_S)


def _client_assert_frozen(commit: str, repo: Path, home: Path) -> None:
    """A clean working tree and a commit that exists. Anything else is CANDIDATE_NOT_FROZEN."""
    if type(commit) is not str or not _COMMIT_RE.fullmatch(commit):
        raise ClientRefusal("CANDIDATE_NOT_FROZEN")
    try:
        status = _client_git(["status", "--porcelain"], repo=repo, home=home)
        if status.returncode != 0 or status.stdout.strip():
            raise ClientRefusal("CANDIDATE_NOT_FROZEN")
        known = _client_git(["cat-file", "-e", commit + "^{commit}"], repo=repo, home=home)
        if known.returncode != 0:
            raise ClientRefusal("CANDIDATE_NOT_FROZEN")
    except ClientRefusal:
        raise
    except (OSError, subprocess.SubprocessError):
        raise ClientRefusal("CANDIDATE_NOT_FROZEN") from None


def _client_objects_dir(repo: Path, home: Path) -> str:
    out = _client_git(["rev-parse", "--git-common-dir"], repo=repo, home=home)
    if out.returncode != 0:
        raise ClientRefusal("CANDIDATE_NOT_FROZEN")
    common = out.stdout.decode("utf-8", "replace").strip()
    if not common:
        raise ClientRefusal("CANDIDATE_NOT_FROZEN")
    if not os.path.isabs(common):
        common = os.path.join(str(repo), common)
    return os.path.join(common, "objects")


def make_bundle(commit: str, *, repo: Path, drop_dir: Path) -> str:
    """Write `z1b-<commit>-<rand16>.bundle` into drop_dir whose HEAD is exactly `commit`.

    The broker's intake requires the frozen commit to BE the bundle's HEAD (the runner clones the
    bundle and runs whatever HEAD checks out). A bare `git bundle create <commit>` records no HEAD,
    and pointing a ref at the commit inside the caller's repository would mutate it. So the bundle
    is cut from a throwaway bare repository that borrows the caller's objects through
    `objects/info/alternates` and whose HEAD names the commit. Nothing touches the caller's refs.
    Refusals happen before anything is written to drop_dir.
    """
    repo, drop_dir = Path(repo), Path(drop_dir)
    work = tempfile.mkdtemp(prefix="z1b-bundle-")
    dst_fd = None
    dest = None
    try:
        home = Path(work)
        _client_assert_frozen(commit, repo, home)
        bare = home / "t.git"
        if _client_git(["init", "--bare", "--quiet", "--template=", str(bare)], home=home).returncode != 0:
            raise ClientRefusal("INTERNAL")
        with open(bare / "objects" / "info" / "alternates", "w", encoding="utf-8") as fh:
            fh.write(_client_objects_dir(repo, home) + "\n")
        for args in (["update-ref", "refs/heads/z1b", commit], ["symbolic-ref", "HEAD", "refs/heads/z1b"]):
            if _client_git(args, git_dir=bare, home=home).returncode != 0:
                raise ClientRefusal("CANDIDATE_NOT_FROZEN")
        tmp_bundle = home / "out.bundle"
        if _client_git(["bundle", "create", str(tmp_bundle), "HEAD"], git_dir=bare, home=home).returncode != 0:
            raise ClientRefusal("INTERNAL")
        name = "z1b-%s-%s.bundle" % (commit, secrets.token_hex(8))
        dest = drop_dir / name
        try:
            dst_fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with open(tmp_bundle, "rb") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(dst_fd, view):]
            os.fsync(dst_fd)
            os.fchmod(dst_fd, 0o644)
        except OSError:
            try:
                os.unlink(str(dest))
            except OSError:
                pass
            raise ClientRefusal("INTERNAL") from None
        finally:
            if dst_fd is not None:
                os.close(dst_fd)
        return name
    except ClientRefusal:
        raise
    except (OSError, subprocess.SubprocessError):
        raise ClientRefusal("INTERNAL") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def broker_stale(installed_tree, repo: Path) -> bool:
    """True unless the installed broker tree id equals `git rev-parse HEAD:scripts/eval_seal`."""
    if type(installed_tree) is not str or not _COMMIT_RE.fullmatch(installed_tree):
        return True
    work = tempfile.mkdtemp(prefix="z1b-stale-")
    try:
        out = _client_git(["rev-parse", "HEAD:scripts/eval_seal"], repo=Path(repo), home=Path(work))
    except (OSError, subprocess.SubprocessError):
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if out.returncode != 0:
        return True
    return out.stdout.decode("utf-8", "replace").strip() != installed_tree


# ---------------------------------------------------------------------------- broker calls

def _client_validate(text: str) -> dict:
    """Strict validation by declared kind.

    validate_broker_json treats ANY `ok: false` document as a refusal, so a seal-check that
    honestly reports a failing check would be unreadable (and surface as INTERNAL). A document
    that declares the seal-check schema is therefore validated as one; everything else goes through
    the general dispatcher. Both validators are strict, so the pre-parse only chooses which.
    """
    try:
        probe = json.loads(text)
    except (ValueError, RecursionError):
        raise ValueError("invalid broker output") from None
    if type(probe) is dict and probe.get("schema") == _seal_output.SEALCHECK_SCHEMA_ID:
        return _seal_output.validate_sealcheck_json(text)
    return _seal_output.validate_broker_json(text)


def call_broker(argv: list, *, runner=subprocess.run) -> dict:
    """Run the broker as zurvaneval and return its validated document.

    stdout is validated against the closed broker schema; anything else is INTERNAL and the raw
    text is dropped. stderr goes to /dev/null and is never read. A refusal document raises
    ClientRefusal(code).
    """
    cmd = [SUDO, "-n", "-u", EVAL_USER, BROKER] + [str(a) for a in argv]
    try:
        proc = runner(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                      env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, timeout=_BROKER_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        raise ClientRefusal("INTERNAL") from None
    raw = getattr(proc, "stdout", None)
    if type(raw) is not bytes or not raw or len(raw) > _MAX_STDOUT:
        raise ClientRefusal("INTERNAL")
    try:
        doc = _client_validate(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ClientRefusal("INTERNAL") from None
    if doc.get("ok") is False and "code" in doc:
        raise ClientRefusal(doc["code"])
    if getattr(proc, "returncode", None) not in ((0, 2) if doc.get("ok") is False else (0,)):
        raise ClientRefusal("INTERNAL")  # an ok document with a failing exit status is not believable
    return doc


def _client_expect(doc: dict, schema_id: str) -> dict:
    if doc.get("schema") != schema_id:
        raise ClientRefusal("INTERNAL")
    return doc


def client_status() -> dict:
    """Broker status plus the CLIENT's staleness verdict (the broker never judges staleness)."""
    _client_require_configured()
    doc = dict(_client_expect(call_broker(["status"]), _seal_output.STATUS_SCHEMA_ID))
    doc["stale"] = broker_stale(doc.get("installed_tree"), REPO)
    return doc


def client_run(commit: str, suite: str, declare: str = None, final_acceptance: bool = False) -> dict:
    """Freeze, drop and run one candidate commit on the sealed suite. Charges a budget run when not cached."""
    _client_require_configured()
    if suite not in _SUITES or type(final_acceptance) is not bool:
        raise ClientRefusal("BAD_ARGS")
    if declare is not None and (type(declare) is not str or not _DECLARE_RE.fullmatch(declare)):
        raise ClientRefusal("DECLARATION_INVALID")
    if suite == "holdout" and final_acceptance:
        raise ClientRefusal("BAD_ARGS")
    if suite == "reserve" and not final_acceptance:
        raise ClientRefusal("RESERVE_NOT_ELIGIBLE")
    work = tempfile.mkdtemp(prefix="z1b-run-")
    try:
        _client_assert_frozen(commit, REPO, Path(work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if client_status()["stale"]:
        raise ClientRefusal("BROKER_STALE")
    name = make_bundle(commit, repo=REPO, drop_dir=DROP_DIR)
    argv = ["run", "--commit", commit, "--bundle", name, "--suite", suite]
    if declare is not None:
        argv += ["--declare", declare]
    if final_acceptance:
        argv.append("--final-acceptance")
    try:
        doc = _client_expect(call_broker(argv), _seal_output.RESULT_SCHEMA_ID)
        if doc.get("candidate_commit") != commit or doc.get("suite") != suite:
            raise ClientRefusal("INTERNAL")
        return doc
    finally:
        try:  # the broker unlinks a staged drop file; a refusal before staging leaves it behind
            os.unlink(str(DROP_DIR / name))
        except OSError:
            pass


def client_seal_check() -> dict:
    """The broker's identity checks merged with the dev-identity half. ok only when both are ok."""
    _client_require_configured()
    local = local_seal_check(SealPaths.real())
    if not local["configured"]:
        raise ClientRefusal("SEAL_NOT_CONFIGURED")
    doc = _client_expect(call_broker(["seal-check"]), _seal_output.SEALCHECK_SCHEMA_ID)
    checks = dict(doc["checks"])
    checks.update(local["checks"])
    return {"ok": bool(doc["ok"]) and all(checks.values()), "broker_sha": doc["broker_sha"], "checks": checks}


# contract names (status / run / seal_check): aliases, so the generic names stay unique as defs
status = client_status
run = client_run
seal_check = client_seal_check


# ---------------------------------------------------------------------------- dev-identity seal check

def _client_sealed_paths(paths: SealPaths) -> list:
    return [paths.eval_home, paths.keys_dir, paths.sets_dir, paths.runs_dir, paths.incidents_dir,
            paths.manifest_file, paths.corpus_dir, paths.stage_dir, paths.candidate_home, paths.scratch_dir]


def client_deny_rules(paths: SealPaths) -> list:
    """The permissions.deny rules .claude/settings.json must carry (Claude Code rule syntax).

    `//<abs>` is Claude Code's absolute-path form for Read/Edit/Write rules; Edit rules govern
    every file-editing tool. Bash rules are a best effort only (a script can assemble a path at
    run time): the OS permission, not these rules, is the wall.
    """
    roots = [paths.eval_home, paths.corpus_dir, paths.stage_dir, paths.candidate_home]
    rules = []
    for root in roots:
        body = "/" + str(root)  # str(root) already starts with "/", giving "//abs"
        for tool in _DENY_TOOLS:
            rules.append("%s(%s/**)" % (tool, body))
        rules.append("Read(%s)" % body)
        rules.append("Bash(*%s*)" % root)
    return rules


def _client_rule_covers(rule: str, tool: str, path: str) -> bool:
    m = _RULE_RE.fullmatch(rule) if type(rule) is str else None
    if not m or m.group(1) != tool or not m.group(2).startswith("//"):
        return False
    body = m.group(2)[1:]
    for tail in ("/**", "/*"):
        if body.endswith(tail):
            body = body[:-len(tail)]
            break
    body = body.rstrip("/")
    return bool(body) and (path == body or path.startswith(body + "/"))


def _client_deny_rules_present(sealed: list) -> bool:
    try:
        settings = json.loads(Path(SETTINGS_PATH).read_text(encoding="utf-8"))
        deny = settings["permissions"]["deny"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if type(deny) is not list:
        return False
    for path in sealed:
        for tool in ("Read", "Edit"):
            if not any(_client_rule_covers(r, tool, str(path)) for r in deny):
                return False
    return True


def _client_probe(path: Path) -> str:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return "ENOENT"
    except NotADirectoryError:
        return "ENOENT"
    except OSError as exc:
        return "EACCES" if exc.errno == errno.EACCES else "ERROR"
    os.close(fd)
    return "READABLE"


def _client_broker_tree(broker_dir: Path):
    """(exists, root_owned, not_writable) over the whole broker tree, symlinks refused."""
    try:
        top = os.lstat(str(broker_dir))
    except OSError:
        return False, False, False
    if not stat.S_ISDIR(top.st_mode):
        return True, False, False
    owned = writable = True
    stack = [str(broker_dir)]
    while stack:
        cur = stack.pop()
        try:
            st = os.lstat(cur)
            if stat.S_ISLNK(st.st_mode):
                return True, False, False
            if st.st_uid != ROOT_UID:
                owned = False
            if st.st_mode & 0o022:
                writable = False
            if stat.S_ISDIR(st.st_mode):
                with os.scandir(cur) as it:
                    stack.extend(e.path for e in it)
        except OSError:
            return True, False, False
    return True, owned, writable


def local_seal_check(paths: SealPaths) -> dict:
    """The development-identity half of Z1b gate 1 and 1b.

    Every sealed path must refuse this identity with EACCES. ENOENT means the seal is not there:
    it is reported as not configured and is never a pass. Also: the settings deny rules cover every
    sealed path for Read and Edit, and the broker tree is root-owned and writable by nobody else.
    """
    sealed = _client_sealed_paths(paths)
    probed = {str(p): _client_probe(p) for p in sealed}
    missing = any(v == "ENOENT" for v in probed.values())
    exists, owned, not_writable = _client_broker_tree(paths.broker_dir)
    configured = not missing and exists
    checks = {
        "dev_cannot_read_sealed": all(v == "EACCES" for v in probed.values()),
        "local_deny_rules_present": _client_deny_rules_present([str(p) for p in sealed]),
        "local_broker_root_owned": exists and owned,
        "local_broker_not_writable": exists and not_writable,
    }
    return {"configured": configured, "ok": configured and all(checks.values()),
            "paths": probed, "checks": checks}


# ---------------------------------------------------------------------------- DEV set runner

def _client_default_search(corpus_dir: Path):
    """search(query) -> [{"page","heading","score"}] over the local corpus export's index."""
    from scripts.hybrid_search import search_hybrid
    from scripts.z1b_candidate import chunk_to_page

    db = str(Path(corpus_dir) / "search.sqlite")

    def search(query: str) -> list:
        out = []
        for chunk in search_hybrid(query, limit=10, db_path=db) or []:
            page = chunk_to_page(chunk.get("source_path"), "")
            if page is not None:
                heading = chunk.get("heading")
                out.append({"page": page, "heading": heading if isinstance(heading, str) else None,
                            "score": float(chunk.get("hybrid_score", 0.0))})
        return out

    return search


def _client_load_items(dev_path: Path, spec: dict) -> list:
    from scripts.eval_seal.spec import validate_item

    items = []
    try:
        lines = Path(dev_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise ClientRefusal("BAD_ARGS") from None
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            validate_item(item, spec)
        except (ValueError, RecursionError):
            raise ClientRefusal("BAD_ARGS") from None
        items.append(item)
    if not items:
        raise ClientRefusal("BAD_ARGS")
    return items


def client_dev(dev_path, corpus_dir, *, search=None, spec_path=None) -> dict:
    """Score the DEV set locally with the sealed-side grader against a local corpus export.

    The dev file's digest is looked up in the dataset registry first: a HOLDOUT or RESERVE file is
    refused (per-item rows never leave the seal), so this command cannot be pointed at a sealed set.
    """
    from scripts import eval_datasets as ed
    from scripts.eval_seal import corpus as seal_corpus
    from scripts.eval_seal import grade
    from scripts.eval_seal.spec import load_spec

    dev_path, corpus_dir = Path(dev_path), Path(corpus_dir)
    try:
        info = ed.dataset_for_file(dev_path)
    except OSError:
        raise ClientRefusal("BAD_ARGS") from None
    ed.require_itemised_allowed(info)
    try:
        spec = load_spec(Path(spec_path) if spec_path else REPO / "eval" / "z1b" / "spec.json")
    except SealRefusal as exc:
        raise ClientRefusal(exc.code) from None
    items = _client_load_items(dev_path, spec)
    try:
        pages = seal_corpus.corpus_pages(corpus_dir)
    except SealRefusal:
        pages = None  # an unreadable export leaves targets unresolved rather than faking presence
    fn = search if search is not None else _client_default_search(corpus_dir)
    rows, detail = [], []
    for item in items:
        row = grade.grade_item(item, fn(item["query"]), spec, corpus_pages=pages)
        rows.append(row)
        detail.append({"id": item["id"], "stratum": row["stratum"], "rank": row["rank"],
                       "set_fraction": row["set_fraction"], "abstained": row["abstained"],
                       "target_missing": row["target_missing"]})
    agg = grade.aggregate(rows, spec, per_stratum=True)
    agg["dataset"] = {"name": info.name, "state": info.state, "sha256": info.sha256}
    agg["evaluator_version"] = grade.EVALUATOR_VERSION
    agg["rows"] = detail
    return agg


# ---------------------------------------------------------------------------- CLI glue

def client_cli_main(args) -> int:
    """`zurvan eval z1b <action>`. Exit 0 ok, 2 refusal (prints the CODE only)."""
    from scripts.eval_datasets import DatasetNotEligible

    def emit(doc):
        print(json.dumps(doc, sort_keys=True))

    try:
        action = getattr(args, "z1b_action", None)
        if action == "status":
            doc = client_status()
            emit(doc)
            if doc["stale"]:
                print("BROKER_STALE", file=sys.stderr)
            return 0
        if action == "run":
            emit(client_run(args.commit, args.suite, args.declare, bool(args.final_acceptance)))
            return 0
        if action == "seal-check":
            doc = client_seal_check()
            emit(doc)
            return 0 if doc["ok"] else 2
        if action == "dev":
            emit(client_dev(args.dev, args.corpus))
            return 0
        if action == "promote":
            from scripts import z1b_promotion
            emit(z1b_promotion.promote(args.commit, args.declaration))
            return 0
        if action == "final-acceptance":
            from scripts import z1b_promotion
            emit(z1b_promotion.final_acceptance(args.commit))
            return 0
        print("BAD_ARGS")
        return 2
    except (ClientRefusal, DatasetNotEligible, SealRefusal) as exc:
        print(exc.code)
        return 2
    except Exception:  # noqa: BLE001 - nothing but a code leaves this function
        print("INTERNAL")
        return 2
