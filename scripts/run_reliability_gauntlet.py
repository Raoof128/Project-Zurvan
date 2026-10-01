import os
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _throwaway_store(sources):
    """A private copy of just enough store for ingest -> extract -> audit.

    The gauntlet used to run against the LIVE store: every check.sh run
    ingested these five sources into the corpus every retrieval baseline is
    measured on (filed 2026-09-28, "5 revisions per gate"). The sources are
    COPIED (raw/ is never modified) into a temporary store, and every step runs
    there with ZURVAN_ROOT pointing at it.
    """
    store = Path(tempfile.mkdtemp(prefix="zurvan-gauntlet-"))
    for d in ("wiki/sources", "wiki/claims", "wiki/summaries", "data"):
        (store / d).mkdir(parents=True, exist_ok=True)
    for src in sources:
        target = store / os.path.relpath(os.path.abspath(src), os.getcwd())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return store


def run_gauntlet(sources, keep_store=False):
    results = []
    store = _throwaway_store(sources)
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO)}

    def _run(args):
        return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                              cwd=str(store), env=env)

    try:
        _run_all(sources, results, _run)
    finally:
        if keep_store:
            print(f"Gauntlet store kept at {store}")
        else:
            shutil.rmtree(store, ignore_errors=True)
    return results


def _run_all(sources, results, _run):
    for source in sources:
        source = os.path.relpath(os.path.abspath(source), os.getcwd())
        print(f"\n{'='*50}\nTesting: {source}\n{'='*50}")
        
        # 1. Ingest
        print("[1] Ingesting...")
        ingest_res = _run([str(REPO / "scripts" / "ingest.py"), source])
        if ingest_res.returncode != 0:
            print(f"Ingestion failed for {source}:\n{ingest_res.stderr}")
            results.append({"source": source, "status": "Failed Ingestion"})
            continue
            
        # 2. Extract
        basename = os.path.basename(source)
        source_md_name = f"{basename}.md"
        wiki_source_path = os.path.join("wiki", "sources", source_md_name)
        
        print(f"[2] Extracting from {wiki_source_path}...")
        extract_res = _run([str(REPO / "scripts" / "extract.py"), "--source", wiki_source_path])
        if extract_res.returncode != 0:
            print(f"Extraction failed for {source}:\n{extract_res.stderr}")
            results.append({"source": source, "status": "Failed Extraction/Validation"})
            continue
            
        # 3. Audit
        print("[3] Auditing Wiki...")
        audit_res = _run([str(REPO / "scripts" / "audit_wiki.py")])
        if "Found" in audit_res.stdout and "issues:" in audit_res.stdout:
            # We don't fail the gauntlet script entirely on audit issues, but we log them
            print(f"Audit found issues after processing {source}:\n{audit_res.stdout}")
            results.append({"source": source, "status": "Audit Issues Found"})
        else:
            print(f"Audit passed cleanly for {source}.")
            results.append({"source": source, "status": "Passed"})
            
    # Write summary
    print(f"\n{'='*50}\nGauntlet Summary\n{'='*50}")
    for res in results:
        print(f"{res['source']}: {res['status']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Extraction Reliability Gauntlet")
    parser.add_argument("sources", nargs="+", help="Paths to raw sources to test")
    parser.add_argument("--keep-store", action="store_true",
                        help="Keep the throwaway store for inspection")
    args = parser.parse_args()
    
    # Ensure sources are in raw/
    for src in args.sources:
        if not os.path.abspath(src).startswith(os.path.abspath("raw")):
            print(f"Error: {src} must be located in the raw/ directory to protect data.")
            exit(1)
            
    run_gauntlet(args.sources, keep_store=args.keep_store)
