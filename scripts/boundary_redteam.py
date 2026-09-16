"""Red-team the shipped Boundary against real protected material.

Every attack is run against the LIVE implementation with a store built from real
private source pages and the live 50-item instrument. Both outcomes are recorded.
"""
import shutil, subprocess, sys, tempfile, pathlib, sqlite3, json, re
sys.path.insert(0, '.')
import scripts.boundary as boundary
from scripts.boundary import store
from scripts.instruments import gold_path

root = pathlib.Path(tempfile.mkdtemp()) / "b"
(root/"sources").mkdir(parents=True); (root/"instruments").mkdir(parents=True)
src = sorted(pathlib.Path("wiki/sources").glob("*.md"))[:60]
for p in src:
    shutil.copyfile(p, root/"sources"/p.name)
shutil.copyfile(gold_path(), root/"instruments"/"search_gold.jsonl")
boundary.build(root)
PROT = src[3].read_bytes()
GOLD = [json.loads(l)["query"] for l in open(gold_path(), encoding="utf-8") if l.strip()]

results = []
def check(name, passed, detail=""):
    results.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}")

print("\n== A. does it actually block real protected material? ==")
v = boundary.scan_blob(root, "leak.md", PROT[:500], sink=["PUBLIC_ARTIFACT"])
check("500 B of a real protected page blocks", v.blocked)
v2 = boundary.scan(root, f"we should note that {GOLD[7]} is unresolved", sink="RETRIEVAL_CORPUS")
check("a real gold item blocks into RETRIEVAL_CORPUS", v2.blocked)
v3 = boundary.scan(root, "entirely ordinary prose about nothing in particular", sink="PUBLIC_ARTIFACT")
check("ordinary prose passes", not v3.blocked)

print("\n== B. does any OUTPUT leak protected content? ==")
blob = repr(v) + repr(v.matches) + str(v) + "".join(repr(m) for m in v.matches)
leaked = [w for w in PROT[:500].decode("utf-8","ignore").split() if len(w) > 9 and w in blob]
check("Verdict/Match repr carries no protected token", not leaked, f"leaked: {leaked[:3]}")
blob2 = repr(v2) + "".join(repr(m) for m in v2.matches)
check("Verdict carries no gold text", not any(g.lower() in blob2.lower() for g in GOLD))
out = subprocess.run([sys.executable, "scripts/boundary_guard.py"], capture_output=True, text=True,
                     env={**__import__("os").environ, "ZURVAN_BOUNDARY_ROOT": str(root), "PYTHONPATH": "."})
check("guard output carries no gold text", not any(g.lower() in out.stdout.lower() for g in GOLD))

print("\n== C. integrity attacks (each must raise, never return clean) ==")
def attack(name, mutate):
    r2 = pathlib.Path(tempfile.mkdtemp())/"b"
    shutil.copytree(root, r2)
    try:
        mutate(r2)
        v = boundary.scan_blob(r2, "x.md", PROT[:500], sink=["PUBLIC_ARTIFACT"])
        check(name, False, f"returned blocked={v.blocked} instead of raising")
    except (store.StaleStateError, store.StateIntegrityError) as e:
        check(name, True, type(e).__name__)
    finally:
        shutil.rmtree(r2.parent, ignore_errors=True)

def _sql(r, q):
    c = sqlite3.connect(r/"boundary.sqlite"); c.execute(q); c.commit(); c.close()

attack("delete all fingerprints",       lambda r: _sql(r, "DELETE FROM fingerprints"))
attack("delete one fingerprint",        lambda r: _sql(r, "DELETE FROM fingerprints WHERE rowid=1"))
attack("tamper origins counts",         lambda r: _sql(r, "UPDATE origins SET n_fingerprints=n_fingerprints+1"))
attack("drop the committed total",      lambda r: _sql(r, "DELETE FROM meta WHERE key='total_fingerprints'"))
attack("drop the manifest digest",      lambda r: _sql(r, "DELETE FROM meta WHERE key='manifest_digest'"))
attack("downgrade detector version",    lambda r: _sql(r, "UPDATE meta SET value='old' WHERE key='d1_version'"))
attack("corrupt the database",          lambda r: (r/"boundary.sqlite").write_bytes(b"\x00corrupt"))
attack("tamper a content blob",         lambda r: next(p for p in (r/"blobs").rglob("*") if p.is_file()).write_bytes(b"x"))
attack("edit a protected source",       lambda r: (r/"sources"/src[3].name).write_bytes(PROT + b"\nEDITED"))
attack("add a new protected source",    lambda r: (r/"sources"/"new.md").write_bytes(b"newly protected material here"))
attack("add an instrument item",        lambda r: (r/"instruments"/"extra.jsonl").write_text('{"query":"a brand new evaluation item"}\n'))

