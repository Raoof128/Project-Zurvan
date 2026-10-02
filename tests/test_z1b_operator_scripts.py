"""Z1b Task 12 tests: operator scripts, exercised ONLY through --render into tmp_path (contract s5).

Nothing here runs a script in real mode except the refusal tests, which run as a non-root user and
must create nothing. No test uses sudo, dscl, tmutil, pfctl or writes outside tmp_path.
"""
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEAL = ROOT / "scripts" / "eval_seal"
CLT_PY = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"
SCRIPTS = ["setup.sh", "install.sh", "install_corpus.sh", "tamper_drill.sh", "uninstall.sh"]
DEV = "dev"
DEV_HOME = "/Users/dev"
REPO_PATH = "/Users/dev/Desktop/Raouf/Zurvan"
SHA_A = "a" * 40

needs_clt = pytest.mark.skipif(not Path(CLT_PY).exists(), reason="CLT python 3.9 absent")

sys.path.insert(0, str(ROOT))
from scripts.eval_seal import render as R  # noqa: E402
from scripts.eval_seal.paths import SealPaths  # noqa: E402


# ------------------------------------------------------------------ helpers

def _t12_env():
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(ROOT), "LANG": "C"}


def _t12_sh(script, *args, env=None, cwd=None):
    return subprocess.run(
        ["/bin/bash", str(SEAL / script), *map(str, args)],
        capture_output=True, text=True, env=env or _t12_env(), cwd=cwd, timeout=120,
    )


def _t12_git(repo, *args):
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo)}
    out = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, env=env, check=True,
    )
    return out.stdout.strip()


_T12_FILES = {
    "scripts/eval_seal/__init__.py": "",
    "scripts/eval_seal/paths.py": "X = 1\n",
    "scripts/eval_seal/broker.py": "def broker_main(argv=None):\n    return 0\n",
    "scripts/eval_seal/candidate_run.py": "def main(argv):\n    return 0\n",
    "scripts/eval_seal/author.py": "def author_main(argv):\n    return 0\n",
    "scripts/eval_seal/corpus.py": "def corpus_sha256(d):\n    return ''\n",
    "scripts/eval_seal/sandbox/candidate.sb": "(version 1)\n",
    "scripts/boundary/d1.py": "D1 = True\n",
    "eval/z1b/spec.json": "{}\n",
    "README.md": "not exported\n",
}


def _t12_make_repo(tmp_path, name="repo", extra=None):
    repo = tmp_path / name
    repo.mkdir()
    _t12_git(repo, "init", "-q", "-b", "main")
    files = dict(_T12_FILES)
    files.update(extra or {})
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _t12_git(repo, "add", "-A")
    _t12_git(repo, "commit", "-q", "-m", "seed")
    return repo, _t12_git(repo, "rev-parse", "HEAD")


def _t12_fake_tools(tmp_path):
    bindir = tmp_path / "tools"
    bindir.mkdir()
    out = {}
    for name, body in (("age", b"AGE-BIN-1"), ("age-keygen", b"AGEKEYGEN-BIN-1"), ("claude", b"CLAUDE-BIN-1")):
        p = bindir / name
        p.write_bytes(body)
        p.chmod(0o755)
        out[name] = p
    hf = tmp_path / "hfmodel" / "models--sentence-transformers--all-MiniLM-L6-v2"
    (hf / "blobs").mkdir(parents=True)
    (hf / "refs").mkdir()
    (hf / "refs" / "main").write_text("rev1")
    snap = hf / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (hf / "blobs" / "b1").write_bytes(b"WEIGHTS")
    os.symlink("../../blobs/b1", snap / "model.safetensors")
    (snap / "config.json").write_text("{}")
    out["hf"] = hf
    return out


def _t12_install_args(repo, sha, tools, stage):
    return [
        sha, "--render", stage, "--repo", repo, "--dev-user", DEV, "--dev-home", DEV_HOME,
        "--age", tools["age"], "--age-keygen", tools["age-keygen"], "--claude", tools["claude"],
        "--hf-dir", tools["hf"],
    ]


@pytest.fixture(scope="module")
def t12_world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("t12world")
    repo, sha = _t12_make_repo(tmp)
    tools = _t12_fake_tools(tmp)
    out = {"tmp": tmp, "repo": repo, "sha": sha, "tools": tools}
    for kind, stage_name in (("setup.sh", "setup"), ("uninstall.sh", "uninstall"), ("tamper_drill.sh", "drill")):
        stage = tmp / stage_name
        r = _t12_sh(kind, "--render", stage, "--repo", REPO_PATH, "--dev-user", DEV, "--dev-home", DEV_HOME)
        assert r.returncode == 0, r.stderr
        out[stage_name] = stage
    purge = tmp / "purge"
    r = _t12_sh("uninstall.sh", "--render", purge, "--repo", REPO_PATH, "--dev-user", DEV, "--dev-home", DEV_HOME, "--purge-sealed")
    assert r.returncode == 0, r.stderr
    out["purge"] = purge
    inst = tmp / "install"
    r = _t12_sh("install.sh", *_t12_install_args(repo, sha, tools, inst))
    assert r.returncode == 0, r.stderr
    out["install"] = inst
    export = tmp / "export"
    (export / "wiki" / "decisions").mkdir(parents=True)
    (export / "wiki" / "decisions" / "p1.md").write_text("page one\n")
    (export / "search.sqlite").write_bytes(b"SQLITE-FAKE")
    out["export"] = export
    corp = tmp / "corpus"
    r = _t12_sh("install_corpus.sh", export, "--render", corp)
    assert r.returncode == 0, r.stderr
    out["corpus"] = corp
    return out


def _t12_lines(stage):
    text = (Path(stage) / "commands.txt").read_text()
    return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def _t12_all_lines(w):
    out = []
    for k in ("setup", "install", "uninstall", "purge", "drill", "corpus"):
        out.extend(_t12_lines(w[k]))
    return out


_DIR_RE = re.compile(r"^/usr/bin/install -d -o (\S+) -g (\S+) -m (\d{4}) (\S+)$")
_FILE_RE = re.compile(r"^/usr/bin/install -o (\S+) -g (\S+) -m (\d{4}) (\S+) (\S+)$")


def _t12_dirs(lines):
    out = {}
    for ln in lines:
        m = _DIR_RE.match(ln)
        if m:
            out[m.group(4)] = (m.group(1), m.group(2), m.group(3))
    return out


def _t12_files(lines):
    out = {}
    for ln in lines:
        m = _FILE_RE.match(ln)
        if m:
            out[m.group(5)] = (m.group(1), m.group(2), m.group(3), m.group(4))
    return out


def _t12_expected_dirs():
    p = SealPaths.real()
    ev, cand = ("zurvaneval", "zurvaneval", "0700"), ("zurvancandidate", "zurvancandidate", "0700")
    au = ("zurvanauthor", "zurvanauthor", "0700")
    return {
        "/Library/ZurvanEval": ("root", "wheel", "0755"),
        str(p.runtime_dir): ("root", "wheel", "0755"),
        str(p.corpus_dir): ("root", "zurvancorpus", "0750"),
        str(p.stage_dir): ("zurvaneval", "zurvancandidate", "0750"),
        str(p.eval_home): ev, str(p.keys_dir): ev, str(p.private_dir): ev, str(p.sets_dir): ev, str(p.runs_dir): ev,
        str(p.incidents_dir): ev,
        str(p.candidate_home): cand, str(p.scratch_dir): cand,
        str(p.author_home): au, str(p.author_workspace): au, str(p.author_home) + "/private": au,
        str(p.drop_dir): ("root", "wheel", "1733"),
    }


