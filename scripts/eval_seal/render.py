"""Operator-script engine for the sealed evaluation (spec s5.1, s5.2; contract s5).

Every operator script (setup.sh, install.sh, install_corpus.sh, tamper_drill.sh, uninstall.sh, refresh_acl.sh) is a thin
shell wrapper around this module. It builds a PLAN: the files that would be installed (written under the
stage directory, mirroring absolute paths) and `commands.txt`, one shell command per line.

* `--render DIR` : the stage is DIR; nothing privileged happens; DIR/commands.txt is the audit artefact.
* real mode      : the shell wrapper (root, via sudo) stages into a root-owned temp dir and then executes the
                   very same commands.txt with `/bin/bash -euo pipefail`. What you review is what runs.

stdlib only, Python 3.9 compatible. Run as a script by the CLT python with -I, or imported by tests.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from string import Template

try:
    from .paths import AUTHOR_USER, CANDIDATE_USER, CORPUS_GROUP, EVAL_USER, SealPaths
    from .manifest import NAMESPACE as MANIFEST_NAMESPACE, PRINCIPAL as MANIFEST_PRINCIPAL
    from .promotion import NAMESPACE as PROMOTION_NAMESPACE
except ImportError:  # run as a script: scripts/eval_seal is not a package here
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.eval_seal.paths import AUTHOR_USER, CANDIDATE_USER, CORPUS_GROUP, EVAL_USER, SealPaths
    from scripts.eval_seal.manifest import NAMESPACE as MANIFEST_NAMESPACE, PRINCIPAL as MANIFEST_PRINCIPAL
    from scripts.eval_seal.promotion import NAMESPACE as PROMOTION_NAMESPACE

# the eval signing key verifies in BOTH sealed namespaces (manifest + promotion attestation, #16); the
# anchor lists both, while ssh-keygen still enforces each signature's own namespace at verify time.
ALLOWED_NAMESPACES = ",".join((MANIFEST_NAMESPACE, PROMOTION_NAMESPACE))

_P = SealPaths.real()
TEMPLATES = Path(__file__).resolve().parent / "templates"

CLT_PYTHON = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"
ZE_ROOT = "/Library/ZurvanEval"
BROKER_DIR = str(_P.broker_dir)
BROKER_BIN = str(_P.broker_bin)
BROKER_LIB = str(_P.broker_lib)
RUNTIME_DIR = str(_P.runtime_dir)
CORPUS_DIR = str(_P.corpus_dir)
STAGE_DIR = str(_P.stage_dir)
EVAL_HOME = str(_P.eval_home)
KEYS_DIR = str(_P.keys_dir)
PRIVATE_DIR = str(_P.private_dir)
SETS_DIR = str(_P.sets_dir)
RUNS_DIR = str(_P.runs_dir)
INCIDENTS_DIR = str(_P.incidents_dir)
CANDIDATE_HOME = str(_P.candidate_home)
SCRATCH_DIR = str(_P.scratch_dir)
AUTHOR_HOME = str(_P.author_home)
AUTHOR_WS = str(_P.author_workspace)
AUTHOR_PRIVATE = AUTHOR_HOME + "/private"
DROP_DIR = str(_P.drop_dir)
SUDOERS_FILE = str(_P.sudoers_file)
PF_ANCHOR_FILE = str(_P.pf_anchor)
PF_CONF = "/etc/pf.conf"
PF_CONF_BAK = "/etc/pf.conf.zurvan-eval.bak"
CRON_DENY_FILES = ("/usr/lib/cron/cron.deny", "/usr/lib/cron/at.deny")
SCAN_LABEL = "local.zurvan.eval.scan"
PF_LABEL = "local.zurvan.eval.pf"
SCAN_PLIST = "/Library/LaunchDaemons/%s.plist" % SCAN_LABEL
PF_PLIST = "/Library/LaunchDaemons/%s.plist" % PF_LABEL
HF_MODEL_RE = re.compile(r"models--[A-Za-z0-9_.-]+")
DEFAULT_HF_MODEL = "models--sentence-transformers--all-MiniLM-L6-v2"

WRAPPERS = (  # (wrapper name, module, entry function): the entry is a module-level function taking argv
    ("zurvan-broker", "broker", "broker_main"),
    ("zurvan-candidate-run", "candidate_run", "main"),
    ("zurvan-author", "author", "author_main"),
)
BROKER_WRAPPER = BROKER_BIN + "/zurvan-broker"
CANDIDATE_WRAPPER = BROKER_BIN + "/zurvan-candidate-run"
EXPORT_PATHS = ("scripts/eval_seal", "scripts/boundary/d1.py", "eval/z1b/spec.json")
USER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
RESERVED_USERS = frozenset(["root", "ALL", EVAL_USER, CANDIDATE_USER, AUTHOR_USER])
FORBIDDEN_STAGE_PREFIXES = ("/etc", "/private/etc", "/Library", "/System", "/usr", "/bin", "/sbin",
                            "/Applications", "/var/at", "/private/var/at", "/private/var/db")


class OperatorRefusal(Exception):
    """A refusal with a human-readable reason (operator scripts only; never crosses the broker boundary)."""


# ------------------------------------------------------------------ validation

def validate_sha(sha):
    if not isinstance(sha, str) or SHA_RE.fullmatch(sha) is None:
        raise OperatorRefusal("the sha must be exactly 40 lowercase hex characters")
    return sha


def validate_user(name):
    if not isinstance(name, str) or USER_RE.fullmatch(name) is None or name in RESERVED_USERS:
        raise OperatorRefusal("invalid or reserved development user name")
    return name


def check_stage(stage):
    real = os.path.realpath(str(stage))
    if real == "/":
        raise OperatorRefusal("the render directory must not be /")
    for pre in FORBIDDEN_STAGE_PREFIXES:
        if real == pre or real.startswith(pre + "/"):
            raise OperatorRefusal("the render directory must not be inside %s" % pre)
    return Path(os.path.abspath(str(stage)))


# ------------------------------------------------------------------ plan

class _Raw(str):
    """A command token that is emitted unquoted (shell variables)."""


class Plan:
    def __init__(self, kind, stage):
        self.kind = kind
        self.stage = Path(stage)
        self.lines = []

    def comment(self, text):
        for part in str(text).splitlines() or [""]:
            self.lines.append(("# " + part).rstrip())

    def cmd(self, *argv):
        self.lines.append(" ".join(a if isinstance(a, _Raw) else shlex.quote(str(a)) for a in argv))

    def raw(self, line):
        if "\n" in line:
            raise OperatorRefusal("internal: command line contains a newline")
        self.lines.append(line)

    def staged_path(self, final):
        return self.stage / str(final).lstrip("/")

    def stage_bytes(self, final, data):
        dest = self.staged_path(final)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        os.chmod(str(dest), 0o600)
        return dest

    def stage_copy(self, final, src):
        """Stream src into the stage; return the sha256 of the staged bytes."""
        dest = self.staged_path(final)
        dest.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        with open(str(src), "rb") as fin, open(str(dest), "wb") as fout:
            while True:
                chunk = fin.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                fout.write(chunk)
        os.chmod(str(dest), 0o600)
        return h.hexdigest()

    def install_dir(self, path, owner, group, mode):
        self.cmd("/usr/bin/install", "-d", "-o", owner, "-g", group, "-m", mode, path)

    def install_file(self, final, data, owner, group, mode):
        staged = self.stage_bytes(final, data)
        self.install_staged(final, staged, owner, group, mode)

    def install_staged(self, final, staged, owner, group, mode):
        self.cmd("/usr/bin/install", "-o", owner, "-g", group, "-m", mode, staged, final)

    def write(self):
        self.stage.mkdir(parents=True, exist_ok=True)
        head = [
            "# zurvan-eval operator plan: %s" % self.kind,
            "# Generated by scripts/eval_seal/render.py. With --render this is a PLAN only: nothing was changed.",
            "# In real mode this exact file is executed by /bin/bash -euo pipefail as root.",
            "# Files it installs are staged below this directory, mirroring absolute paths.",
        ]
        text = "\n".join(head + self.lines) + "\n"
        out = self.stage / "commands.txt"
        out.write_text(text)
        os.chmod(str(out), 0o600)


def _fail_line(msg):
    return "echo %s >&2; exit 1" % shlex.quote("refusing: " + msg)


# ------------------------------------------------------------------ git (read only, defensive)

def _git(repo, git_user, args):
    argv = ["/usr/bin/git", "--no-optional-locks", "-c", "safe.directory=%s" % repo, "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null", "-C", str(repo)] + list(args)
    if os.geteuid() == 0 and git_user:
        # root never executes anything named by the dev-writable repo config: git runs as the dev user
        argv = ["/usr/bin/sudo", "-n", "-u", git_user, "-H"] + argv
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_TERMINAL_PROMPT": "0", "LANG": "C"}
    p = subprocess.run(argv, capture_output=True, env=env)
    return p.returncode, p.stdout, p.stderr


def check_commit(repo, sha, git_user):
    validate_sha(sha)
    rc, _o, _e = _git(repo, git_user, ["cat-file", "-e", sha + "^{commit}"])
    if rc != 0:
        raise OperatorRefusal("%s is not a commit in %s" % (sha, repo))
    rc, _o, _e = _git(repo, git_user, ["merge-base", "--is-ancestor", sha, "HEAD"])
    if rc == 1:
        raise OperatorRefusal("%s is not contained in HEAD of %s" % (sha, repo))
    if rc != 0:
        raise OperatorRefusal("could not compare %s with HEAD" % sha)


def assert_clean_tree(repo, sha, git_user):
    """The scripts root is about to run live in the working tree: they must be the reviewed bytes."""
    validate_sha(sha)
    rc, _o, _e = _git(repo, git_user, ["diff", "--no-ext-diff", "--no-textconv", "--quiet", sha, "--"] + list(EXPORT_PATHS))
    if rc == 1:
        raise OperatorRefusal("the working tree differs from %s under scripts/eval_seal" % sha)
    if rc != 0:
        raise OperatorRefusal("could not diff the working tree against %s" % sha)
    rc, out, _e = _git(repo, git_user, ["status", "--porcelain", "--untracked-files=all", "--"] + list(EXPORT_PATHS))
    if rc != 0:
        raise OperatorRefusal("could not read the working tree status")
    if any(ln.startswith(b"??") for ln in out.splitlines()):
        raise OperatorRefusal("untracked files under scripts/eval_seal")


def _blob_oid(data):
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def export_sources(repo, sha, git_user):
    """git archive <sha> over EXPORT_PATHS (the sealed code and the signed spec), verified; returns {relpath: bytes}."""
    validate_sha(sha)
    rc, out, _e = _git(repo, git_user, ["ls-tree", "-r", "-z", "--full-tree", sha, "--"] + list(EXPORT_PATHS))
    if rc != 0 or not out:
        raise OperatorRefusal("the sources are missing at %s" % sha)
    want = {}
    for entry in out.split(b"\x00"):
        if not entry:
            continue
        meta, _tab, path = entry.partition(b"\t")
        mode, typ, oid = meta.decode("ascii").split(" ")
        name = path.decode("utf-8")
        if mode == "120000":
            raise OperatorRefusal("a symlink exists in the reviewed tree: %s" % name)
        if typ != "blob" or mode not in ("100644", "100755"):
            raise OperatorRefusal("non-regular entry in the reviewed tree: %s" % name)
        want[name] = oid
    rc, tar_bytes, _e = _git(repo, git_user, ["archive", "--format=tar", sha, "--"] + list(EXPORT_PATHS))
    if rc != 0:
        raise OperatorRefusal("git archive failed for %s" % sha)
    got = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        for m in tf.getmembers():
            name = m.name
            if m.isdir():
                continue
            if m.issym() or m.islnk():
                raise OperatorRefusal("the archive contains a symlink or hardlink: %s" % name)
            if not m.isfile():
                raise OperatorRefusal("the archive contains a special file: %s" % name)
            parts = name.split("/")
            if name.startswith("/") or ".." in parts or not name.startswith(("scripts/eval_seal/", "scripts/boundary/", "eval/z1b/")):
                raise OperatorRefusal("the archive contains an unexpected path: %s" % name)
            got[name] = tf.extractfile(m).read()
    if set(got) != set(want):
        raise OperatorRefusal("the archive and the git tree disagree about which files exist")
    for name, data in got.items():
        if _blob_oid(data) != want[name]:
            raise OperatorRefusal("exported bytes differ from the git object for %s" % name)
    return got


# ------------------------------------------------------------------ wrappers, sudoers

def wrapper_source(module, entry):
    if re.fullmatch(r"[a-z_][a-z0-9_]*", module) is None or re.fullmatch(r"[a-z_][a-z0-9_]*", entry) is None:
        raise OperatorRefusal("internal: bad wrapper module or entry name")
    return (
        "#!%s -I\n"
        "import sys\n"
        'sys.path.insert(0, "%s")\n'
        "from scripts.eval_seal.%s import %s as _entry\n"
        "sys.exit(_entry(sys.argv[1:]))\n"
    ) % (CLT_PYTHON, BROKER_LIB, module, entry)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _sudoers_lines():
    defaults = [
        "Defaults>%s env_reset, !setenv, !env_keep, !env_check" % EVAL_USER,
        "Defaults>%s env_reset, !setenv, !env_keep, !env_check" % CANDIDATE_USER,
    ]
    return defaults


_RULE_RE = re.compile(
    r"(?P<who>[A-Za-z_][A-Za-z0-9_.-]{0,63}) ALL=\((?P<runas>[a-z]+)\) NOPASSWD: sha256:(?P<dig>[0-9a-f]{64}) (?P<path>/\S+)")
_INCLUDE_RE = re.compile(r"#\s*(include|includedir)\b")


def validate_sudoers(text, wrappers):
    """Structural allowlist. `wrappers` maps absolute installed path -> bytes. Returns a list of problems."""
    problems = []
    if "\\" in text:
        problems.append("backslash: line continuation is not allowed anywhere in the file")
    defaults_seen = []
    rules = []
    for ln in text.split("\n"):
        if ln == "":
            continue
        if _INCLUDE_RE.match(ln):
            problems.append("include directive not allowed: %r" % ln[:60])
            continue
        if ln.startswith("#"):
            continue
        if ln in _sudoers_lines():
            defaults_seen.append(ln)
            continue
        m = _RULE_RE.fullmatch(ln)
        if m is None:
            problems.append("unexpected rule line: %r" % ln[:80])
            continue
        rules.append(m)
    if sorted(defaults_seen) != sorted(_sudoers_lines()):
        problems.append("missing or duplicated Defaults line: the rule set must reset and empty the environment")
    if len(rules) != 2:
        problems.append("rule count: expected exactly 2 rules, found %d" % len(rules))
        return problems
    want = (
        (EVAL_USER, BROKER_WRAPPER, True),
        (CANDIDATE_USER, CANDIDATE_WRAPPER, False),
    )
    for m, (runas, path, dev_rule) in zip(rules, want):
        who = m.group("who")
        if dev_rule and who in RESERVED_USERS:
            problems.append("rule: invalid invoking user %r" % who)
        if not dev_rule and who != EVAL_USER:
            problems.append("rule: the second rule must be invoked by %s" % EVAL_USER)
        if m.group("runas") != runas:
            problems.append("rule: run-as must be exactly (%s), found (%s)" % (runas, m.group("runas")))
        if m.group("path") != path:
            problems.append("rule: path must be exactly %s" % path)
            continue
        if path not in wrappers:
            problems.append("rule: no wrapper bytes supplied for %s" % path)
        elif m.group("dig") != _sha256(wrappers[path]):
            problems.append("digest mismatch for %s" % path)
    return problems


def render_sudoers(dev_user, wrappers):
    validate_user(dev_user)
    for need in (BROKER_WRAPPER, CANDIDATE_WRAPPER):
        if need not in wrappers:
            raise OperatorRefusal("internal: missing wrapper %s" % need)
    tmpl = Template((TEMPLATES / "sudoers.tmpl").read_text())
    text = tmpl.substitute(
        DEV_USER=dev_user, EVAL_USER=EVAL_USER, CANDIDATE_USER=CANDIDATE_USER,
        BROKER_PATH=BROKER_WRAPPER, BROKER_DIGEST=_sha256(wrappers[BROKER_WRAPPER]),
        CANDIDATE_RUN_PATH=CANDIDATE_WRAPPER, CANDIDATE_RUN_DIGEST=_sha256(wrappers[CANDIDATE_WRAPPER]))
    problems = validate_sudoers(text, wrappers)
    if problems:
        raise OperatorRefusal("rendered sudoers failed its own validation: %s" % "; ".join(problems))
    return text


def _render_template(name, **kw):
    return Template((TEMPLATES / name).read_text()).substitute(**kw)


# ------------------------------------------------------------------ plans

def _acl(perms):
    return "user:%s allow %s" % (EVAL_USER, perms)


def _acl_targets(dev_home, repo):
    """(search-only chain, full-read targets). Targets are the scan set of spec s5.6."""
    full = [
        dev_home + "/.claude/projects/*Zurvan*",
        repo + "/data/traces",
        repo + "/wiki/traces",
        repo + "/logs",
        dev_home + "/.zurvan/reports",
    ]
    search = []

    def add_chain(path):
        # every ancestor from dev_home down to the parent of path
        base = dev_home.rstrip("/")
        if not (path == base or path.startswith(base + "/")):
            return
        cur = base
        rel = path[len(base):].strip("/")
        chain = [cur]
        for part in [p for p in rel.split("/") if p][:-1]:
            if "*" in part:
                break
            cur = cur + "/" + part
            chain.append(cur)
        for c in chain:
            if c not in search:
                search.append(c)

    for t in full:
        add_chain(t)
    # the repo and its data/wiki parents sit under dev_home normally; if not, they still need search
    for extra in (repo, repo + "/data", repo + "/wiki"):
        if extra not in search:
            search.append(extra)
    # keep ~/.zurvan reachable (parent of reports)
    return search, full


def _quote_target(path):
    """Quote a path but keep every '*' live for the shell (the scan glob)."""
    if "*" not in path:
        return path
    return _Raw("*".join(shlex.quote(seg) if seg else "" for seg in path.split("*")))


def plan_setup(plan, dev_home, repo):
    search, full = _acl_targets(dev_home, repo)
    plan.comment("setup: two non-admin identities, one shared corpus group, sealed directories, backup exclusion,")
    plan.comment("candidate network block, cron/at denial, read-only scan ACLs. Claude Code login is NOT done here (operator step).")
    plan.comment("Both identities get shell /usr/bin/false: sudo and launchd run commands directly, no login shell is ever needed.")
    plan.comment("preflight: the ANCESTOR paths the eval identity must traverse have to exist. The read-ACL")
    plan.comment("TARGETS themselves (logs, reports, the *Zurvan* transcript dirs) can legitimately be absent")
    plan.comment("on a fresh machine -- they are granted below only if present (refresh_acl re-grants ones")
    plan.comment("created later, and the scan fails closed if it ever cannot read a target), so requiring them")
    plan.comment("here would abort the whole -euo pipefail install for a path that simply does not exist yet.")
    plan.cmd("/bin/test", "-d", "/Users/Shared")
    for p in search:
        plan.cmd("/bin/test", "-d", p)
    plan.comment("refuse to clobber existing identities")
    for kind, name in (("Users", EVAL_USER), ("Users", CANDIDATE_USER), ("Users", AUTHOR_USER),
                       ("Groups", EVAL_USER), ("Groups", CANDIDATE_USER), ("Groups", AUTHOR_USER),
                       ("Groups", CORPUS_GROUP)):
        plan.raw('if /usr/bin/dscl . -read /%s/%s >/dev/null 2>&1; then %s; fi' % (kind, name, _fail_line("%s %s already exists" % (kind[:-1].lower(), name))))
    plan.comment("ids 600-699: next free block of four (eval user, candidate user, author user, corpus group)")
    plan.raw("ZV_BASE=$({ /usr/bin/dscl . -list /Users UniqueID; /usr/bin/dscl . -list /Groups PrimaryGroupID; } | "
             "/usr/bin/awk 'BEGIN { m = 599 } $2 + 0 >= 600 && $2 + 0 < 700 && $2 + 0 > m { m = $2 + 0 } END { print m + 1 }')")
    plan.raw('[ "$ZV_BASE" -lt 696 ] || { echo "refusing: no free id block in 600-699" >&2; exit 1; }')
    plan.raw("ZV_EVAL_ID=$ZV_BASE")
    plan.raw("ZV_CAND_ID=$((ZV_BASE + 1))")
    plan.raw("ZV_AUTHOR_ID=$((ZV_BASE + 2))")
    plan.raw("ZV_CORPUS_GID=$((ZV_BASE + 3))")
    dscl = "/usr/bin/dscl"
    for grp, var in ((EVAL_USER, "$ZV_EVAL_ID"), (CANDIDATE_USER, "$ZV_CAND_ID"), (AUTHOR_USER, "$ZV_AUTHOR_ID"), (CORPUS_GROUP, "$ZV_CORPUS_GID")):
        plan.cmd(dscl, ".", "-create", "/Groups/" + grp)
        plan.cmd(dscl, ".", "-create", "/Groups/" + grp, "PrimaryGroupID", _Raw('"%s"' % var))
    for user, var, real in ((EVAL_USER, "$ZV_EVAL_ID", "Zurvan sealed evaluator"),
                            (CANDIDATE_USER, "$ZV_CAND_ID", "Zurvan sealed candidate runner"),
                            (AUTHOR_USER, "$ZV_AUTHOR_ID", "Zurvan sealed author")):
        u = "/Users/" + user
        plan.cmd(dscl, ".", "-create", u)
        plan.cmd(dscl, ".", "-create", u, "UserShell", "/usr/bin/false")
        plan.cmd(dscl, ".", "-create", u, "RealName", real)
        plan.cmd(dscl, ".", "-create", u, "UniqueID", _Raw('"%s"' % var))
        plan.cmd(dscl, ".", "-create", u, "PrimaryGroupID", _Raw('"%s"' % var))
        plan.cmd(dscl, ".", "-create", u, "NFSHomeDirectory", u)
        plan.cmd(dscl, ".", "-create", u, "IsHidden", "1")
        plan.cmd(dscl, ".", "-create", u, "Password", "*")
    plan.cmd(dscl, ".", "-append", "/Groups/" + CORPUS_GROUP, "GroupMembership", EVAL_USER, CANDIDATE_USER, AUTHOR_USER)
    plan.comment("directories: owner, group and mode are set in one step by install -d")
    ev = (EVAL_USER, EVAL_USER, "0700")
    cd = (CANDIDATE_USER, CANDIDATE_USER, "0700")
    au = (AUTHOR_USER, AUTHOR_USER, "0700")
    table = (
        (ZE_ROOT, ("root", "wheel", "0755")),
        (RUNTIME_DIR, ("root", "wheel", "0755")),
        (CORPUS_DIR, ("root", CORPUS_GROUP, "0750")),
        (STAGE_DIR, (EVAL_USER, CANDIDATE_USER, "0750")),
        (EVAL_HOME, ev), (KEYS_DIR, ev), (PRIVATE_DIR, ev), (SETS_DIR, ev), (RUNS_DIR, ev), (INCIDENTS_DIR, ev),
        (CANDIDATE_HOME, cd), (SCRATCH_DIR, cd),
        (AUTHOR_HOME, au), (AUTHOR_WS, au), (AUTHOR_PRIVATE, au),
        (DROP_DIR, ("root", "wheel", "1733")),
    )
    for path, (o, g, m) in table:
        plan.install_dir(path, o, g, m)
    plan.cmd("/bin/chmod", "1733", DROP_DIR)
    plan.comment("T7: keys never reach a Time Machine backup (seal-check proves it with tmutil isexcluded)")
    plan.cmd("/usr/bin/tmutil", "addexclusion", "-p", KEYS_DIR)
    plan.comment("R18: private/ holds the plaintext authoring audit files and the claude session state: never backed up either")
    plan.cmd("/usr/bin/tmutil", "addexclusion", "-p", PRIVATE_DIR)
    plan.comment("Rev 3 #2: the author workspace and the author's claude config hold plaintext items: TM-excluded,")
    plan.comment("and the eval identity gets a READ ACL on the workspace so the seal phase can read the draft bundle.")
    plan.cmd("/usr/bin/tmutil", "addexclusion", "-p", AUTHOR_WS)
    plan.cmd("/usr/bin/tmutil", "addexclusion", "-p", AUTHOR_PRIVATE)
    plan.cmd("/bin/chmod", "+a", _acl("read,list,search,readattr,file_inherit,directory_inherit"), AUTHOR_WS)
    plan.comment("T3: the candidate identity has no outbound network (pf matches user for TCP/UDP only: defence in depth)")
    plan.install_file(PF_ANCHOR_FILE, _render_template("pf.anchor.tmpl", CANDIDATE_USER=CANDIDATE_USER).encode(),
                      "root", "wheel", "0644")
    plan.cmd("/bin/cp", "-p", PF_CONF, PF_CONF_BAK)
    plan.raw("/usr/bin/grep -qxF 'anchor \"zurvan-eval\"' %s || /usr/bin/printf '%%s\\n' 'anchor \"zurvan-eval\"' "
             "'load anchor \"zurvan-eval\" from \"%s\"' >> %s" % (PF_CONF, PF_ANCHOR_FILE, PF_CONF))
    plan.raw("/sbin/pfctl -n -f %s || { /bin/cp -p %s %s; echo 'refusing: pf.conf does not parse' >&2; exit 1; }"
             % (PF_CONF, PF_CONF_BAK, PF_CONF))
    plan.cmd("/sbin/pfctl", "-f", PF_CONF)
    plan.cmd("/sbin/pfctl", "-E")
    plan.install_file(PF_PLIST, _render_template("pf.launchd.plist.tmpl").encode(), "root", "wheel", "0644")
    plan.cmd("/usr/bin/plutil", "-lint", plan.staged_path(PF_PLIST))
    plan.cmd("/bin/launchctl", "bootstrap", "system", PF_PLIST)
    plan.comment("the candidate may not schedule anything")
    for f in CRON_DENY_FILES:
        plan.raw("/usr/bin/grep -qx %s %s 2>/dev/null || /usr/bin/printf '\\n%%s\\n' %s >> %s"
                 % (CANDIDATE_USER, f, CANDIDATE_USER, f))
    plan.comment("T8: read-only ACLs so the eval identity can scan development transcripts and traces (spec s5.6)")
    plan.comment("search = traverse only (ancestors); full = read, list, search, readattr + inheritance. Never write/delete.")
    # Apply each ACL only if the target exists. A dev-side path can legitimately be absent on a fresh
    # machine (e.g. the repo has no logs/ yet, or no transcript dir matches *Zurvan*); chmod on a missing
    # target would fail and -- because every operator plan runs under `bash -euo pipefail` -- abort the whole
    # install. A missing target simply gets no ACL now (refresh_acl re-grants late-created transcript dirs,
    # and the scan fails closed if it later cannot read a target). A chmod that FAILS on an EXISTING path
    # still aborts, so real errors are never swallowed.
    search_acl = shlex.quote(_acl("search"))
    full_acl = shlex.quote(_acl("read,list,search,readattr,file_inherit,directory_inherit"))
    for p in search:
        plan.raw("if [ -e %s ]; then /bin/chmod +a %s %s; fi" % (shlex.quote(p), search_acl, shlex.quote(p)))
    for p in full:
        if "*" in p:
            plan.raw('for d in %s; do if [ -e "$d" ]; then /bin/chmod -R +a %s "$d"; fi; done'
                     % (_quote_target(p), full_acl))
        else:
            plan.raw("if [ -e %s ]; then /bin/chmod -R +a %s %s; fi" % (shlex.quote(p), full_acl, shlex.quote(p)))
    plan.cmd("/usr/bin/id", EVAL_USER)
    plan.cmd("/usr/bin/id", CANDIDATE_USER)
    plan.cmd("/usr/bin/id", AUTHOR_USER)


def _read_regular(path, what):
    real = os.path.realpath(str(path))
    if not os.path.isfile(real):
        raise OperatorRefusal("%s is not a regular file: %s" % (what, path))
    return real


def _resolve_tool(explicit, name, search_path):
    if explicit:
        cand = str(explicit)
    else:
        cand = shutil.which(name, path=search_path or "/usr/bin:/bin")
        if not cand:
            raise OperatorRefusal("%s not found on the operator PATH; pass --%s" % (name, name))
    real = _read_regular(cand, name)
    if not os.access(real, os.X_OK):
        raise OperatorRefusal("%s is not executable: %s" % (name, real))
    return real


def _hf_files(hf_dir):
    root = os.path.realpath(str(hf_dir))
    name = os.path.basename(os.path.abspath(str(hf_dir)))
    if not os.path.isdir(root) or HF_MODEL_RE.fullmatch(name) is None:
        raise OperatorRefusal("--hf-dir must be a Hugging Face cache model directory (models--org--name)")
    out = {}
    for sub in ("refs", "snapshots"):
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            raise OperatorRefusal("the model directory has no %s/" % sub)
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for dn in list(dirnames):
                if os.path.islink(os.path.join(dirpath, dn)):
                    raise OperatorRefusal("a symlinked directory in the model cache: %s" % dn)
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                real = os.path.realpath(p)
                if not real.startswith(root + os.sep) or not os.path.isfile(real):
                    raise OperatorRefusal("model file escapes the model directory: %s" % p)
                out[os.path.relpath(p, root)] = real
    if not any(k.startswith("snapshots/") for k in out):
        raise OperatorRefusal("the model directory has no snapshot files")
    return name, out


def _ancestors(rel):
    parts = rel.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def allowed_signers_derive_sh(pub_path: str, out_path: str) -> str:
    """Inner POSIX shell (no privilege change) that (re)derives the manifest-verify trust anchor from an
    ed25519 signing pubkey, as the EXACT line `manifest.verify` expects:
    `<principal> namespaces="<ns>" <keytype> <keydata>`. Atomic (temp + rename), idempotent and
    self-healing: it is rebuilt from the CURRENT pub on every install, so a missing, stale or externally
    supplied `allowed_signers` can never be trusted — the anchor always binds the key just generated, with
    the fixed principal/namespace the verifier pins. `\\042` is printf's octal for `"`, so no literal quote
    has to survive three shell quoting levels."""
    tmp = out_path + ".new"
    return ("umask 077; k=\"$(/usr/bin/cut -d' ' -f1,2 %s)\" && "
            "/usr/bin/printf '%%s namespaces=\\042%%s\\042 %%s\\n' '%s' '%s' \"$k\" > %s && "
            "/bin/mv -f %s %s") % (pub_path, MANIFEST_PRINCIPAL, ALLOWED_NAMESPACES, tmp, tmp, out_path)


def allowed_signers_cmd(keys_dir: str, eval_user: str) -> str:
    """The install step: derive `allowed_signers` AS the eval identity (the keys dir is eval-owned 0700, and
    the broker that reads the anchor also runs as the eval identity — signer and verifier are one trusted
    identity, so the anchor lives beside the key)."""
    inner = allowed_signers_derive_sh(keys_dir + "/signing_ed25519.pub", keys_dir + "/allowed_signers")
    return "/usr/bin/sudo -u %s -H /bin/sh -c %s" % (eval_user, shlex.quote(inner))


def plan_install(plan, repo, sha, dev_user, dev_home, search_path, age, age_keygen, claude, hf_dir, real):
    validate_sha(sha)
    validate_user(dev_user)
    git_user = dev_user if os.geteuid() == 0 else None
    check_commit(repo, sha, git_user)
    if real:
        assert_clean_tree(repo, sha, git_user)
    files = export_sources(repo, sha, git_user)
    if "eval/z1b/spec.json" not in files:
        raise OperatorRefusal("eval/z1b/spec.json is missing at %s: the installed broker would have no spec" % sha)
    for _name, mod, _entry in WRAPPERS:
        if "scripts/eval_seal/%s.py" % mod not in files:
            raise OperatorRefusal("scripts/eval_seal/%s.py is missing at %s: nothing to install for that wrapper" % (mod, sha))
    rc, tree_out, _e = _git(repo, git_user, ["rev-parse", "%s:scripts/eval_seal" % sha])
    tree = tree_out.decode("ascii").strip()
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise OperatorRefusal("could not resolve the scripts/eval_seal tree id")
    tools = {
        "age": _resolve_tool(age, "age", search_path),
        "age-keygen": _resolve_tool(age_keygen, "age-keygen", search_path),
        "claude": _resolve_tool(claude, "claude", search_path),
    }
    hf_name, hf_map = _hf_files(hf_dir if hf_dir else (Path(dev_home) / ".cache/huggingface/hub" / DEFAULT_HF_MODEL))

    plan.comment("install: broker sources from reviewed commit %s (scripts/eval_seal tree %s)" % (sha, tree))
    plan.comment("Everything the sealed side executes ends up root:wheel, 0555/0444. Installed in place; any failure leaves the")
    plan.comment("sudoers digest stale, so sudo refuses the broker (fail closed). sudoers is installed last.")
    plan.comment("pinned binaries are copied, then hashed; the hash is of the bytes that get installed:")
    plan.cmd("/bin/test", "-d", ZE_ROOT)
    plan.cmd("/bin/test", "-d", KEYS_DIR)
    plan.cmd("/bin/test", "-x", CLT_PYTHON)
    plan.raw('[ "$(/usr/bin/stat -f %%Su %s)" = root ] || { echo "refusing: the CLT python is not root-owned" >&2; exit 1; }'
             % shlex.quote(CLT_PYTHON))
    plan.cmd("/bin/rm", "-rf", BROKER_DIR, RUNTIME_DIR + "/hf")

    plan.install_dir(BROKER_DIR, "root", "wheel", "0555")
    plan.install_dir(BROKER_BIN, "root", "wheel", "0555")
    plan.install_dir(BROKER_DIR + "/lib", "root", "wheel", "0555")
    dirs = set()
    for rel in files:
        for a in _ancestors(rel):
            dirs.add(a)
    for d in sorted(dirs):
        plan.install_dir(BROKER_DIR + "/lib/" + d, "root", "wheel", "0555")
    for rel in sorted(files):
        plan.install_file(BROKER_DIR + "/lib/" + rel, files[rel], "root", "wheel", "0444")

    wrappers = {}
    for name, mod, entry in WRAPPERS:
        data = wrapper_source(mod, entry).encode()
        wrappers[BROKER_BIN + "/" + name] = data
        plan.install_file(BROKER_BIN + "/" + name, data, "root", "wheel", "0555")

    pinned = []
    for name in ("age", "age-keygen", "claude"):
        final = BROKER_BIN + "/" + name
        digest = plan.stage_copy(final, tools[name])
        plan.comment("pinned %s from %s sha256=%s" % (name, tools[name], digest))
        plan.install_staged(final, plan.staged_path(final), "root", "wheel", "0555")
        pinned.append("%s  %s" % (digest, final))
    plan.install_file(BROKER_DIR + "/PINNED.sha256", ("\n".join(pinned) + "\n").encode(), "root", "wheel", "0444")
    plan.install_file(str(_P.installed_sha_file), (sha + "\n").encode(), "root", "wheel", "0444")
    plan.install_file(BROKER_DIR + "/INSTALLED_TREE", (tree + "\n").encode(), "root", "wheel", "0444")
    plan.install_file(BROKER_DIR + "/SCAN_TARGETS.json",
                      (json.dumps({"dev_home": dev_home, "repo": repo}, sort_keys=True) + "\n").encode(),
                      "root", "wheel", "0444")
    plan.install_file(BROKER_DIR + "/empty-mcp.json", (TEMPLATES / "empty-mcp.json").read_bytes(), "root", "wheel", "0444")

    hf_base = RUNTIME_DIR + "/hf/hub/" + hf_name
    for d in (RUNTIME_DIR + "/hf", RUNTIME_DIR + "/hf/hub", hf_base):
        plan.install_dir(d, "root", "wheel", "0555")
    hf_dirs = set()
    for rel in hf_map:
        for a in _ancestors(rel):
            hf_dirs.add(a)
    for d in sorted(hf_dirs):
        plan.install_dir(hf_base + "/" + d, "root", "wheel", "0555")
    for rel in sorted(hf_map):
        final = hf_base + "/" + rel
        plan.stage_copy(final, hf_map[rel])
        plan.install_staged(final, plan.staged_path(final), "root", "wheel", "0444")

    plan.comment("prove what landed: pinned digests, no writable or foreign-owned file anywhere under the trusted trees")
    plan.cmd("/usr/bin/shasum", "-a", "256", "-c", BROKER_DIR + "/PINNED.sha256")
    plan.raw('if [ -n "$(/usr/bin/find %s %s/hf \\( -perm -0020 -o -perm -0002 -o ! -user root -o ! -group wheel \\) -print)" ]; '
             'then echo "refusing: writable or foreign-owned file under the broker tree" >&2; exit 1; fi' % (BROKER_DIR, RUNTIME_DIR))
    for path, data in sorted(wrappers.items()):
        if path in (BROKER_WRAPPER, CANDIDATE_WRAPPER):
            plan.raw('[ "$(/usr/bin/shasum -a 256 %s | /usr/bin/awk \'{print $1}\')" = %s ] || '
                     '{ echo "refusing: installed wrapper digest mismatch" >&2; exit 1; }' % (path, _sha256(data)))

    plan.comment("keys: made AS the eval identity with the PINNED age binary, only when absent (a reinstall never rotates them)")
    plan.raw("if [ ! -e %s/age.key ]; then /usr/bin/sudo -u %s -H %s/age-keygen -o %s/age.key; fi"
             % (KEYS_DIR, EVAL_USER, BROKER_BIN, KEYS_DIR))
    plan.raw("if [ ! -e %s/signing_ed25519 ]; then /usr/bin/sudo -u %s -H /usr/bin/ssh-keygen -q -t ed25519 -N '' "
             "-C zurvan-eval-signing -f %s/signing_ed25519; fi" % (KEYS_DIR, EVAL_USER, KEYS_DIR))
    plan.comment("the manifest-verify trust anchor is DERIVED from the just-generated signing key on EVERY "
                 "install (idempotent, self-healing): a missing, stale or externally supplied allowed_signers "
                 "can never be trusted, and it always binds the current key with the pinned principal+namespace")
    plan.raw(allowed_signers_cmd(KEYS_DIR, EVAL_USER))

    sudoers = render_sudoers(dev_user, wrappers)
    staged = plan.stage_bytes(SUDOERS_FILE, sudoers.encode())
    plan.cmd("/usr/sbin/visudo", "-c", "-f", staged)
    plan.cmd("/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "0440", staged, SUDOERS_FILE)
    plan.raw("/usr/sbin/visudo -c -f %s || { /bin/rm -f %s; echo 'refusing: installed sudoers does not parse' >&2; exit 1; }"
             % (SUDOERS_FILE, SUDOERS_FILE))
    plan.raw("/usr/sbin/visudo -c || { /bin/rm -f %s; echo 'refusing: sudoers configuration does not parse' >&2; exit 1; }" % SUDOERS_FILE)

    plist = _render_template("scan.launchd.plist.tmpl", EVAL_USER=EVAL_USER, BROKER_PATH=BROKER_WRAPPER)
    staged_plist = plan.stage_bytes(SCAN_PLIST, plist.encode())
    plan.cmd("/usr/bin/plutil", "-lint", staged_plist)
    plan.install_staged(SCAN_PLIST, staged_plist, "root", "wheel", "0644")
    plan.raw("/bin/launchctl bootout system/%s 2>/dev/null || true" % SCAN_LABEL)
    plan.cmd("/bin/launchctl", "bootstrap", "system", SCAN_PLIST)
    plan.cmd("/bin/echo", "installed broker from %s" % sha)


def _walk_export(export_dir):
    root = os.path.abspath(str(export_dir))
    if not os.path.isdir(root) or os.path.islink(root):
        raise OperatorRefusal("the export directory does not exist or is a symlink: %s" % export_dir)
    dirs, files = [], []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        for dn in sorted(dirnames):
            full = os.path.join(dirpath, dn)
            if os.path.islink(full):
                raise OperatorRefusal("symlink in the corpus export: %s" % full)
            rel = os.path.relpath(full, root)
            _check_name(rel)
            dirs.append(rel)
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            st = os.lstat(full)
            import stat as _stat
            if _stat.S_ISLNK(st.st_mode):
                raise OperatorRefusal("symlink in the corpus export: %s" % full)
            if not _stat.S_ISREG(st.st_mode):
                raise OperatorRefusal("special file in the corpus export: %s" % full)
            if st.st_nlink != 1:
                raise OperatorRefusal("hardlink in the corpus export (links=%d): %s" % (st.st_nlink, full))
            rel = os.path.relpath(full, root)
            _check_name(rel)
            files.append(rel)
    if not files:
        raise OperatorRefusal("the export directory is empty")
    return root, sorted(dirs), sorted(files)


def _check_name(rel):
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in rel):
        raise OperatorRefusal("control character in a corpus file name")


def plan_install_corpus(plan, export_dir):
    root, dirs, files = _walk_export(export_dir)
    plan.comment("install_corpus: copy (install(1) writes new inodes, never links) the export into the pinned corpus directory")
    plan.comment("the digest printed at the end is computed from the INSTALLED bytes with the installed code")
    plan.cmd("/bin/test", "-f", BROKER_DIR + "/lib/scripts/eval_seal/corpus.py")
    plan.raw("/usr/bin/dscl . -read /Groups/%s >/dev/null" % CORPUS_GROUP)
    plan.raw('if [ -n "$(/bin/ls -A %s)" ]; then echo "refusing: corpus directory is not empty (remove it deliberately first)" >&2; exit 1; fi'
             % CORPUS_DIR)
    for d in dirs:
        plan.install_dir(CORPUS_DIR + "/" + d, "root", CORPUS_GROUP, "0750")
    for f in files:
        plan.cmd("/usr/bin/install", "-o", "root", "-g", CORPUS_GROUP, "-m", "0440", os.path.join(root, f), CORPUS_DIR + "/" + f)
    plan.raw('if [ -n "$(/usr/bin/find %s \\( -type f -links +1 -o -type l \\) -print)" ]; '
             'then echo "refusing: hardlink or symlink in the installed corpus" >&2; exit 1; fi' % CORPUS_DIR)
    code = ('import sys; sys.path.insert(0, "%s"); from pathlib import Path; '
            'from scripts.eval_seal.corpus import corpus_sha256; '
            'print("corpus_sha256 " + corpus_sha256(Path("%s")))') % (BROKER_LIB, CORPUS_DIR)
    plan.cmd(CLT_PYTHON, "-I", "-c", code)


def plan_refresh_acl(plan, dev_home):
    """R15: re-grant the read-only scan ACL on every CURRENT Claude Code transcript directory for this repo."""
    glob = dev_home + "/.claude/projects/*Zurvan*"
    perms = _acl("read,list,search,readattr,file_inherit,directory_inherit")
    plan.comment("refresh_acl: transcript directories created after setup (new worktrees) are unreadable to the eval identity")
    plan.comment("until this runs, and the scan then reports SCAN_INCOMPLETE (fail closed). Read-only grant, same ACL as setup.")
    plan.cmd("/bin/test", "-d", dev_home + "/.claude/projects")
    plan.raw("/bin/ls -d %s >/dev/null" % _quote_target(glob))
    plan.comment("remove an identical entry first so running this twice never stacks duplicates, then grant")
    plan.raw("/bin/chmod -R -a %s %s || true" % (shlex.quote(perms), _quote_target(glob)))
    plan.raw("/bin/chmod -R +a %s %s" % (shlex.quote(perms), _quote_target(glob)))


def plan_tamper_drill(plan, dev_user):
    validate_user(dev_user)
    b = BROKER_WRAPPER
    bak = b + ".bak"
    plan.comment("tamper drill (gate 1b oracle): a wrapper changed by ONE byte must make sudo refuse it; restored afterwards.")
    plan.comment("Run only after install.sh. Any failure leaves the wrapper restored by the EXIT trap.")
    plan.cmd("/bin/test", "-f", b)
    plan.cmd("/bin/cp", "-p", b, bak)
    plan.raw("trap 'if [ -e %s ]; then /bin/mv -f %s %s; fi' EXIT" % (bak, bak, b))
    plan.raw("/usr/bin/printf '#' >> %s" % b)
    plan.raw("DRILL_OUT=$(/usr/bin/sudo -u %s /usr/bin/sudo -n -u %s %s status 2>&1) && DRILL_RC=0 || DRILL_RC=$?"
             % (dev_user, EVAL_USER, b))
    plan.cmd("/bin/mv", "-f", bak, b)
    plan.raw("DRILL_NOW=$(/usr/bin/shasum -a 256 %s | /usr/bin/awk '{print $1}')" % b)
    plan.raw("DRILL_PIN=$(/usr/bin/awk -v b=%s 'index($0, b) && /NOPASSWD: sha256:/ { sub(/.*sha256:/, \"\"); print $1 }' %s)"
             % (b, SUDOERS_FILE))
    plan.raw("POS_OUT=$(/usr/bin/sudo -u %s /usr/bin/sudo -n -u %s %s status 2>&1) && POS_RC=0 || POS_RC=$?"
             % (dev_user, EVAL_USER, b))
    plan.raw('if [ "$DRILL_RC" -ne 0 ] && [[ "$DRILL_OUT" != \\{* ]] && [ "$DRILL_NOW" = "$DRILL_PIN" ] && '
             '{ [ "$POS_RC" -eq 0 ] || [ "$POS_RC" -eq 2 ]; } && [[ "$POS_OUT" == \\{* ]]; '
             'then echo "DRILL PASS"; else echo "DRILL FAIL" >&2; exit 1; fi')


def plan_uninstall(plan, dev_home, repo, purge):
    search, full = _acl_targets(dev_home, repo)
    plan.comment("uninstall: authority first (sudoers), then network rules, ACLs, broker, runtime, corpus.")
    if purge:
        plan.comment("--purge-sealed: ALSO deletes the identities, keys, sealed sets and ledger. IRREVERSIBLE: the suite must be re-authored.")
    else:
        plan.comment("sealed state (keys, sets, runs, incidents, the identities) is KEPT. Use --purge-sealed to destroy it.")
    plan.cmd("/bin/rm", "-f", SUDOERS_FILE)
    plan.raw("/bin/launchctl bootout system/%s 2>/dev/null || true" % SCAN_LABEL)
    plan.cmd("/bin/rm", "-f", SCAN_PLIST)
    plan.raw("/bin/launchctl bootout system/%s 2>/dev/null || true" % PF_LABEL)
    plan.cmd("/bin/rm", "-f", PF_PLIST)
    plan.raw("/usr/bin/sed -i '' '/zurvan-eval/d' %s" % PF_CONF)
    plan.cmd("/sbin/pfctl", "-f", PF_CONF)
    plan.cmd("/bin/rm", "-f", PF_ANCHOR_FILE, PF_CONF_BAK)
    for f in CRON_DENY_FILES:
        plan.raw("/usr/bin/sed -i '' '/^%s$/d' %s" % (CANDIDATE_USER, f))
    for p in full:
        plan.raw("/bin/chmod -R -a %s %s || true" % (shlex.quote(_acl("read,list,search,readattr,file_inherit,directory_inherit")),
                                                       _quote_target(p)))
    for p in reversed(search):
        plan.raw("/bin/chmod -a %s %s || true" % (shlex.quote(_acl("search")), shlex.quote(p)))
    plan.cmd("/bin/rm", "-rf", BROKER_DIR)
    plan.cmd("/bin/rm", "-rf", RUNTIME_DIR)
    plan.cmd("/bin/rm", "-rf", CORPUS_DIR)
    plan.cmd("/bin/rm", "-rf", STAGE_DIR)
    plan.cmd("/bin/rmdir", ZE_ROOT)
    plan.raw("/bin/rmdir %s || true" % DROP_DIR)
    if purge:
        plan.cmd("/usr/bin/tmutil", "removeexclusion", "-p", KEYS_DIR)
        plan.cmd("/usr/bin/tmutil", "removeexclusion", "-p", PRIVATE_DIR)
        plan.cmd("/usr/bin/tmutil", "removeexclusion", "-p", AUTHOR_WS)
        plan.cmd("/usr/bin/tmutil", "removeexclusion", "-p", AUTHOR_PRIVATE)
        plan.cmd("/bin/rm", "-rf", EVAL_HOME)
        plan.cmd("/bin/rm", "-rf", CANDIDATE_HOME)
        plan.cmd("/bin/rm", "-rf", AUTHOR_HOME)
        for user in (EVAL_USER, CANDIDATE_USER, AUTHOR_USER):
            plan.cmd("/usr/bin/dscl", ".", "-delete", "/Users/" + user)
        for grp in (EVAL_USER, CANDIDATE_USER, AUTHOR_USER, CORPUS_GROUP):
            plan.cmd("/usr/bin/dscl", ".", "-delete", "/Groups/" + grp)


# ------------------------------------------------------------------ cli

def build_parser():
    ap = argparse.ArgumentParser(prog="render.py", description="Render the operator plan for one script.")
    sub = ap.add_subparsers(dest="kind", required=True)

    def common(p):
        p.add_argument("--stage", required=True)
        p.add_argument("--repo", default=None)
        p.add_argument("--dev-user", default=None)
        p.add_argument("--dev-home", default=None)
        p.add_argument("--real", action="store_true")
        p.add_argument("--search-path", default=None)

    for k in ("setup", "tamper-drill", "refresh-acl"):
        common(sub.add_parser(k))
    p = sub.add_parser("uninstall")
    common(p)
    p.add_argument("--purge-sealed", action="store_true")
    p = sub.add_parser("install")
    common(p)
    p.add_argument("--sha", required=True)
    for opt in ("age", "age-keygen", "claude", "hf-dir"):
        p.add_argument("--" + opt, default=None)
    p = sub.add_parser("install-corpus")
    common(p)
    p.add_argument("--export-dir", required=True)
    return ap


def render_main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        stage = check_stage(args.stage)
        dev_user = validate_user(args.dev_user) if args.dev_user else "devuser"
        dev_home = os.path.abspath(args.dev_home) if args.dev_home else "/Users/" + dev_user
        repo = os.path.abspath(args.repo) if args.repo else os.getcwd()
        plan = Plan(args.kind, stage)
        if args.kind == "setup":
            plan_setup(plan, dev_home, repo)
        elif args.kind == "install":
            plan_install(plan, repo, args.sha, dev_user, dev_home, args.search_path, args.age, args.age_keygen,
                         args.claude, args.hf_dir, args.real)
        elif args.kind == "install-corpus":
            plan_install_corpus(plan, args.export_dir)
        elif args.kind == "tamper-drill":
            plan_tamper_drill(plan, dev_user)
        elif args.kind == "refresh-acl":
            plan_refresh_acl(plan, dev_home)
        elif args.kind == "uninstall":
            plan_uninstall(plan, dev_home, repo, args.purge_sealed)
        plan.write()
    except OperatorRefusal as exc:
        sys.stderr.write("refusing: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(render_main())