print("\n== D. policy cannot fail open ==")
from scripts.boundary import policy
for label, fn in (("typo source class", lambda: policy.is_blocked("PRVATE", ["PUBLIC_ARTIFACT"])),
                  ("empty sink set",    lambda: policy.is_blocked("PRIVATE", [])),
                  ("unknown sink",      lambda: policy.is_blocked("PRIVATE", ["SOMEWHERE"]))):
    try:
        fn(); check(label, False, "returned instead of raising")
    except policy.PolicyError:
        check(label, True)

print("\n== F. degenerate content is not protected content ==")
# Added 2026-09-16 after the first measurement against real material: a shared
# horizontal rule made `scripts/check.sh` unreleasable, and one such file
# produced 63,975 candidate anchors. Rows 18-20 of the gauntlet pin the rule on
# synthetic input; these run it against the real store.
RULE = ("\u2500" * 90).encode("utf-8")
check("a shared 270 B rule does not block",
      not boundary.scan_blob(root, "x.md", RULE, sink=["PUBLIC_ARTIFACT"]).blocked)
check("a rule plus three real bytes does not block",
      not boundary.scan_blob(root, "x.md", RULE + b"\n# ", sink=["PUBLIC_ARTIFACT"]).blocked)
check("padding cannot conceal real protected prose",
      boundary.scan_blob(root, "x.md", RULE + PROT[:500] + RULE,
                         sink=["PUBLIC_ARTIFACT"]).blocked)

# REPORTED, NOT GATED. Scanning real tracked files against a store built from
# real wiki/sources pages flags files that quote the repository's own ingested
# documents — a provenance inversion, not a coincidence: ingest.py writes a
# public file into wiki/sources/ and it becomes PRIVATE by location alone.
# Until ingestion records a source class (see the spec's open item), this number
# measures ingestion overlap, so gating on it would gate on the wrong thing.
# It is printed every run so the number cannot drift unnoticed.
import time as _t
_all = [f for f in subprocess.run(["git","ls-files"],capture_output=True,text=True)
        .stdout.split() if f.endswith((".py",".sh",".md"))]
# Spread across the whole tracked list rather than the first N alphabetically,
# so the sample is not all of scripts/.
_sample = _all[::max(1, len(_all)//25)][:25]
_fp, _lat = 0, []
for f in _sample:
    try: _b = pathlib.Path(f).read_bytes()
    except OSError: continue
    _s0 = _t.time()
    _fp += boundary.scan_blob(root, f, _b, sink=["PUBLIC_ARTIFACT"]).blocked
    _lat.append((_t.time() - _s0) * 1000)
_lat.sort()
print(f"  [INFO] tracked-file overlap {_fp}/{len(_sample)}  "
      f"(ingestion round-trips; not gated — see spec open item)")
print(f"  [INFO] scan latency p50 {_lat[len(_lat)//2]:.0f} ms  "
      f"p95 {_lat[int(len(_lat)*0.95)]:.0f} ms  (budget p95 <= 400)")

print("\n== E. nothing protected reaches git ==")
tracked = subprocess.run(["git","ls-files"],capture_output=True,text=True).stdout.split()
check("instrument cleartext untracked", "eval/search_gold.jsonl" not in tracked)
check("boundary store untracked", not any("zurvan/boundary" in f for f in tracked))
names=[l.strip() for l in open(".private-projects",encoding="utf-8") if l.strip() and not l.startswith("#")]
bad=[]
for f in tracked:
    try: t = pathlib.Path(f).read_text(encoding="utf-8", errors="ignore")
    except OSError: continue
    if any(re.search(re.escape(n), t, re.I) for n in names): bad.append(f)
check("no private project name in any tracked file", not bad, f"{bad[:3]}")
# data/traces/ are FROZEN research artifacts (standing invariant: must not be
# mutated). One June provenance trace contains gold item #1 verbatim, which
# predates this policy and cannot be edited without breaking a frozen artifact.
# Named explicitly so the exception is auditable rather than silent.
FROZEN_EXCEPTIONS = {"data/traces/"}
gbad=[f for f in tracked
      if f.endswith((".py",".md",".json",".sh"))
      and not any(f.startswith(x) for x in FROZEN_EXCEPTIONS)
      and any(g.lower() in pathlib.Path(f).read_text(encoding="utf-8",errors="ignore").lower()
              for g in GOLD)]
check("no gold item verbatim in any tracked file (frozen traces excepted)", not gbad, f"{gbad[:3]}")

shutil.rmtree(root.parent, ignore_errors=True)
print(f"\n{'='*60}\nRED TEAM: {sum(1 for _,p,_ in results if p)}/{len(results)} passed")
for n,p,d in results:
    if not p: print(f"  FAILED: {n}  {d}")
sys.exit(0 if all(p for _,p,_ in results) else 1)