# ------------------------------------------------------------------ syntax

@pytest.mark.parametrize("script", SCRIPTS + ["_lib.sh"])
def test_t12_every_shell_script_passes_bash_n(script):
    r = subprocess.run(["/bin/bash", "-n", str(SEAL / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", SCRIPTS + ["_lib.sh"])
def test_t12_shellcheck_when_installed(script):
    exe = shutil.which("shellcheck")
    if not exe:
        pytest.skip("shellcheck absent")
    r = subprocess.run([exe, "-x", "-S", "warning", str(SEAL / script)], capture_output=True, text=True, cwd=SEAL)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_t12_scripts_start_strict_and_pin_path(script):
    text = (SEAL / script).read_text()
    assert text.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in text.split("\n", 4)[1] + text.split("\n", 4)[2] + text.split("\n", 4)[3]


def test_t12_lib_pins_path_and_checks_root_and_sudo_user():
    text = (SEAL / "_lib.sh").read_text()
    assert "set -euo pipefail" in text
    assert "PATH=/usr/bin:/bin:/usr/sbin:/sbin" in text
    assert "/usr/bin/id -u" in text
    assert "SUDO_USER" in text


# ------------------------------------------------------------------ real-mode refusal

@needs_clt
@pytest.mark.parametrize("script,extra", [
    ("setup.sh", []), ("install.sh", [SHA_A]), ("install_corpus.sh", ["/nonexistent"]),
    ("tamper_drill.sh", []), ("uninstall.sh", []),
])
def test_t12_real_mode_refuses_without_root_and_creates_nothing(tmp_path, script, extra):
    if os.geteuid() == 0:
        pytest.skip("must run as non-root")
    marker = tmp_path / "home"
    marker.mkdir()
    env = {"HOME": str(marker), "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["/usr/bin/env", "-i", f"HOME={marker}", "PATH=/usr/bin:/bin", "/bin/bash", str(SEAL / script), *extra],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode != 0
    assert "refus" in r.stderr.lower() and "root" in r.stderr.lower()
    assert list(marker.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["home"]


@needs_clt
def test_t12_render_flag_without_dir_is_refused():
    r = _t12_sh("setup.sh", "--render")
    assert r.returncode != 0


@needs_clt
@pytest.mark.parametrize("bad", ["/", "/etc/zz-t12-render", "/Library/zz-t12-render", "/usr/zz-t12", "/private/etc/zz-t12"])
def test_t12_render_refuses_system_directories(bad):
    r = _t12_sh("setup.sh", "--render", bad, "--repo", ROOT, "--dev-user", DEV, "--dev-home", DEV_HOME)
    assert r.returncode != 0
    assert "refus" in (r.stderr + r.stdout).lower()
    assert not Path("/etc/zz-t12-render").exists()
    assert not Path("/Library/zz-t12-render").exists()
    assert not Path("/usr/zz-t12").exists()


# ------------------------------------------------------------------ setup.sh

@needs_clt
def test_t12_setup_directory_table_matches_spec(t12_world):
    lines = _t12_lines(t12_world["setup"])
    assert _t12_dirs(lines) == _t12_expected_dirs()


@needs_clt
def test_t12_setup_chown_chmod_lines_agree_with_the_table(t12_world):
    table = _t12_expected_dirs()
    seen = 0
    for ln in _t12_lines(t12_world["setup"]):
        toks = shlex.split(ln)
        if toks[0] == "/bin/chmod" and len(toks) == 3 and re.fullmatch(r"\d{4}", toks[1]):
            seen += 1
            assert table[toks[2]][2] == toks[1], ln
        if toks[0] == "/usr/sbin/chown" and len(toks) == 3:
            seen += 1
            assert toks[1] == ":".join(table[toks[2]][:2]), ln
    assert seen >= 1  # the sticky drop dir is chmod'ed explicitly as well


@needs_clt
def test_t12_setup_directory_modes_are_never_looser_than_spec(t12_world):
    for path, (_o, _g, mode) in _t12_dirs(_t12_lines(t12_world["setup"])).items():
        if path.startswith("/Users/zurvan"):
            assert mode == "0700", path


@needs_clt
def test_t12_setup_users_groups_and_login_hiding(t12_world):
    text = "\n".join(_t12_lines(t12_world["setup"]))
    for user in ("zurvaneval", "zurvancandidate", "zurvanauthor"):
        assert f"/usr/bin/dscl . -create /Users/{user} IsHidden 1" in text
        assert f"/usr/bin/dscl . -create /Users/{user} UserShell /usr/bin/false" in text
        assert re.search(rf"/usr/bin/dscl \. -create /Users/{user} Password '?\*'?$", text, re.M)
        assert f"NFSHomeDirectory /Users/{user}" in text
    assert "/usr/bin/dscl . -append /Groups/zurvancorpus GroupMembership zurvaneval zurvancandidate zurvanauthor" in text
    assert "-create /Groups/zurvancorpus" in text and "-create /Groups/zurvancandidate" in text
    # non-admin: neither user is ever appended to the admin group
    assert "/Groups/admin" not in text
    assert "dseditgroup" not in text
    # refuses to clobber existing identities
    assert "/usr/bin/dscl . -read /Users/zurvaneval" in text
    assert "/usr/bin/dscl . -read /Users/zurvancandidate" in text
    assert "/usr/bin/dscl . -read /Users/zurvanauthor" in text


@needs_clt
def test_t12_setup_time_machine_exclusion_for_keys(t12_world):
    lines = _t12_lines(t12_world["setup"])
    assert "/usr/bin/tmutil addexclusion -p /Users/zurvaneval/keys" in lines
    assert "/usr/bin/tmutil addexclusion -p /Users/zurvaneval/private" in lines  # R18
    assert "/usr/bin/tmutil addexclusion -p /Users/zurvanauthor/workspace" in lines  # Rev 3 #2
    assert "/usr/bin/tmutil addexclusion -p /Users/zurvanauthor/private" in lines
    # exclusion is applied after the directory exists
    assert lines.index("/usr/bin/tmutil addexclusion -p /Users/zurvaneval/keys") > next(
        i for i, ln in enumerate(lines) if ln.endswith(" /Users/zurvaneval/keys") and ln.startswith("/usr/bin/install -d"))


@needs_clt
def test_t12_setup_pf_anchor_blocks_candidate_outbound(t12_world):
    stage = Path(t12_world["setup"])
    anchor = (stage / "etc/pf.anchors/zurvan-eval").read_text()
    rules = [ln for ln in anchor.splitlines() if ln and not ln.startswith("#")]
    assert rules == ["block drop out quick user zurvancandidate"]
    text = "\n".join(_t12_lines(stage))
    assert 'anchor "zurvan-eval"' in text
    assert 'load anchor "zurvan-eval" from "/etc/pf.anchors/zurvan-eval"' in text
    assert "/sbin/pfctl -n -f /etc/pf.conf" in text
    assert "/sbin/pfctl -f /etc/pf.conf" in text
    assert "/sbin/pfctl -E" in text or "pfctl -E" in text
    assert (stage / "Library/LaunchDaemons/local.zurvan.eval.pf.plist").exists()


@needs_clt
def test_t12_setup_cron_and_at_deny_for_candidate(t12_world):
    text = "\n".join(_t12_lines(t12_world["setup"]))
    assert "/usr/lib/cron/cron.deny" in text
    assert "/usr/lib/cron/at.deny" in text
    assert text.count("zurvancandidate >> /usr/lib/cron/") == 2 or text.count(">> /usr/lib/cron/") == 2


# setup grants each ACL only if the target exists (so a missing optional dev path never aborts the
# -euo pipefail install); the chmod is therefore wrapped in `if [ -e T ]; then ...; fi`, or, for the
# transcript glob, a `for d in <glob>; do if [ -e "$d" ]; then ... "$d"; fi; done` loop.
_ACL_CHMOD = re.compile(r"/bin/chmod (?:-R )?\+a '?user:zurvaneval allow ([a-z_,]+)'? (\S+?)(?:;|$)")


def _t12_acls(lines):
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("for d in "):                       # glob loop: the target is the glob, not "$d"
            glob = s[len("for d in "):].split(";", 1)[0].strip()
            m = _ACL_CHMOD.search(s)
            if m:
                out.append((set(m.group(1).split(",")), glob))
            continue
        m = _ACL_CHMOD.search(s)                             # `if [ -e T ]; then chmod ... T; fi`, or a bare chmod
        if m:
            out.append((set(m.group(1).split(",")), m.group(2)))
    return out


@needs_clt
def test_t12_setup_acls_are_read_only(t12_world):
    acls = _t12_acls(_t12_lines(t12_world["setup"]))
    assert acls
    allowed = {"search", "read", "list", "readattr", "file_inherit", "directory_inherit"}
    for perms, path in acls:
        assert perms <= allowed, (perms, path)
        for bad in ("write", "delete", "add_file", "add_subdirectory", "delete_child", "append", "writeattr",
                    "writeextattr", "writesecurity", "chown", "limit_inherit", "only_inherit"):
            assert bad not in perms
    # no ACL grant ever mentions another user or a deny/write verb
    for ln in _t12_lines(t12_world["setup"]):
        if "+a" in ln:
            assert "allow" in ln and "deny" not in ln
            assert "user:zurvaneval" in ln and "zurvancandidate" not in ln


@needs_clt
def test_t12_setup_acl_targets_cover_the_scan_set(t12_world):
    acls = _t12_acls(_t12_lines(t12_world["setup"]))
    by_path = {p: perms for perms, p in acls}
    full = {"read", "list", "search", "readattr", "file_inherit", "directory_inherit"}
    for p in (f"{DEV_HOME}/.claude/projects/*Zurvan*", f"{REPO_PATH}/data/traces", f"{REPO_PATH}/wiki/traces",
              f"{REPO_PATH}/logs", f"{DEV_HOME}/.zurvan/reports"):
        assert by_path[p] == full, p
    for p in (DEV_HOME, f"{DEV_HOME}/Desktop", f"{DEV_HOME}/Desktop/Raouf", REPO_PATH, f"{REPO_PATH}/data",
              f"{REPO_PATH}/wiki", f"{DEV_HOME}/.claude", f"{DEV_HOME}/.claude/projects", f"{DEV_HOME}/.zurvan"):
        assert by_path[p] == {"search"}, p
    # Rev 3 #2: the eval identity also gets a READ ACL on the author workspace, so the seal phase can read
    # the draft bundle. That is the only ACL target outside the dev home / repo.
    assert by_path["/Users/zurvanauthor/workspace"] == full
    for p in by_path:
        assert (p.startswith(DEV_HOME) or p.startswith(REPO_PATH)
                or p == "/Users/zurvanauthor/workspace")


@needs_clt
def test_t12_setup_does_not_generate_keys_or_touch_sudoers(t12_world):
    text = "\n".join(_t12_lines(t12_world["setup"]))
    assert "age-keygen" not in text and "ssh-keygen" not in text
    assert "sudoers" not in text
    assert not (Path(t12_world["setup"]) / "etc/sudoers.d").exists()


# ------------------------------------------------------------------ the sealed side executes only root-owned code

_SAFE_BINS = ("/usr/bin/", "/bin/", "/usr/sbin/", "/sbin/", "/Library/ZurvanEval/broker/bin/")
_FORBID = ("/opt/homebrew", "/opt/anaconda3", "/Users/dev", "~/", "/usr/local/")


@needs_clt
def test_t12_nothing_runs_unowned_binaries_as_the_sealed_identities(t12_world):
    seen = 0
    for ln in _t12_all_lines(t12_world):
        for ident in ("zurvaneval", "zurvancandidate"):
            m = re.search(rf"-u {ident} (?:-H )?(\S+)", ln)
            if m and "/usr/bin/sudo" in ln:
                seen += 1
                exe = m.group(1)
                assert exe.startswith(_SAFE_BINS), ln
                assert not exe.startswith(("/Library/ZurvanEval/broker/lib", "/Users")), ln
    assert seen >= 3


@needs_clt
def test_t12_no_command_line_names_a_user_owned_binary(t12_world):
    for ln in _t12_all_lines(t12_world):
        for bad in _FORBID:
            if bad == "/Users/dev":
                continue  # ACL targets legitimately live in the dev home; checked separately
            assert bad not in ln, ln
        first = shlex.split(ln)[0] if not re.match(r"^(if |\[ |trap |[A-Z_0-9]+=|for |\{)", ln) else ""
        if first:
            assert first.startswith("/") and not first.startswith(("/Users", "/opt", "/private/var/folders")), ln


@needs_clt
def test_t12_command_lines_have_a_known_shape(t12_world):
    for ln in _t12_all_lines(t12_world):
        assert "\n" not in ln
        assert re.match(r"^(/|if |\[ |trap |[A-Z_0-9]+=|for |\{)", ln), ln


# ------------------------------------------------------------------ install.sh

def _t12_stage_file(stage, final):
    return Path(stage) / str(final).lstrip("/")


@needs_clt
def test_t12_install_exports_only_reviewed_sources_with_layout(t12_world):
    lib = _t12_stage_file(t12_world["install"], "/Library/ZurvanEval/broker/lib")
    got = sorted(str(p.relative_to(lib)) for p in lib.rglob("*") if p.is_file())
    assert got == sorted(k for k in _T12_FILES if k.startswith(("scripts/eval_seal", "scripts/boundary", "eval/z1b/")))
    assert not (lib / "scripts/__init__.py").exists()
    assert not any(p.is_symlink() for p in lib.rglob("*"))


@needs_clt
def test_t12_install_records_sha_and_tree(t12_world):
    stage, repo, sha = t12_world["install"], t12_world["repo"], t12_world["sha"]
    assert _t12_stage_file(stage, "/Library/ZurvanEval/broker/INSTALLED_SHA").read_text() == sha + "\n"
    tree = _t12_git(repo, "rev-parse", f"{sha}:scripts/eval_seal")
    assert _t12_stage_file(stage, "/Library/ZurvanEval/broker/INSTALLED_TREE").read_text() == tree + "\n"
    assert _t12_stage_file(stage, SealPaths.real().installed_sha_file).exists()


@needs_clt
def test_t12_install_modes_owners_and_no_writable_bits(t12_world):
    lines = _t12_lines(t12_world["install"])
    dirs, files = _t12_dirs(lines), _t12_files(lines)
    broker_dirs = {p: v for p, v in dirs.items() if p.startswith("/Library/ZurvanEval/broker") or p.startswith("/Library/ZurvanEval/runtime")}
    assert "/Library/ZurvanEval/broker/lib/scripts/eval_seal" in broker_dirs
    for p, v in broker_dirs.items():
        assert v == ("root", "wheel", "0555"), p
    for p, v in files.items():
        if p.startswith(("/Library/ZurvanEval/broker", "/Library/ZurvanEval/runtime")):
            assert v[0:2] == ("root", "wheel"), p
            want = "0555" if p.startswith("/Library/ZurvanEval/broker/bin/") else "0444"
            assert v[2] == want, p
    # the post-install sweep that proves nothing is writable or foreign-owned is part of the plan
    text = "\n".join(lines)
    assert "-perm -0020" in text and "-perm -0002" in text and "! -user root" in text


@needs_clt
def test_t12_install_pins_binaries_by_digest(t12_world):
    stage, tools = t12_world["install"], t12_world["tools"]
    pinned = _t12_stage_file(stage, "/Library/ZurvanEval/broker/PINNED.sha256").read_text().splitlines()
    want = sorted(
        f"{hashlib.sha256(tools[n].read_bytes()).hexdigest()}  /Library/ZurvanEval/broker/bin/{n}"
        for n in ("age", "age-keygen", "claude"))
    assert sorted(pinned) == want
    for n in ("age", "age-keygen", "claude"):
        assert _t12_stage_file(stage, f"/Library/ZurvanEval/broker/bin/{n}").read_bytes() == tools[n].read_bytes()
    text = "\n".join(_t12_lines(stage))
    assert "/usr/bin/shasum -a 256 -c /Library/ZurvanEval/broker/PINNED.sha256" in text


@needs_clt
def test_t12_install_copies_hf_model_without_symlinks_or_blobs(t12_world):
    base = _t12_stage_file(t12_world["install"], "/Library/ZurvanEval/runtime/hf/hub/models--sentence-transformers--all-MiniLM-L6-v2")
    assert (base / "refs/main").read_text() == "rev1"
    weights = base / "snapshots/rev1/model.safetensors"
    assert weights.read_bytes() == b"WEIGHTS" and not weights.is_symlink()
    assert not (base / "blobs").exists()
    assert not any(p.is_symlink() for p in base.rglob("*"))


@needs_clt
def test_t12_wrappers_use_clt_python_isolated_and_absolute_lib(t12_world):
    for name, mod, entry in (("zurvan-broker", "broker", "broker_main"), ("zurvan-candidate-run", "candidate_run", "main"),
                             ("zurvan-author", "author", "author_main")):
        text = _t12_stage_file(t12_world["install"], f"/Library/ZurvanEval/broker/bin/{name}").read_text()
        first = text.splitlines()[0]
        assert first == f"#!{CLT_PY} -I"
        assert "/opt/" not in text and "/usr/local" not in text and "anaconda" not in text
        assert "environ" not in text and "getenv" not in text and "import os" not in text
        assert 'sys.path.insert(0, "/Library/ZurvanEval/broker/lib")' in text
        assert f"from scripts.eval_seal.{mod} import {entry} as _entry" in text
        assert "sys.exit(_entry(sys.argv[1:]))" in text
        # compiles under the python that will run it
        compile(text.split("\n", 1)[1], name, "exec")


def _t12_wrappers(stage):
    out = {}
    for name in ("zurvan-broker", "zurvan-candidate-run", "zurvan-author"):
        final = f"/Library/ZurvanEval/broker/bin/{name}"
        out[final] = _t12_stage_file(stage, final).read_bytes()
    return out


@needs_clt
def test_t12_sudoers_has_exactly_two_rules_pinned_to_wrapper_digests(t12_world):
    text = _t12_stage_file(t12_world["install"], "/etc/sudoers.d/zurvan-eval").read_text()
    wrappers = _t12_wrappers(t12_world["install"])
    assert R.validate_sudoers(text, wrappers) == []
    rules = [ln for ln in text.splitlines() if "NOPASSWD" in ln]
    assert len(rules) == 2
    b, c = "/Library/ZurvanEval/broker/bin/zurvan-broker", "/Library/ZurvanEval/broker/bin/zurvan-candidate-run"
    db, dc = (hashlib.sha256(wrappers[x]).hexdigest() for x in (b, c))
    assert rules[0] == f"{DEV} ALL=(zurvaneval) NOPASSWD: sha256:{db} {b}"
    assert rules[1] == f"zurvaneval ALL=(zurvancandidate) NOPASSWD: sha256:{dc} {c}"
    assert db != dc
    # no rule for the author wrapper: Raouf runs it with his own password
    assert "zurvan-author" not in text
    for forbidden in ("ALL=(ALL)", "SETENV", "*", "?", "ALL, ", "!/", "\\"):
        assert forbidden not in text.replace("# ", "#"), forbidden
    assert re.search(r"^Defaults>zurvaneval .*env_reset", text, re.M)
    assert re.search(r"^Defaults>zurvancandidate .*env_reset", text, re.M)
    assert text.count("!env_keep") == 2 and text.count("!setenv") == 2


@needs_clt
def test_t12_sudoers_passes_visudo_check_and_is_staged_before_install(t12_world):
    stage = Path(t12_world["install"])
    staged = stage / "etc/sudoers.d/zurvan-eval"
    r = subprocess.run(["/usr/sbin/visudo", "-c", "-f", str(staged)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = _t12_lines(stage)
    i_check = next(i for i, ln in enumerate(lines) if ln.startswith("/usr/sbin/visudo -c -f") and str(stage) in ln)
    i_inst = next(i for i, ln in enumerate(lines) if ln.endswith(" /etc/sudoers.d/zurvan-eval") and ln.startswith("/usr/bin/install"))
    assert i_check < i_inst
    assert lines[i_inst].startswith("/usr/bin/install -o root -g wheel -m 0440 ")
    # sudoers is the very last privileged step that changes authority: after bins, pins and perms sweep
    assert i_inst > max(i for i, ln in enumerate(lines) if "PINNED.sha256" in ln)
    assert any(ln.startswith("/usr/sbin/visudo -c -f /etc/sudoers.d/zurvan-eval") for ln in lines[i_inst:])
    # the digest in the plan is re-verified against the installed bytes before sudoers is installed
    assert any("shasum -a 256" in ln and "zurvan-broker" in ln for ln in lines[:i_inst])


@needs_clt
def test_t12_visudo_rejects_a_broken_rule(t12_world, tmp_path):
    text = _t12_stage_file(t12_world["install"], "/etc/sudoers.d/zurvan-eval").read_text()
    bad = tmp_path / "bad"
    bad.write_text(text + "this is not sudoers\n")
    r = subprocess.run(["/usr/sbin/visudo", "-c", "-f", str(bad)], capture_output=True, text=True)
    assert r.returncode != 0


@needs_clt
def test_t12_dev_user_with_a_dot_is_valid_sudoers_syntax(t12_world):
    # dev is the real dev account; the digest rule must still parse
    text = _t12_stage_file(t12_world["install"], "/etc/sudoers.d/zurvan-eval").read_text()
    assert f"{DEV} ALL=" in text


def _t12_good_sudoers(w):
    return (_t12_stage_file(w["install"], "/etc/sudoers.d/zurvan-eval").read_text(), _t12_wrappers(w["install"]))


@needs_clt
@pytest.mark.parametrize("mutate,expect", [
    (lambda t: t + "raoof ALL=(ALL) ALL\n", "unexpected"),
    (lambda t: t + "raoof ALL=(ALL) NOPASSWD: ALL\n", "unexpected"),
    (lambda t: t.replace("NOPASSWD: sha256:", "NOPASSWD: ", 1), "rule"),
    (lambda t: re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, t, count=1), "digest"),
    (lambda t: t.replace("ALL=(zurvaneval)", "ALL=(root)", 1), "rule"),
    (lambda t: t.replace("ALL=(zurvaneval)", "ALL=(ALL)", 1), "rule"),
    (lambda t: t.replace("zurvan-broker", "zurvan-*", 1), "rule"),
    (lambda t: t.replace("NOPASSWD:", "SETENV: NOPASSWD:", 1), "rule"),
    (lambda t: t.replace("!env_keep, ", "", 1), "unexpected"),
    (lambda t: t.replace("!setenv, ", "", 1), "unexpected"),
    (lambda t: "#include /tmp/evil\n" + t, "include"),
    (lambda t: "#includedir /tmp/evil.d\n" + t, "include"),
    (lambda t: "@include /tmp/evil\n" + t, "unexpected"),
    (lambda t: t + "Defaults !authenticate\n", "unexpected"),
    (lambda t: t + "Defaults env_keep += \"PATH\"\n", "unexpected"),
    (lambda t: t + "# harmless \\\nraoof ALL=(ALL) ALL\n", "backslash"),
    (lambda t: t.split("\n", 6)[0] + "\n" + t.split("\n", 6)[6], "rule"),
    (lambda t: t + t.splitlines()[-1] + "\n", "rule"),
    (lambda t: t.replace(f"{DEV} ALL", "root ALL", 1), "rule"),
    (lambda t: t.replace(f"{DEV} ALL", "%admin ALL", 1), "rule"),
    (lambda t: t.replace(f"{DEV} ALL", "ALL ALL", 1), "rule"),
])
def test_t12_validate_sudoers_flags_every_mutation(t12_world, mutate, expect):
    text, wrappers = _t12_good_sudoers(t12_world)
    problems = R.validate_sudoers(mutate(text), wrappers)
    assert problems, "mutation went undetected"
    assert any(expect in p for p in problems), problems


@needs_clt
def test_t12_validate_sudoers_detects_a_changed_wrapper(t12_world):
    text, wrappers = _t12_good_sudoers(t12_world)
    b = "/Library/ZurvanEval/broker/bin/zurvan-broker"
    tampered = dict(wrappers)
    tampered[b] = wrappers[b] + b"#"
    problems = R.validate_sudoers(text, tampered)
    assert any("digest" in p for p in problems)
    missing = {k: v for k, v in wrappers.items() if k != b}
    assert R.validate_sudoers(text, missing)


@needs_clt
def test_t12_a_user_name_cannot_smuggle_sudoers_syntax(t12_world):
    wrappers = _t12_wrappers(t12_world["install"])
    for bad in ("root", "ALL", "%admin", "zurvaneval", "zurvancandidate", "a b", "x\nroot ALL=(ALL) ALL", "",
                "a" * 80, "-x", "x,y", "x=ALL", "#x", "x:y", "!x", "x\\"):
        with pytest.raises(R.OperatorRefusal):
            R.render_sudoers(bad, wrappers)
        with pytest.raises(R.OperatorRefusal):
            R.validate_user(bad)
    assert R.validate_user("dev") == "dev"


@needs_clt
def test_t12_scan_launchd_plist_lints_and_runs_as_eval_user(t12_world):
    stage = Path(t12_world["install"])
    plist = stage / "Library/LaunchDaemons/local.zurvan.eval.scan.plist"
    r = subprocess.run(["/usr/bin/plutil", "-lint", str(plist)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    d = plistlib.loads(plist.read_bytes())
    assert d["UserName"] == "zurvaneval"
    assert d["ProgramArguments"] == ["/Library/ZurvanEval/broker/bin/zurvan-broker", "scan"]
    assert d["StartCalendarInterval"] == {"Hour": 4, "Minute": 17}
    assert "sudo" not in json.dumps(d)
    pf = stage.parent / "setup/Library/LaunchDaemons/local.zurvan.eval.pf.plist"
    assert subprocess.run(["/usr/bin/plutil", "-lint", str(pf)], capture_output=True).returncode == 0


@needs_clt
def test_t12_install_writes_scan_targets_and_empty_mcp_config(t12_world):
    stage = Path(t12_world["install"])
    cfg = json.loads(_t12_stage_file(stage, "/Library/ZurvanEval/broker/SCAN_TARGETS.json").read_text())
    assert cfg == {"dev_home": DEV_HOME, "repo": str(t12_world["repo"])}
    assert json.loads(_t12_stage_file(stage, "/Library/ZurvanEval/broker/empty-mcp.json").read_text()) == {"mcpServers": {}}


@needs_clt
def test_t12_install_generates_keys_as_eval_user_only_when_absent(t12_world):
    lines = _t12_lines(t12_world["install"])
    age = [ln for ln in lines if "age-keygen" in ln and "-u zurvaneval" in ln]
    ssh = [ln for ln in lines if "ssh-keygen" in ln and "-u zurvaneval" in ln]
    assert len(age) == 1 and len(ssh) == 1
    assert age[0].startswith("if [ ! -e /Users/zurvaneval/keys/age.key ]; then /usr/bin/sudo -u zurvaneval -H /Library/ZurvanEval/broker/bin/age-keygen -o /Users/zurvaneval/keys/age.key")
    assert "/usr/bin/ssh-keygen" in ssh[0] and "-t ed25519" in ssh[0] and "-N ''" in ssh[0]
    assert "/Users/zurvaneval/keys/signing_ed25519" in ssh[0] and ssh[0].startswith("if [ ! -e ")
    # keys are made after the pinned age binary is installed and verified
    i_age_bin = next(i for i, ln in enumerate(lines) if ln.endswith(" /Library/ZurvanEval/broker/bin/age-keygen") and "install" in ln)
    assert lines.index(age[0]) > i_age_bin


@needs_clt
def test_t12_install_derives_allowed_signers_as_eval_after_the_signing_key(t12_world):
    # #7: the manifest-verify trust anchor is created at install, as the eval identity, from the pubkey of
    # the signing key generated just above it. Never left to a pre-existing or externally supplied file.
    lines = _t12_lines(t12_world["install"])
    al = [ln for ln in lines if "/Users/zurvaneval/keys/allowed_signers" in ln and "-u zurvaneval" in ln]
    assert len(al) == 1, al
    ln = al[0]
    assert "/Users/zurvaneval/keys/signing_ed25519.pub" in ln
    assert 'namespaces=' in ln and "zurvan-z1b-manifest" in ln and "zurvaneval" in ln
    ssh = next(i for i, l in enumerate(lines) if "ssh-keygen" in l and "-u zurvaneval" in l)
    assert lines.index(ln) > ssh  # derived only after the signing key exists


@needs_clt
def test_t12_install_requires_setup_first_and_the_clt_python(t12_world):
    text = "\n".join(_t12_lines(t12_world["install"]))
    assert "/bin/test -d /Library/ZurvanEval" in text
    assert "/bin/test -d /Users/zurvaneval/keys" in text
    assert f"/bin/test -x {CLT_PY}" in text


@needs_clt
def test_t12_install_is_deterministic(t12_world, tmp_path):
    w = t12_world
    again = tmp_path / "again"
    r = _t12_sh("install.sh", *_t12_install_args(w["repo"], w["sha"], w["tools"], again))
    assert r.returncode == 0, r.stderr
    a = (Path(w["install"]) / "commands.txt").read_text().replace(str(w["install"]), "@S@")
    b = (again / "commands.txt").read_text().replace(str(again), "@S@")
    assert a == b
    for rel in ("etc/sudoers.d/zurvan-eval", "Library/ZurvanEval/broker/bin/zurvan-broker",
                "Library/LaunchDaemons/local.zurvan.eval.scan.plist"):
        assert (Path(w["install"]) / rel).read_bytes() == (again / rel).read_bytes()


# ---- install.sh refusals

@needs_clt
@pytest.mark.parametrize("bad", ["abc123", "A" * 40, "g" * 40, "a" * 39, "a" * 41, "$(touch /tmp/t12-pwn)", "a" * 40 + "\n",
                                 "HEAD", "main", "-h", "../" + "a" * 37, "a" * 40 + " --upload-pack=x"])
def test_t12_install_refuses_a_malformed_sha(t12_world, tmp_path, bad):
    w = t12_world
    out = tmp_path / "out"
    r = _t12_sh("install.sh", *_t12_install_args(w["repo"], bad, w["tools"], out))
    assert r.returncode != 0
    assert not (out / "etc/sudoers.d/zurvan-eval").exists()
    assert not Path("/tmp/t12-pwn").exists()


@needs_clt
def test_t12_install_refuses_a_sha_that_is_not_a_commit(t12_world, tmp_path):
    w = t12_world
    out = tmp_path / "out"
    r = _t12_sh("install.sh", *_t12_install_args(w["repo"], "b" * 40, w["tools"], out))
    assert r.returncode != 0 and "commit" in r.stderr.lower()
    assert not (out / "etc/sudoers.d/zurvan-eval").exists()
    # a tree or blob oid is also not a commit
    tree = _t12_git(w["repo"], "rev-parse", f"{w['sha']}^{{tree}}")
    r = _t12_sh("install.sh", *_t12_install_args(w["repo"], tree, w["tools"], out))
    assert r.returncode != 0


@needs_clt
def test_t12_install_refuses_a_commit_not_contained_in_head(t12_world, tmp_path):
    repo, sha = _t12_make_repo(tmp_path, "r2")
    _t12_git(repo, "checkout", "-q", "-b", "side")
    (repo / "scripts/eval_seal/paths.py").write_text("X = 2\n")
    _t12_git(repo, "commit", "-q", "-am", "side")
    side = _t12_git(repo, "rev-parse", "HEAD")
    _t12_git(repo, "checkout", "-q", "main")
    tools = _t12_fake_tools(tmp_path)
    out = tmp_path / "out"
    r = _t12_sh("install.sh", *_t12_install_args(repo, side, tools, out))
    assert r.returncode != 0 and "head" in r.stderr.lower()
    assert not (out / "etc/sudoers.d/zurvan-eval").exists()


@needs_clt
def test_t12_install_refuses_a_symlink_in_the_exported_tree(tmp_path):
    repo, _ = _t12_make_repo(tmp_path, "r3")
    os.symlink("/etc/passwd", repo / "scripts/eval_seal/evil.py")
    _t12_git(repo, "add", "-A")
    _t12_git(repo, "commit", "-q", "-m", "symlink")
    sha = _t12_git(repo, "rev-parse", "HEAD")
    tools = _t12_fake_tools(tmp_path)
    out = tmp_path / "out"
    r = _t12_sh("install.sh", *_t12_install_args(repo, sha, tools, out))
    assert r.returncode != 0 and "symlink" in r.stderr.lower()
    assert not (out / "etc/sudoers.d/zurvan-eval").exists()


@needs_clt
def test_t12_install_refuses_export_subst_rewriting(tmp_path):
    # a .gitattributes in the reviewed tree must not be able to change the bytes that get installed
    repo, _ = _t12_make_repo(tmp_path, "r4", extra={
        "scripts/eval_seal/.gitattributes": "paths.py export-subst\n",
        "scripts/eval_seal/paths.py": "X = '$Format:%H$'\n",
    })
    sha = _t12_git(repo, "rev-parse", "HEAD")
    tools = _t12_fake_tools(tmp_path)
    out = tmp_path / "out"
    r = _t12_sh("install.sh", *_t12_install_args(repo, sha, tools, out))
    assert r.returncode != 0 and "differ" in r.stderr.lower()
    assert not (out / "etc/sudoers.d/zurvan-eval").exists()


@needs_clt
def test_t12_install_refuses_when_entrypoints_are_missing(tmp_path):
    repo, _ = _t12_make_repo(tmp_path, "r5")
    _t12_git(repo, "rm", "-q", "scripts/eval_seal/broker.py")
    _t12_git(repo, "commit", "-q", "-m", "drop broker")
    sha = _t12_git(repo, "rev-parse", "HEAD")
    tools = _t12_fake_tools(tmp_path)
    out = tmp_path / "out"
    r = _t12_sh("install.sh", *_t12_install_args(repo, sha, tools, out))
    assert r.returncode != 0 and "broker" in r.stderr.lower()


@needs_clt
def test_t12_install_refuses_a_missing_or_symlinked_pinned_input(t12_world, tmp_path):
    w = t12_world
    tools = dict(w["tools"])
    tools["age"] = tmp_path / "missing-age"
    r = _t12_sh("install.sh", *_t12_install_args(w["repo"], w["sha"], tools, tmp_path / "o1"))
    assert r.returncode != 0
    tools = dict(w["tools"])
    tools["hf"] = tmp_path / "no-such-model"
    r = _t12_sh("install.sh", *_t12_install_args(w["repo"], w["sha"], tools, tmp_path / "o2"))
    assert r.returncode != 0


def test_t12_working_tree_must_equal_the_reviewed_sha_in_real_mode(tmp_path):
    repo, sha = _t12_make_repo(tmp_path, "r6")
    R.assert_clean_tree(repo, sha, None)  # clean: no raise
    (repo / "scripts/eval_seal/paths.py").write_text("X = 99\n")
    with pytest.raises(R.OperatorRefusal) as exc:
        R.assert_clean_tree(repo, sha, None)
    assert "differs" in str(exc.value)
    _t12_git(repo, "checkout", "-q", "--", "scripts/eval_seal/paths.py")
    (repo / "scripts/eval_seal/evil.py").write_text("print(1)\n")
    with pytest.raises(R.OperatorRefusal) as exc:
        R.assert_clean_tree(repo, sha, None)
    assert "untracked" in str(exc.value)


def test_t12_export_sources_returns_verified_bytes(tmp_path):
    repo, sha = _t12_make_repo(tmp_path, "r7")
    files = R.export_sources(repo, sha, None)
    assert files["scripts/eval_seal/paths.py"] == b"X = 1\n"
    assert "README.md" not in files
    assert set(files) == {k for k in _T12_FILES if k.startswith(("scripts/eval_seal", "scripts/boundary", "eval/z1b/"))}


# ------------------------------------------------------------------ install_corpus.sh

@needs_clt
def test_t12_corpus_modes_group_and_copy_not_link(t12_world):
    lines = _t12_lines(t12_world["corpus"])
    dirs, files = _t12_dirs(lines), _t12_files(lines)
    assert dirs["/Library/ZurvanEval/corpus/wiki"] == ("root", "zurvancorpus", "0750")
    assert dirs["/Library/ZurvanEval/corpus/wiki/decisions"] == ("root", "zurvancorpus", "0750")
    assert set(files) == {"/Library/ZurvanEval/corpus/search.sqlite", "/Library/ZurvanEval/corpus/wiki/decisions/p1.md"}
    for p, (o, g, m, src) in files.items():
        assert (o, g, m) == ("root", "zurvancorpus", "0440")
        assert src.startswith(str(t12_world["export"]))
    text = "\n".join(lines)
    assert " ln " not in text and "ln -" not in text and "cp -l" not in text and "--link" not in text
    # hardlink / symlink sweep after the copy
    assert "-type f -links +1" in text and "-type l" in text
    # print the digest of the INSTALLED bytes using the installed (pinned) code
    assert f"{CLT_PY} -I -c" in text and "corpus_sha256" in text and "/Library/ZurvanEval/broker/lib" in text


@needs_clt
def test_t12_corpus_refuses_a_nonempty_destination_and_requires_install_first(t12_world):
    text = "\n".join(_t12_lines(t12_world["corpus"]))
    assert "/bin/ls -A /Library/ZurvanEval/corpus" in text
    assert "/bin/test -f /Library/ZurvanEval/broker/lib/scripts/eval_seal/corpus.py" in text
    assert "/usr/bin/dscl . -read /Groups/zurvancorpus" in text


@needs_clt
def test_t12_corpus_refuses_symlinks_special_names_and_missing_dir(tmp_path):
    exp = tmp_path / "e"
    exp.mkdir()
    (exp / "a.txt").write_text("a")
    os.symlink("/etc/passwd", exp / "evil")
    r = _t12_sh("install_corpus.sh", exp, "--render", tmp_path / "o1")
    assert r.returncode != 0 and "symlink" in r.stderr.lower()
    exp2 = tmp_path / "e2"
    exp2.mkdir()
    (exp2 / "bad\nname").write_text("x")
    r = _t12_sh("install_corpus.sh", exp2, "--render", tmp_path / "o2")
    assert r.returncode != 0
    r = _t12_sh("install_corpus.sh", tmp_path / "nope", "--render", tmp_path / "o3")
    assert r.returncode != 0
    exp3 = tmp_path / "e3"
    exp3.mkdir()
    r = _t12_sh("install_corpus.sh", exp3, "--render", tmp_path / "o4")
    assert r.returncode != 0 and "empty" in r.stderr.lower()
    exp4 = tmp_path / "e4"
    exp4.mkdir()
    (exp4 / "x").write_text("x")
    os.link(exp4 / "x", exp4 / "y")
    r = _t12_sh("install_corpus.sh", exp4, "--render", tmp_path / "o5")
    assert r.returncode != 0 and "hardlink" in r.stderr.lower()
    exp5 = tmp_path / "e5"
    exp5.mkdir()
    (exp5 / "a b'c").write_text("quoted")
    r = _t12_sh("install_corpus.sh", exp5, "--render", tmp_path / "o6")
    assert r.returncode == 0
    ln = [x for x in _t12_lines(tmp_path / "o6") if x.startswith("/usr/bin/install -o")][0]
    assert shlex.split(ln)[-1] == "/Library/ZurvanEval/corpus/a b'c"


# ------------------------------------------------------------------ tamper_drill.sh

@needs_clt
def test_t12_tamper_drill_plan_has_the_oracle_steps_in_order(t12_world):
    lines = _t12_lines(t12_world["drill"])
    text = "\n".join(lines)
    b = "/Library/ZurvanEval/broker/bin/zurvan-broker"
    i_bak = next(i for i, ln in enumerate(lines) if ln.startswith("/bin/cp -p") and ".bak" in ln)
    i_trap = next(i for i, ln in enumerate(lines) if ln.startswith("trap "))
    i_app = next(i for i, ln in enumerate(lines) if "printf '#'" in ln or 'printf "#"' in ln)
    i_run = next(i for i, ln in enumerate(lines) if f"/usr/bin/sudo -u {DEV} /usr/bin/sudo -n -u zurvaneval" in ln and "status" in ln)
    i_rest = next(i for i, ln in enumerate(lines) if ln.startswith("/bin/mv -f") and ".bak" in ln)
    i_pos = max(i for i, ln in enumerate(lines) if "sudo -n -u zurvaneval" in ln)
    i_verdict = next(i for i, ln in enumerate(lines) if "DRILL PASS" in ln)
    assert i_bak < i_trap < i_app < i_run < i_rest < i_pos < i_verdict
    assert ">>" in lines[i_app] and b in lines[i_app] or "DRILL_BROKER" in lines[i_app]
    assert "DRILL FAIL" in text and "exit 1" in text
    assert "/etc/sudoers.d/zurvan-eval" in text and "shasum -a 256" in text
    # the drill never writes the sudoers file and never stages a replacement broker
    assert not any("install" in ln and "sudoers" in ln for ln in lines)
    assert not (Path(t12_world["drill"]) / "etc").exists()
    assert not (Path(t12_world["drill"]) / "Library").exists()


@needs_clt
def test_t12_tamper_drill_restores_even_when_the_run_step_blows_up(t12_world):
    lines = _t12_lines(t12_world["drill"])
    trap = next(ln for ln in lines if ln.startswith("trap "))
    assert ".bak" in trap and "EXIT" in trap and "/bin/mv -f" in trap


@needs_clt
def test_t12_tamper_drill_expects_refusal_not_success(t12_world):
    text = "\n".join(_t12_lines(t12_world["drill"]))
    assert "DRILL_RC" in text and '-ne 0' in text
    # the positive control after restore must succeed, so a drill that fails for another reason cannot pass
    assert "POS_RC" in text


# ------------------------------------------------------------------ uninstall.sh

@needs_clt
def test_t12_uninstall_keeps_sealed_data_by_default(t12_world):
    text = "\n".join(_t12_lines(t12_world["uninstall"]))
    assert "dscl . -delete" not in text
    assert "/Users/zurvaneval" not in text.replace("user:zurvaneval", "")
    assert "removeexclusion" not in text
    assert "rm -rf /Library/ZurvanEval/broker" in text
    assert "rm -f /etc/sudoers.d/zurvan-eval" in text
    assert "/Library/LaunchDaemons/local.zurvan.eval.scan.plist" in text
    assert "launchctl bootout" in text
    assert "-a" in text and "user:zurvaneval allow" in text  # ACL removal


@needs_clt
def test_t12_uninstall_purge_removes_identities_and_everything(t12_world):
    text = "\n".join(_t12_lines(t12_world["purge"]))
    for needle in ("/usr/bin/dscl . -delete /Users/zurvaneval", "/usr/bin/dscl . -delete /Users/zurvancandidate",
                   "/usr/bin/dscl . -delete /Users/zurvanauthor",
                   "/usr/bin/dscl . -delete /Groups/zurvancorpus", "/usr/bin/tmutil removeexclusion -p /Users/zurvaneval/keys",
                   "/usr/bin/tmutil removeexclusion -p /Users/zurvaneval/private",
                   "rm -rf /Users/zurvaneval", "rm -rf /Users/zurvancandidate"):
        assert needle in text, needle
    lines = _t12_lines(t12_world["purge"])
    assert lines.index(next(ln for ln in lines if "removeexclusion" in ln)) < lines.index(
        next(ln for ln in lines if ln.startswith("/bin/rm -rf /Users/zurvaneval")))


@needs_clt
def test_t12_uninstall_removes_pf_lines_and_cron_entries(t12_world):
    text = "\n".join(_t12_lines(t12_world["uninstall"]))
    assert "/etc/pf.anchors/zurvan-eval" in text
    assert "sed -i '' '/zurvan-eval/d' /etc/pf.conf" in text
    assert "/sbin/pfctl -f /etc/pf.conf" in text
    assert "sed -i '' '/^zurvancandidate$/d' /usr/lib/cron/cron.deny" in text
    assert "/usr/lib/cron/at.deny" in text


# ------------------------------------------------------------------ whole-plan invariants

@needs_clt
def test_t12_render_never_writes_outside_the_stage(t12_world):
    stage = Path(t12_world["install"])
    for p in stage.rglob("*"):
        assert str(p.resolve()).startswith(str(stage.resolve()))
    # every staged file mirrors an absolute path the plan installs (or is commands.txt)
    plan = "\n".join(_t12_lines(stage))
    for p in stage.rglob("*"):
        if p.is_file() and p.name != "commands.txt":
            final = "/" + str(p.relative_to(stage))
            assert final in plan, final


@needs_clt
def test_t12_commands_txt_header_says_it_is_a_plan(t12_world):
    head = (Path(t12_world["setup"]) / "commands.txt").read_text().splitlines()[:3]
    assert head[0].startswith("# ")
    assert any("render" in h.lower() or "plan" in h.lower() for h in head)


def test_t12_real_paths_in_render_match_sealpaths_properties():
    p = SealPaths.real()
    assert R.EVAL_HOME == str(p.eval_home)
    assert R.BROKER_DIR == str(p.broker_dir)
    assert R.SUDOERS_FILE == str(p.sudoers_file)
    assert R.PF_ANCHOR_FILE == str(p.pf_anchor)


def test_t12_render_module_runs_under_clt_python_39_isolated():
    if not Path(CLT_PY).exists():
        pytest.skip("CLT python absent")
    r = subprocess.run([CLT_PY, "-I", str(SEAL / "render.py"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "setup" in r.stdout and "install" in r.stdout


# ------------------------------------------------------------------ extra adversarial and syntax checks

@needs_clt
@pytest.mark.parametrize("key", ["setup", "install", "corpus", "drill", "uninstall", "purge"])
def test_t12_every_rendered_plan_is_valid_bash(t12_world, key):
    r = subprocess.run(["/bin/bash", "-n", str(Path(t12_world[key]) / "commands.txt")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_clt
@pytest.mark.parametrize("bad", ["root", "x\nroot ALL=(ALL) ALL", "a b", "%admin", "ALL", "zurvaneval", "x;id"])
def test_t12_cli_refuses_a_hostile_dev_user(tmp_path, bad):
    out = tmp_path / "o"
    r = _t12_sh("setup.sh", "--render", out, "--repo", REPO_PATH, "--dev-user", bad, "--dev-home", DEV_HOME)
    assert r.returncode != 0
    assert not (out / "commands.txt").exists()


@needs_clt
def test_t12_render_refuses_a_stage_that_symlinks_into_a_system_directory(tmp_path):
    link = tmp_path / "link"
    os.symlink("/etc", link)
    r = _t12_sh("setup.sh", "--render", link / "zz-t12-nested", "--repo", REPO_PATH, "--dev-user", DEV, "--dev-home", DEV_HOME)
    assert r.returncode != 0 and "refus" in r.stderr.lower()
    assert not Path("/etc/zz-t12-nested").exists()


@needs_clt
def test_t12_install_commands_do_nothing_before_every_render_time_check_has_passed(tmp_path):
    # all validation (sha, symlinks, entrypoints, tools) happens while rendering, before a single command is written
    repo, sha = _t12_make_repo(tmp_path, "r8")
    tools = _t12_fake_tools(tmp_path)
    out = tmp_path / "o"
    bad_tools = dict(tools)
    bad_tools["claude"] = tmp_path / "gone"
    r = _t12_sh("install.sh", *_t12_install_args(repo, sha, bad_tools, out))
    assert r.returncode != 0
    assert not (out / "commands.txt").exists()


def test_t12_validate_sha_is_strict_at_the_python_layer_too():
    for bad in ("A" * 40, "a" * 39, "a" * 41, "a" * 40 + "\n", "", None, 5, "g" * 40, "-" + "a" * 39):
        with pytest.raises(R.OperatorRefusal):
            R.validate_sha(bad)
    assert R.validate_sha("0123456789abcdef" * 2 + "01234567") == "0123456789abcdef" * 2 + "01234567"


@needs_clt
def test_t12_render_py_refuses_an_uppercase_sha_even_without_the_shell_guard(tmp_path):
    repo, sha = _t12_make_repo(tmp_path, "r9")
    tools = _t12_fake_tools(tmp_path)
    r = subprocess.run([CLT_PY, "-I", str(SEAL / "render.py"), "install", "--sha", sha.upper(), "--stage", str(tmp_path / "o"),
                        "--repo", str(repo), "--dev-user", DEV, "--age", str(tools["age"]), "--age-keygen", str(tools["age-keygen"]),
                        "--claude", str(tools["claude"]), "--hf-dir", str(tools["hf"])], capture_output=True, text=True)
    assert r.returncode == 2 and "40 lowercase hex" in r.stderr
    assert not (tmp_path / "o" / "commands.txt").exists()


@needs_clt
def test_t12_setup_acls_are_guarded_so_a_missing_dev_path_never_aborts(t12_world):
    # regression: setup runs under `bash -euo pipefail`; a bare `chmod` on a dev path that does not exist
    # yet (e.g. REPO/logs, or no *Zurvan* transcript dir) would abort the whole install. Every dev-side
    # ACL grant must therefore be guarded by an existence test (or the glob loop), never a bare chmod.
    lines = _t12_lines(t12_world["setup"])
    # dev-side grants only: the sealed-identity homes (zurvan{eval,candidate,author}) are dirs setup itself
    # creates, so their ACLs are unconditional by design; the fragile ones are the pre-existing dev paths.
    sealed = ("/Users/zurvaneval", "/Users/zurvancandidate", "/Users/zurvanauthor")
    dev_acl_lines = [ln.strip() for ln in lines
                     if "chmod" in ln and "+a" in ln and "allow" in ln
                     and not any(h in ln for h in sealed)]
    assert dev_acl_lines
    for ln in dev_acl_lines:
        assert ln.startswith("if [ -e ") or ln.startswith("for d in "), ln
    # the known-optional targets are present AND guarded
    text = "\n".join(lines)
    assert ("for d in %s/.claude/projects/*Zurvan*;" % DEV_HOME) in text
    assert ("if [ -e %s/logs ]; then" % REPO_PATH) in text
