"""Z1b Task 17f: every scripts/eval_seal/*.py compiles and imports under the CLT Python 3.9 with -I.

That interpreter (root-owned, 3.9.6, isolated mode) is what runs the sealed side. The repo python is
3.12, so a `match` statement, a `X | Y` annotation evaluated at runtime, a 3.10-only stdlib call or a
third-party import would pass every other test and fail only on the real broker. `-I` ignores PYTHONPATH,
the user site and the cwd, so the import path is added explicitly.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLT_PY39 = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"

if not Path(CLT_PY39).exists():
    pytest.skip("Z1b: CLT python 3.9 absent (%s); the sealed-side interpreter is UNVERIFIED on this machine"
                % CLT_PY39, allow_module_level=True)

_T17_CHILD = r'''
import ast, glob, json, os, sys
repo = sys.argv[1]
sys.path.insert(0, repo)
out = {"version": list(sys.version_info[:3]), "isolated": bool(sys.flags.isolated), "modules": {}}
for path in sorted(glob.glob(os.path.join(repo, "scripts", "eval_seal", "*.py"))):
    name = os.path.basename(path)[:-3]
    mod = "scripts.eval_seal" if name == "__init__" else "scripts.eval_seal." + name
    res = {"compiled": False, "imported": False, "future": False}
    try:
        src = open(path, encoding="utf-8").read()
        compile(src, path, "exec")
        tree = ast.parse(src, path, feature_version=(3, 9))
        res["compiled"] = True
        body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]
        res["future"] = bool(body) and isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__" \
            and any(a.name == "annotations" for a in body[0].names)
        __import__(mod)
        res["imported"] = True
    except BaseException as exc:  # report the class only
        res["error"] = type(exc).__name__
    out["modules"][name] = res
print(json.dumps(out))
'''


def _t17_probe():
    cp = subprocess.run([CLT_PY39, "-I", "-c", _T17_CHILD, str(REPO)], capture_output=True, text=True, timeout=300,
                        env={"PATH": "/usr/bin:/bin"})
    assert cp.returncode == 0, cp.stderr[-800:]
    return json.loads(cp.stdout.strip().splitlines()[-1])


def test_t17_the_probe_really_runs_python_3_9_in_isolated_mode():
    info = _t17_probe()
    assert info["version"][:2] == [3, 9] and info["isolated"] is True


def test_t17_every_eval_seal_module_compiles_and_imports_under_python_39():
    info = _t17_probe()
    expected = sorted(p.stem for p in (REPO / "scripts" / "eval_seal").glob("*.py"))
    assert sorted(info["modules"]) == expected and len(expected) >= 10
    failed = {n: r for n, r in info["modules"].items() if not (r["compiled"] and r["imported"])}
    assert failed == {}, failed


def test_t17_every_eval_seal_module_starts_with_future_annotations():
    info = _t17_probe()
    missing = [n for n, r in info["modules"].items() if n != "__init__" and not r["future"]]
    assert missing == [], missing


def test_t17_a_python_310_only_construct_would_be_caught_by_the_probe(tmp_path):
    """The probe is only meaningful if 3.9 really rejects what 3.12 accepts."""
    bad = tmp_path / "bad.py"
    bad.write_text("def f(x):\n    match x:\n        case 1:\n            return 1\n")
    cp = subprocess.run([CLT_PY39, "-I", "-c", "import sys; compile(open(sys.argv[1]).read(), 'bad', 'exec')",
                         str(bad)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert cp.returncode != 0 and "SyntaxError" in cp.stderr
    compile(bad.read_text(), "bad", "exec")  # the repo interpreter accepts it
    assert sys.version_info >= (3, 10)
